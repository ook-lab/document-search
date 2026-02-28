"""
パイプライン管理モジュール

PipelineManager クラスを提供
全ステージ（A→B→D→E→F→G→J→K）の実行管理

役割：
- パイプライン実行のオーケストレーション
- DB操作（ステータス、メタデータ、進捗）
- リース管理（dequeue, ack, nack）
- リソース管理（並列数、メモリ）

【リース方式キュー】
- dequeue_document RPC で原子化されたデキュー
- ack_document / nack_document で owner 条件付き更新
- 100ワーカーでも重複ゼロを保証
"""
import asyncio
import mimetypes
import os
import re
import socket
import threading
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from loguru import logger
from shared.common.database.client import DatabaseClient
from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.logging import TaskLogger  # Per-Task Logging
from shared.ai.llm_client.llm_client import LLMClient

# 新しいステージベースのアーキテクチャ（A→B→D→E→F→G→J→K）
from shared.pipeline.stage_a import A3EntryPoint
from shared.pipeline.stage_b import B1Controller
from shared.pipeline.stage_d import D1Controller
from shared.pipeline.stage_e import E1Controller
from shared.pipeline.stage_f import F1Controller
from shared.pipeline.stage_g import G1Controller
from shared.pipeline.stage_j_chunking import StageJChunking
from shared.pipeline.stage_k_embedding import StageKEmbedding
from shared.pipeline.config_loader import ConfigLoader

from shared.processing.state_manager import StateManager, get_state_manager
from shared.processing.resource_manager import AdaptiveResourceManager, get_cgroup_memory, get_cgroup_cpu
from shared.processing.execution_policy import ExecutionPolicy, get_execution_policy


def generate_batch_summary(
    log_dir: Path,
    start_time: datetime,
    end_time: datetime,
    success_results: List[Dict[str, Any]],
    failed_results: List[Dict[str, Any]],
    workspace: str,
    limit: int
) -> Path:
    """
    バッチ処理のサマリーファイルを生成（AI解析用）

    Args:
        log_dir: ログディレクトリ
        start_time: 処理開始時刻
        end_time: 処理終了時刻
        success_results: 成功したドキュメントのリスト
        failed_results: 失敗したドキュメントのリスト [{id, title, error}, ...]
        workspace: 処理対象ワークスペース
        limit: 処理上限

    Returns:
        サマリーファイルのパス
    """
    summary_dir = log_dir / 'summary'
    summary_dir.mkdir(parents=True, exist_ok=True)

    timestamp = start_time.strftime('%Y%m%d_%H%M%S')
    summary_path = summary_dir / f'summary_{timestamp}.txt'

    duration = (end_time - start_time).total_seconds()
    total_count = len(success_results) + len(failed_results)

    lines = [
        "=" * 60,
        "ドキュメント処理サマリー",
        "=" * 60,
        "",
        f"処理日時: {start_time.strftime('%Y-%m-%d %H:%M:%S')} - {end_time.strftime('%H:%M:%S')}",
        f"処理時間: {duration:.1f}秒",
        f"ワークスペース: {workspace}",
        f"処理上限: {limit}件",
        "",
        "-" * 60,
        "結果サマリー",
        "-" * 60,
        f"処理件数: {total_count}件",
        f"成功: {len(success_results)}件",
        f"失敗: {len(failed_results)}件",
        f"成功率: {(len(success_results) / total_count * 100) if total_count > 0 else 0:.1f}%",
        "",
    ]

    if failed_results:
        lines.extend([
            "-" * 60,
            "失敗タスク一覧",
            "-" * 60,
        ])
        for i, item in enumerate(failed_results, 1):
            lines.append(f"{i}. {item.get('title', '(不明)')}")
            lines.append(f"   ID: {item.get('id', '(不明)')}")
            lines.append(f"   エラー: {item.get('error', '(不明)')}")
            lines.append("")

    if success_results:
        lines.extend([
            "-" * 60,
            f"成功タスク一覧 ({len(success_results)}件)",
            "-" * 60,
        ])
        for i, item in enumerate(success_results, 1):
            lines.append(f"{i}. {item.get('title', '(不明)')}")

    lines.extend([
        "",
        "=" * 60,
        f"サマリー生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
    ])

    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    logger.info(f"サマリーファイル生成: {summary_path}")
    return summary_path


class PipelineManager:
    """
    パイプライン管理クラス

    全ステージ（A→B→D→E→F→G→J→K）の実行を統括
    StateManagerを通じて状態を一元管理
    AdaptiveResourceManagerで並列数を動的調整
    """

    VIDEO_EXTENSIONS = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.mpeg', '.mpg']

    # リース設定
    DEFAULT_LEASE_SECONDS = 900  # 15分
    HEARTBEAT_INTERVAL = 60  # 60秒ごとにリース延長

    def __init__(self, use_service_role: bool = False, db: DatabaseClient = None):
        """
        PipelineManager の初期化

        Args:
            use_service_role: Trueの場合、Service Role Keyを使用（RLSをバイパス）
                              Worker実行時は必ずTrueを指定すること
            db: 外部から注入する DatabaseClient（指定された場合は use_service_role は無視）
        """
        # 外部注入されたDBがあればそれを使用、なければ新規作成
        if db is not None:
            self.db = db
        else:
            self.db = DatabaseClient(use_service_role=use_service_role)

        # パイプライン初期化: A→B→D→E→F→G→H→J→K
        import os
        gemini_key = os.environ.get('GOOGLE_AI_API_KEY')

        self.llm_client = LLMClient()
        self.config = ConfigLoader()

        # Stage A-G: 新しいアーキテクチャ
        self.stage_a = A3EntryPoint()
        self.stage_b = B1Controller()
        self.stage_d = D1Controller()
        self.stage_e = E1Controller(gemini_api_key=gemini_key)
        self.stage_f = F1Controller(gemini_api_key=gemini_key)
        self.stage_g = G1Controller(api_key=gemini_key)

        # Stage J-K: チャンキング＆埋め込み
        self.stage_j = StageJChunking()
        self.stage_k = StageKEmbedding(self.llm_client, self.db)

        logger.info("✅ パイプライン初期化完了: A→B→D→E→F→G→J→K")

        self.drive = GoogleDriveConnector()
        self.temp_dir = Path("./temp")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # StateManagerを取得（SSOT）
        self.state_manager = get_state_manager()

        # ExecutionPolicy（実行可否判断のSSOT）
        self.execution_policy = get_execution_policy()

        # リソースマネージャー（インスタンス毎に作成）
        self.resource_manager = None

        # loguruハンドラーID（run_batch内で登録・削除）
        self._log_handler_id = None

        # リース方式: ワーカー識別子（重複処理防止）
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
        logger.info(f"[Lease] Worker owner: {self.owner}")

    def get_pending_documents(self, workspace: str = 'all', limit: int = 100) -> List[Dict[str, Any]]:
        """
        processing_status='queued' のドキュメントを取得（バッチ用・dry-run用）

        【注意】実際の処理では dequeue_document() を使用すること
        このメソッドは dry-run や統計表示用

        【変更履歴】
        - 2026-01-19: pending → queued に変更（新キューシステム対応）
        """
        query = self.db.client.table('Rawdata_FILE_AND_MAIL').select('*').eq('processing_status', 'queued')

        if workspace != 'all':
            query = query.eq('workspace', workspace)

        result = query.limit(limit).execute()
        return result.data if result.data else []

    def dequeue_document(self, workspace: str = 'all') -> Optional[Dict[str, Any]]:
        """
        【リース方式】原子化されたデキュー（RPC経由）

        1件だけ取得 + processing + リース設定 を1回の操作で行う
        100ワーカーが同時に呼んでも、同じドキュメントは1つのワーカーにしか渡らない

        Args:
            workspace: 対象ワークスペース（'all' で全対象）

        Returns:
            取得したドキュメント（なければ None）
        """
        try:
            result = self.db.client.rpc('dequeue_document', {
                'p_workspace': workspace,
                'p_lease_seconds': self.DEFAULT_LEASE_SECONDS,
                'p_owner': self.owner
            }).execute()

            if result.data and len(result.data) > 0:
                doc = result.data[0]

                # file_url補完: dequeue RPCが古いバージョンの場合の対策
                if 'file_url' not in doc or doc.get('file_url') is None:
                    補完_result = self.db.client.table('Rawdata_FILE_AND_MAIL').select('file_url').eq('id', doc['id']).execute()
                    if 補完_result.data and len(補完_result.data) > 0:
                        doc['file_url'] = 補完_result.data[0].get('file_url')
                        logger.info(f"[Lease] file_url補完完了: {doc.get('file_url')}")

                logger.info(f"[Lease] Dequeued: id={doc['id']}, owner={self.owner}, lease_until={doc.get('lease_until')}")
                return doc
            else:
                return None

        except Exception as e:
            logger.error(f"[Lease] dequeue_document RPC error: {e}")
            return None

    def ack_document(self, doc_id: str) -> bool:
        """
        【リース方式】処理完了を通知（owner条件付き）

        Args:
            doc_id: ドキュメントID

        Returns:
            更新成功なら True
        """
        try:
            result = self.db.client.rpc('ack_document', {
                'p_id': doc_id,
                'p_owner': self.owner
            }).execute()

            # RPC は更新行数を返す（Supabaseはリストで返す: [0] or [1]）
            if isinstance(result.data, list) and len(result.data) > 0:
                row_count = result.data[0]
            elif isinstance(result.data, int):
                row_count = result.data
            else:
                row_count = 0

            if row_count == 0:
                logger.warning(f"[Lease] ack_document: rowcount=0 (owner mismatch or already released) id={doc_id}")
                return False

            logger.info(f"[Lease] Acked: id={doc_id}, owner={self.owner}")
            return True

        except Exception as e:
            logger.error(f"[Lease] ack_document RPC error: {e}")
            return False

    def nack_document(self, doc_id: str, error_message: str = None, retry: bool = True) -> bool:
        """
        【リース方式】処理失敗を通知（owner条件付き）

        Args:
            doc_id: ドキュメントID
            error_message: エラーメッセージ
            retry: True なら pending に戻す、False なら failed に

        Returns:
            更新成功なら True
        """
        try:
            result = self.db.client.rpc('nack_document', {
                'p_id': doc_id,
                'p_owner': self.owner,
                'p_error_message': error_message,
                'p_retry': retry
            }).execute()

            # RPC は更新行数を返す（Supabaseはリストで返す: [0] or [1]）
            if isinstance(result.data, list) and len(result.data) > 0:
                row_count = result.data[0]
            elif isinstance(result.data, int):
                row_count = result.data
            else:
                row_count = 0

            if row_count == 0:
                logger.warning(f"[Lease] nack_document: rowcount=0 (owner mismatch or already released) id={doc_id}")
                return False

            logger.info(f"[Lease] Nacked: id={doc_id}, retry={retry}, owner={self.owner}")
            return True

        except Exception as e:
            logger.error(f"[Lease] nack_document RPC error: {e}")
            return False

    def renew_lease(self, doc_id: str) -> bool:
        """
        【リース方式】リース延長（ロングジョブ用）

        Args:
            doc_id: ドキュメントID

        Returns:
            延長成功なら True
        """
        try:
            result = self.db.client.rpc('renew_lease', {
                'p_id': doc_id,
                'p_owner': self.owner,
                'p_lease_seconds': self.DEFAULT_LEASE_SECONDS
            }).execute()

            # RPC は更新行数を返す（Supabaseはリストで返す: [0] or [1]）
            if isinstance(result.data, list) and len(result.data) > 0:
                row_count = result.data[0]
            elif isinstance(result.data, int):
                row_count = result.data
            else:
                row_count = 0

            if row_count == 0:
                logger.warning(f"[Lease] renew_lease: rowcount=0 (owner mismatch) id={doc_id}")
                return False

            logger.debug(f"[Lease] Renewed: id={doc_id}")
            return True

        except Exception as e:
            logger.error(f"[Lease] renew_lease RPC error: {e}")
            return False

    # ============================================
    # キュー操作（queued ステータス）
    # ============================================

    def enqueue_documents(
        self,
        workspace: str = 'all',
        limit: int = 100,
        doc_ids: list = None
    ) -> Dict[str, Any]:
        """
        キューに追加: pending → queued

        Args:
            workspace: 対象ワークスペース（'all'で全て）
            limit: 追加上限
            doc_ids: 指定IDリスト（指定時はworkspace/limitを無視）

        Returns:
            {'enqueued_count': int, 'doc_ids': list}
        """
        try:
            result = self.db.client.rpc('enqueue_documents', {
                'p_workspace': workspace,
                'p_limit': limit,
                'p_doc_ids': doc_ids
            }).execute()

            if result.data:
                data = result.data[0] if isinstance(result.data, list) else result.data
                count = data.get('enqueued_count', 0)
                ids = data.get('doc_ids', [])
                logger.info(f"[Queue] Enqueued {count} documents")
                return {'enqueued_count': count, 'doc_ids': ids or []}
            return {'enqueued_count': 0, 'doc_ids': []}

        except Exception as e:
            logger.error(f"[Queue] enqueue_documents error: {e}")
            return {'enqueued_count': 0, 'doc_ids': [], 'error': str(e)}

    def clear_queue(self, workspace: str = 'all') -> int:
        """
        停止: queued → pending に戻す

        Args:
            workspace: 対象ワークスペース（'all'で全て）

        Returns:
            クリアした件数
        """
        try:
            result = self.db.client.rpc('clear_queue', {
                'p_workspace': workspace
            }).execute()

            if result.data:
                data = result.data[0] if isinstance(result.data, list) else result.data
                count = data.get('cleared_count', 0)
                logger.info(f"[Queue] Cleared {count} documents from queue")
                return count
            return 0

        except Exception as e:
            logger.error(f"[Queue] clear_queue error: {e}")
            return 0

    def get_queue_status(self, workspace: str = 'all') -> Dict[str, int]:
        """
        キューの状態を取得

        Returns:
            {'pending': int, 'queued': int, 'processing': int, ...}
        """
        try:
            result = self.db.client.rpc('get_queue_status', {
                'p_workspace': workspace
            }).execute()

            if result.data:
                data = result.data[0] if isinstance(result.data, list) else result.data
                return {
                    'pending': data.get('pending_count', 0),
                    'queued': data.get('queued_count', 0),
                    'processing': data.get('processing_count', 0),
                    'completed': data.get('completed_count', 0),
                    'failed': data.get('failed_count', 0)
                }
            return {}

        except Exception as e:
            logger.error(f"[Queue] get_queue_status error: {e}")
            return {}

    def get_document_by_id(self, doc_id: str) -> Dict[str, Any] | None:
        """IDでドキュメントを取得"""
        # file_url を含む必要なカラムを明示的に列挙（* は file_url を含まない問題がある）
        result = self.db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, file_name, title, workspace, doc_type, processing_status, '
            'source_url, file_url, screenshot_url, '
            'display_subject, display_post_text, '
            'display_sender, display_sender_email, display_sent_at, '
            'owner_id, lease_owner, lease_until'
        ).eq('id', doc_id).execute()
        return result.data[0] if result.data else None

    async def process_single_document(self, doc_id: str, preserve_workspace: bool = True) -> bool:
        """
        単一ドキュメントをIDで処理（CLI用）

        Args:
            doc_id: ドキュメントID
            preserve_workspace: workspaceを保持するか

        Returns:
            処理成功ならTrue
        """
        # ExecutionPolicy で実行可否をチェック（SSOT）
        policy_result = self.execution_policy.can_execute(doc_id=doc_id)
        if not policy_result.allowed:
            logger.warning(f"実行拒否: {policy_result.deny_code} - {policy_result.deny_reason}")
            return False

        # file_url を確実に取得するため、ここで直接クエリ
        result = self.db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, file_name, title, workspace, doc_type, processing_status, '
            'source_url, file_url, screenshot_url, '
            'display_subject, display_post_text, '
            'display_sender, display_sender_email, display_sent_at, '
            'owner_id, lease_owner, lease_until'
        ).eq('id', doc_id).execute()

        doc = result.data[0] if result.data else None
        if not doc:
            logger.error(f"ドキュメントが見つかりません: {doc_id}")
            return False

        title = doc.get('title', doc.get('file_name', '(不明)'))
        logger.info(f"単一ドキュメント処理開始: {title}")
        logger.info(f"  ID: {doc_id}")
        logger.info(f"  Workspace: {doc.get('workspace', '(不明)')}")
        logger.info(f"  Status: {doc.get('processing_status', '(不明)')}")

        # StateManagerを初期化（単一処理用）
        if not self.state_manager.start_processing(1):
            logger.warning("処理開始に失敗しました（既に処理中か、ロック取得失敗）")
            return False

        try:
            result = await self.process_document(doc, preserve_workspace)
            return result
        finally:
            self.state_manager.finish_processing()

    def get_queue_stats(self, workspace: str = 'all') -> Dict[str, int]:
        """統計情報を取得"""
        try:
            query = self.db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status, workspace')

            if workspace != 'all':
                query = query.eq('workspace', workspace)

            # Supabaseのデフォルト制限1000件を回避するため、明示的に上限を設定
            response = query.limit(100000).execute()

            stats = {
                'pending': 0,
                'processing': 0,
                'completed': 0,
                'failed': 0,
                'null': 0
            }

            for doc in response.data:
                status = doc.get('processing_status')
                if status is None:
                    stats['null'] += 1
                else:
                    stats[status] = stats.get(status, 0) + 1

            stats['total'] = len(response.data)

            processed = stats['completed'] + stats['failed']
            if processed > 0:
                stats['success_rate'] = round(stats['completed'] / processed * 100, 1)
            else:
                stats['success_rate'] = 0.0

            return stats

        except Exception as e:
            logger.error(f"統計取得エラー: {e}")
            return {}

    def _mark_as_processing(self, document_id: str):
        """処理中にマーク"""
        try:
            self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'processing',
                'processing_progress': 0.0
            }).eq('id', document_id).execute()
        except Exception as e:
            logger.error(f"処理中マークエラー: {e}")

    # ------------------------------------------------------------------
    # Stage D / E マルチページ結果マージ
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_d_results(d_results: list) -> dict:
        """
        複数ページの D 結果を E1Controller が読める単一 dict にまとめる。

        - tables: 全ページ分を結合（各 table に image_path あり → E がそのまま処理）
        - non_table_image_paths: ページごとの背景画像パスリスト
          （E1Controller はこのリストをループして非表領域を処理する）
        - non_table_image_path: 後方互換のため先頭ページ分を残す
        """
        if not d_results:
            return {}
        if len(d_results) == 1:
            d = dict(d_results[0])
            # 単一ページでもリスト形式を追加
            if d.get('non_table_image_path'):
                d['non_table_image_paths'] = [d['non_table_image_path']]
            return d

        base = dict(d_results[0])
        all_tables = []
        non_table_image_paths = []

        for page_idx, dr in enumerate(d_results):
            page_index = dr.get('page_index', page_idx)
            for table in (dr.get('tables', []) or []):
                t = dict(table)
                # ページ番号をtable_idに付与して一意化（例: D1 → P0_D1）
                t['table_id'] = f"P{page_index}_{t.get('table_id', 'D1')}"
                all_tables.append(t)
            img = dr.get('non_table_image_path')
            if img:
                non_table_image_paths.append(img)

        base['tables'] = all_tables
        base['non_table_image_paths'] = non_table_image_paths
        base['page_count'] = len(d_results)
        return base

    def _update_document_progress(self, document_id: str, progress: float, log_message: str = None):
        """ドキュメントの進捗を更新"""
        try:
            self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_progress': progress
            }).eq('id', document_id).execute()

            if log_message:
                logger.debug(f"進捗更新: {log_message} ({progress*100:.0f}%)")

            # StateManagerの進捗も更新
            if log_message:
                self.state_manager.update_progress(stage=log_message, stage_progress=progress)
        except Exception as e:
            logger.error(f"進捗更新エラー: {e}")

    def _mark_as_completed(self, document_id: str):
        """
        完了にマーク

        【リース方式】ack_document RPC を使用（owner条件付き）
        """
        # リース方式: RPC で owner 条件付き更新
        if not self.ack_document(document_id):
            # フォールバック: 直接更新（単一ドキュメント処理など、dequeue を使わない場合）
            try:
                self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                    'processing_status': 'completed',
                    'processing_progress': 1.0,
                    'lease_owner': None,
                    'lease_until': None
                }).eq('id', document_id).execute()
                logger.debug(f"[Lease] Fallback completed: {document_id}")
            except Exception as e:
                logger.error(f"完了マークエラー: {e}")

    def _mark_as_failed(self, document_id: str, error_message: str = ""):
        """
        エラーにマーク

        【リース方式】nack_document RPC を使用（owner条件付き）
        """
        # リース方式: RPC で owner 条件付き更新（retry=False で failed に）
        if not self.nack_document(document_id, error_message, retry=False):
            # フォールバック: 直接更新（単一ドキュメント処理など、dequeue を使わない場合）
            try:
                update_data = {
                    'processing_status': 'failed',
                    'processing_progress': 0.0,
                    'lease_owner': None,
                    'lease_until': None
                }

                if error_message:
                    doc_result = self.db.client.table('Rawdata_FILE_AND_MAIL').select('metadata').eq('id', document_id).execute()
                    if doc_result.data and len(doc_result.data) > 0:
                        metadata = doc_result.data[0].get('metadata', {}) or {}
                    else:
                        metadata = {}

                    metadata['last_error'] = error_message
                    metadata['last_error_time'] = datetime.now().isoformat()
                    update_data['metadata'] = metadata

                self.db.client.table('Rawdata_FILE_AND_MAIL').update(update_data).eq('id', document_id).execute()
                logger.debug(f"[Lease] Fallback failed: {document_id}")
            except Exception as e:
                logger.error(f"失敗マークエラー: {e}")

    def _get_processing_count(self) -> int:
        """DBから処理中ドキュメント数を取得（実行数の正）"""
        try:
            result = self.db.client.table('Rawdata_FILE_AND_MAIL').select(
                'id', count='exact'
            ).eq('processing_status', 'processing').execute()
            return result.count or 0
        except Exception as e:
            logger.error(f"処理中カウント取得エラー: {e}")
            return 0

    async def process_document(
        self,
        doc: Dict[str, Any],
        preserve_workspace: bool = True,
        progress_callback=None
    ) -> bool:
        """
        単一ドキュメントを処理

        【Per-Task Logging】
        TaskLoggerコンテキストマネージャにより、このタスクのログは
        logs/tasks/{task_id}_{timestamp}.log に出力される。
        マスターログには処理開始/終了のみ記録。
        """
        document_id = doc['id']
        file_name = doc.get('file_name', 'unknown')
        title = doc.get('title', '') or '(タイトル未生成)'

        completed_or_failed = False

        # Per-Task Logging: タスク専用ログファイルに出力
        with TaskLogger(task_id=document_id) as task_log:
            # StateManagerの進捗を更新
            self.state_manager.update_progress(filename=title, stage='開始')
            self.state_manager.add_log(f"処理開始: {title}")

            try:
                self._mark_as_processing(document_id)
                logger.info(f"ドキュメント情報: file_name={file_name}, title={title}")

                # file_urlがあれば添付ファイルあり
                has_attachment = doc.get('file_url')

                if has_attachment:
                    result = await self._process_with_attachment(doc, preserve_workspace, progress_callback)
                else:
                    result = await self._process_text_only(doc, preserve_workspace, progress_callback)

                if isinstance(result, bool):
                    success = result
                    error_msg = "処理失敗" if not success else None
                else:
                    success = result.get('success', False)
                    error_msg = result.get('error', "不明なエラー") if not success else None

                if success:
                    # screenshot_urlがあればPNG削除
                    screenshot_url = doc.get('screenshot_url')
                    if screenshot_url:
                        try:
                            match = re.search(r'/d/([a-zA-Z0-9_-]+)', screenshot_url)
                            if match:
                                png_file_id = match.group(1)
                                self.drive.trash_file(png_file_id)
                                self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                                    'screenshot_url': None
                                }).eq('id', document_id).execute()
                        except Exception as e:
                            logger.warning(f"PNG削除エラー（継続）: {e}")

                    self._mark_as_completed(document_id)
                    completed_or_failed = True
                    self.state_manager.update_progress(success_inc=1)
                    self.state_manager.add_log(f"成功: {title}")
                    logger.info(f"処理完了: success=True")
                else:
                    self._mark_as_failed(document_id, error_msg)
                    completed_or_failed = True
                    self.state_manager.update_progress(error_inc=1)
                    self.state_manager.add_log(f"失敗: {title} - {error_msg}", 'ERROR')
                    logger.error(f"処理失敗: {error_msg}")

                return success

            except Exception as e:
                error_msg = f"処理中エラー: {str(e)}"
                logger.error(error_msg)
                self._mark_as_failed(document_id, error_msg)
                completed_or_failed = True
                self.state_manager.update_progress(error_inc=1)
                self.state_manager.add_log(f"システムエラー: {title} - {e}", 'ERROR')
                return False

            finally:
                if not completed_or_failed:
                    logger.warning(f"処理中断 → pendingに差し戻し: {title}")
                    try:
                        self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                            'processing_status': 'pending'
                        }).eq('id', document_id).execute()
                    except Exception as e:
                        logger.error(f"差し戻しエラー: {e}")

    async def _process_text_only(
        self,
        doc: Dict[str, Any],
        preserve_workspace: bool = True,
        progress_callback=None
    ) -> Dict[str, Any]:
        """テキストのみドキュメントを処理（F→G→J→K）"""
        import json
        from shared.common.processing.metadata_chunker import MetadataChunker

        document_id = doc['id']
        file_name = doc.get('file_name', 'text_only')

        # テキスト有無の確認（display_* フィールドが何もなければ処理不要）
        if not any([
            doc.get('display_subject'),
            doc.get('display_post_text'),
            doc.get('display_sender'),
            doc.get('display_sender_email'),
            doc.get('display_sent_at'),
        ]):
            return {'success': False, 'error': 'テキストが空です'}

        # ============================================
        # Stage F: F1→F3→F5 チェーン（テキストのみ）
        # A→B→D→E をスキップし、rawdata_record から直接 F1 に入力
        # ============================================
        self._update_document_progress(document_id, 0.2, 'データ統合')
        logger.info("[Stage F] テキストのみ: データ統合開始（A/B/D/E スキップ）")

        stage_f_result = self.stage_f.process(rawdata_record=doc)
        if not stage_f_result or not stage_f_result.get('success'):
            detail = (stage_f_result or {}).get('error', '')
            return {'success': False, 'error': f'Stage F失敗: {detail}' if detail else 'Stage F失敗'}

        # Stage G: UI最適化
        self._update_document_progress(document_id, 0.40, 'UI最適化')
        logger.info("[Stage G] UI最適化開始")
        stage_g_result = self.stage_g.process(f5_result=stage_f_result)
        if not stage_g_result or not stage_g_result.get('success'):
            detail = (stage_g_result or {}).get('error', '')
            return {'success': False, 'error': f'Stage G失敗: {detail}' if detail else 'Stage G失敗'}

        ui_data = stage_g_result.get('ui_data', {})
        final_metadata = stage_g_result.get('final_metadata', {})

        try:
            self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                'stage_g_structured_data': ui_data,
                'metadata': final_metadata,
                'g11_structured_tables': json.dumps(final_metadata.get('g11_output', []), ensure_ascii=False) if final_metadata.get('g11_output') else None,
                'g14_reconstructed_tables': json.dumps(final_metadata.get('g14_output', []), ensure_ascii=False) if final_metadata.get('g14_output') else None,
                'g17_table_analyses': json.dumps(final_metadata.get('g17_output', []), ensure_ascii=False) if final_metadata.get('g17_output') else None,
                'g21_articles': json.dumps(final_metadata.get('g21_output', []), ensure_ascii=False) if final_metadata.get('g21_output') else None,
                'g22_ai_extracted': json.dumps(final_metadata.get('g22_output', {}), ensure_ascii=False) if final_metadata.get('g22_output') else None,
                'content_dates': stage_f_result.get('all_dates') or None,
            }).eq('id', document_id).execute()
            logger.info(f"[Stage G] ui_data を DB に保存: {document_id}")
        except Exception as e:
            logger.warning(f"Stage G 結果の DB 保存エラー: {e}")

        if not final_metadata:
            logger.warning("[Stage G] final_metadata が空です")
            return {'success': False, 'error': 'Stage G: final_metadata が生成されませんでした'}

        # Stage J: チャンク化
        self._update_document_progress(document_id, 0.60, 'チャンク化')
        metadata_chunker = MetadataChunker()
        g22_output = final_metadata.get('g22_output', {})
        all_dates = stage_f_result.get('all_dates') or []
        document_data = {
            'file_name': file_name,
            'doc_type': doc.get('doc_type'),
            'display_subject': doc.get('display_subject'),
            'display_post_text': doc.get('display_post_text'),
            'display_sender': doc.get('display_sender'),
            'display_sent_at': doc.get('display_sent_at'),
            'classroom_sender_email': doc.get('classroom_sender_email'),
            # F3: 最初の日付を document_date として使用
            'document_date': all_dates[0] if all_dates else None,
            # DB カラムから取得
            'person': doc.get('person'),
            'organizations': doc.get('organizations'),
            # G22: summary / tags / people
            'summary': g22_output.get('summary', ''),
            'tags': g22_output.get('tags', []),
            'people': g22_output.get('people', []),
            # G21: articles → text_blocks（title/body → title/content）
            'text_blocks': [
                {'title': a.get('title', ''), 'content': a.get('body', '')}
                for a in final_metadata.get('g21_output', [])
                if a.get('body', '').strip()
            ],
            # G17: table analyses → structured_tables（semantic_title → table_title）
            'structured_tables': [
                {'table_title': t.get('description', ''), 'semantic_title': (t.get('sections') or [{}])[0].get('semantic_title', ''), 'headers': t.get('headers', []), 'rows': t.get('rows', []), 'metadata': t.get('metadata', {})}
                for t in final_metadata.get('g17_output', [])
                if t.get('rows')
            ],
            # G22: calendar_events（date/time/event → event_date/event_time/event_name）
            'calendar_events': [
                {'event_date': e.get('date', ''), 'event_time': e.get('time', ''), 'event_name': e.get('event', ''), 'location': e.get('location', '')}
                for e in g22_output.get('calendar_events', [])
            ],
            # G22: tasks（item → task_name）
            'tasks': [
                {'task_name': t.get('item', ''), 'deadline': t.get('deadline', ''), 'description': t.get('description', '')}
                for t in g22_output.get('tasks', [])
            ],
            # G22: notices
            'notices': [
                {'category': n.get('category', ''), 'content': n.get('content', '')}
                for n in g22_output.get('notices', [])
            ],
        }

        chunks = metadata_chunker.create_metadata_chunks(document_data)

        try:
            self.db.client.table('10_ix_search_index').delete().eq('document_id', document_id).execute()
        except Exception as e:
            logger.warning(f"既存チャンク削除エラー（継続）: {e}")

        # Stage K: Embedding
        self._update_document_progress(document_id, 0.80, 'Embedding')
        stage_k_result = self.stage_k.embed_and_save(document_id, chunks)

        if not stage_k_result.get('success'):
            return {'success': False, 'error': f"Stage K失敗: {stage_k_result.get('failed_count', 0)}/{len(chunks)}チャンク保存失敗"}

        failed_count = stage_k_result.get('failed_count', 0)
        if failed_count > 0:
            return {'success': False, 'error': f"Stage K部分失敗: {failed_count}/{len(chunks)}チャンク保存失敗"}

        try:
            self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                'metadata': final_metadata,
                'processing_status': 'completed',
                'processing_progress': 1.0
            }).eq('id', document_id).execute()
            logger.info(f"[テキストのみ] 完了: articles={len(final_metadata.get('articles', []))}件")
        except Exception as e:
            return {'success': False, 'error': f"ドキュメント更新エラー: {e}"}

        logger.info("[パイプライン完了] F→G→J→K すべて成功（テキストのみ）")
        return {'success': True}

    async def _process_with_attachment(
        self,
        doc: Dict[str, Any],
        preserve_workspace: bool = True,
        progress_callback=None
    ) -> Dict[str, Any]:
        """添付ファイルありドキュメントを処理"""
        document_id = doc['id']
        file_name = doc.get('file_name', 'unknown')

        # file_urlからファイルIDを抽出
        drive_file_id = None
        file_url = doc.get('file_url')
        logger.debug(f"[DEBUG] file_url: {file_url}")
        if file_url:
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', file_url)
            if match:
                drive_file_id = match.group(1)
                logger.info(f"[file_url] ファイルID抽出: {drive_file_id}")
            else:
                logger.warning(f"[file_url] 正規表現マッチ失敗: {file_url}")

        if not drive_file_id:
            return {'success': False, 'error': 'file_url からファイルIDを取得できません'}

        file_extension = Path(file_name).suffix.lower()
        if file_extension in self.VIDEO_EXTENSIONS:
            logger.info(f"動画ファイルをスキップ: {file_name}")
            return {'success': True}

        screenshot_url = doc.get('screenshot_url')
        download_file_id = drive_file_id
        download_file_name = file_name

        if screenshot_url:
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', screenshot_url)
            if match:
                download_file_id = match.group(1)
                base_name = Path(file_name).stem
                download_file_name = f"{base_name}.png"
                logger.info(f"[OCR用] PNGをダウンロード: {download_file_name}")

        # ============================================
        # P0-3: doc固有の temp_dir を作成（状態隔離）
        # ============================================
        doc_temp_dir = self.temp_dir / document_id
        doc_temp_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[P0] doc固有temp作成: {doc_temp_dir}")

        # ダウンロード
        self._update_document_progress(document_id, 0.1, 'ダウンロード')
        try:
            self.drive.download_file(download_file_id, download_file_name, str(doc_temp_dir))
            local_path = doc_temp_dir / download_file_name
        except Exception as e:
            error_str = str(e)
            if 'File not found' in error_str or '404' in error_str:
                logger.warning(f"Driveにファイルが存在しません。テキストのみ処理にフォールバック")
                return await self._process_text_only(doc, preserve_workspace)
            return {'success': False, 'error': f'ダウンロード失敗: {e}'}

        mime_type = doc.get('mimeType')
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(file_name)
        if not mime_type:
            mime_type = 'application/octet-stream'

        # 新しいパイプライン実行: A→B→D→E→F→G→J→K
        workspace_to_use = doc.get('workspace', 'unknown') if preserve_workspace else 'unknown'

        try:
            # Stage A: 書類種別判定
            self._update_document_progress(document_id, 0.35, '書類種別判定')
            logger.info("[Stage A] 書類種別判定開始")
            stage_a_result = self.stage_a.process(str(local_path))
            if not stage_a_result or not stage_a_result.get('success'):
                detail = (stage_a_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage A失敗: {detail}' if detail else 'Stage A失敗'}

            # Stage A の結果を DB に保存
            try:
                raw_metadata = stage_a_result.get('raw_metadata', {})
                pdf_creator = raw_metadata.get('Creator', '')
                pdf_producer = raw_metadata.get('Producer', '')

                # Gatekeeper 結果を取得
                gatekeeper_result = stage_a_result.get('a5_gatekeeper') or stage_a_result.get('gatekeeper') or {}

                update_data = {
                    'pdf_creator': pdf_creator,
                    'pdf_producer': pdf_producer,
                    'origin_app': stage_a_result.get('origin_app'),
                    'layout_profile': stage_a_result.get('layout_profile'),
                    # doc_type は ingestion が設定した値を保持する（Stage A で上書きしない）
                    # Gatekeeper フィールド
                    'gate_decision': gatekeeper_result.get('decision'),
                    'gate_block_code': gatekeeper_result.get('block_code'),
                    'gate_block_reason': gatekeeper_result.get('block_reason'),
                    'origin_confidence': stage_a_result.get('confidence'),
                    'gate_policy_version': gatekeeper_result.get('policy_version'),
                }

                self.db.client.table('Rawdata_FILE_AND_MAIL').update(
                    update_data
                ).eq('id', document_id).execute()

                logger.info(f"[Stage A] 結果をDBに保存:")
                logger.info(f"  ├─ origin_app: {stage_a_result.get('origin_app')}")
                logger.info(f"  ├─ origin_confidence: {stage_a_result.get('confidence')}")
                logger.info(f"  ├─ layout_profile: {stage_a_result.get('layout_profile')}")
                logger.info(f"  ├─ gate_decision: {gatekeeper_result.get('decision')}")
                if gatekeeper_result.get('decision') == 'BLOCK':
                    logger.info(f"  ├─ gate_block_code: {gatekeeper_result.get('block_code')}")
                    logger.info(f"  └─ gate_block_reason: {gatekeeper_result.get('block_reason')}")
                else:
                    logger.info(f"  └─ gate_policy_version: {gatekeeper_result.get('policy_version')}")
            except Exception as e:
                logger.warning(f"[Stage A] 結果のDB保存エラー（処理は継続）: {e}")

            # classify_results / classify_page_details への記録（本番パイプラインのみ）
            try:
                raw_meta = stage_a_result.get('raw_metadata') or {}
                classify_row = {
                    'filename':   file_name or '',
                    'rawdata_id': document_id,
                    'creator':    raw_meta.get('Creator') or None,
                    'producer':   raw_meta.get('Producer') or None,
                    'pdf_title':  raw_meta.get('Title') or None,
                    'raw_meta':   raw_meta,
                    'verdict':    stage_a_result.get('origin_app') or stage_a_result.get('document_type') or '',
                    'reason':     stage_a_result.get('reason') or None,
                    'page_count': stage_a_result.get('page_count') or 0,
                    'error_msg':  None,
                }
                res = self.db.client.table('classify_results').insert(classify_row).execute()
                if res.data:
                    classify_result_id = res.data[0]['id']
                    per_page = (
                        stage_a_result.get('a4_layout', {}).get('layout_metrics', {}).get('per_page', [])
                        or stage_a_result.get('layout_metrics', {}).get('per_page', [])
                    )
                    page_type_map = stage_a_result.get('page_type_map', {})
                    if per_page:
                        page_rows = []
                        for p in per_page:
                            page_idx = p.get('page', 0)  # 0-based
                            images_detail = p.get('images_detail', []) or []
                            page_rows.append({
                                'result_id':           classify_result_id,
                                'page_num':            page_idx + 1,  # 1-based
                                'verdict':             page_type_map.get(page_idx, '') or '',
                                'reason':              None,
                                'chars':               p.get('chars'),
                                'images':              p.get('images'),
                                'vectors':             p.get('vector_count'),
                                'has_selectable_text': p.get('has_selectable_text'),
                                'x0_std':              p.get('x_std'),
                                'fonts':               [f.get('name', '') for f in (p.get('fonts') or []) if isinstance(f, dict)],
                                'wing_fonts':          [],
                                'colorspaces':         list({d.get('colorspace', '') for d in images_detail if d.get('colorspace')}),
                                'filters':             list({d.get('filter', '') for d in images_detail if d.get('filter')}),
                            })
                        self.db.client.table('classify_page_details').insert(page_rows).execute()
                    logger.info(f"[Stage A] classify_results 保存完了: {classify_row['filename']}")
            except Exception as e:
                logger.warning(f"[Stage A] classify_results 保存エラー（処理は継続）: {e}")

            # Stage B: Format-Specific Physical Structuring
            self._update_document_progress(document_id, 0.40, '物理構造抽出')
            logger.info("[Stage B] 物理構造抽出開始")
            stage_b_result = self.stage_b.process(
                file_path=str(local_path),
                a_result=stage_a_result,
                log_dir=local_path.parent,
            )
            if not stage_b_result or not stage_b_result.get('success'):
                b_error = (stage_b_result or {}).get('error', '不明')
                raw_meta = stage_a_result.get('raw_metadata', {})
                creator = raw_meta.get('Creator', '')
                producer = raw_meta.get('Producer', '')
                gate = stage_a_result.get('a5_gatekeeper') or stage_a_result.get('gatekeeper') or {}
                origin_app = stage_a_result.get('origin_app', '')
                confidence = stage_a_result.get('confidence', '')
                gate_code = gate.get('block_code', '')
                gate_reason = gate.get('block_reason', '')
                parts = [f'Stage B失敗: {b_error}']
                if creator:    parts.append(f'Creator={creator}')
                if producer:   parts.append(f'Producer={producer}')
                if origin_app: parts.append(f'判定={origin_app}({confidence})')
                if gate_code:  parts.append(f'Gate={gate_code}')
                if gate_reason:parts.append(f'理由={gate_reason}')
                return {'success': False, 'error': ' | '.join(parts)}

            purged_pdf_path = stage_b_result.get('purged_pdf_path')
            if not purged_pdf_path:
                return {'success': False, 'error': 'Stage B失敗: purged_pdf_path が生成されませんでした'}

            # Stage D + E: Multi-page loop
            # page_type_map からコンテンツページを決定
            # - COVER のみ除外（テキストなし・処理不要）
            # - UNKNOWN（LOW信頼度）は Stage B をスキップ済みだが D/E には含める → OCR
            _page_type_map = stage_a_result.get('page_type_map', {})
            _page_confidence_map = stage_a_result.get('page_confidence_map', {})
            if _page_type_map:
                content_pages = sorted(
                    idx for idx, ptype in _page_type_map.items()
                    if ptype != 'COVER'
                )
            else:
                content_pages = [0]  # フォールバック: ページ0のみ

            low_conf_pages_set = {
                int(idx) for idx, conf in _page_confidence_map.items()
                if conf == 'LOW'
            }
            high_conf_count = len(content_pages) - len(
                [p for p in content_pages if p in low_conf_pages_set]
            )
            logger.info(
                f"[Stage D] 処理対象ページ: {[p+1 for p in content_pages]} "
                f"（HIGH={high_conf_count}, LOW/OCR={len(low_conf_pages_set)}）"
            )

            all_d_results = []

            for page_idx, page_num in enumerate(content_pages):
                # ページ別出力ディレクトリ（ファイル名衝突回避）
                page_output_dir = Path(doc_temp_dir) / f"page_{page_num}"
                page_output_dir.mkdir(parents=True, exist_ok=True)

                # Stage D: Visual Structure Analysis（1ページずつ）
                progress_d = 0.45 + (page_idx / len(content_pages)) * 0.05
                self._update_document_progress(document_id, progress_d, f'視覚構造解析 p{page_num+1}')
                logger.info(f"[Stage D] ページ {page_num+1} 視覚構造解析開始")

                # purged_image_path は None → D1 が page_num 指定で自動生成
                d_result = self.stage_d.process(
                    pdf_path=Path(purged_pdf_path),
                    purged_image_path=None,
                    page_num=page_num,
                    output_dir=page_output_dir
                )
                if not d_result or not d_result.get('success'):
                    detail = (d_result or {}).get('error', '')
                    logger.warning(f"[Stage D] ページ {page_num+1} 失敗: {detail} → スキップ")
                    continue
                all_d_results.append(d_result)

            if not all_d_results:
                return {'success': False, 'error': 'Stage D失敗: 全ページが失敗しました'}

            logger.info(f"[Stage D] 完了: {len(all_d_results)}/{len(content_pages)}ページ")

            # 全ページの D 結果をマージ
            # tables: 全ページ分を結合
            # non_table_image_paths: ページごとの背景画像リスト（E が順番に処理）
            stage_d_result = self._merge_d_results(all_d_results)

            # Stage E: 全ページ分の PNG を一括処理
            # E1Controller は non_table_image_paths リストをループして処理する
            self._update_document_progress(document_id, 0.50, 'AI抽出')
            logger.info(f"[Stage E] AI抽出開始: {len(all_d_results)}ページ分")
            stage_e_result = self.stage_e.process(
                purged_pdf_path=purged_pdf_path,
                stage_d_result=stage_d_result,
                output_dir=doc_temp_dir,
                stage_b_result=stage_b_result
            )
            if not stage_e_result or not stage_e_result.get('success'):
                detail = (stage_e_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage E失敗: {detail}' if detail else 'Stage E失敗'}

            # Stage F: Data Fusion & Normalization
            self._update_document_progress(document_id, 0.55, 'データ統合')
            logger.info("[Stage F] データ統合開始")
            stage_f_result = self.stage_f.process(
                stage_a_result=stage_a_result,
                stage_b_result=stage_b_result,
                stage_d_result=stage_d_result,
                stage_e_result=stage_e_result,
                rawdata_record=doc,
            )
            if not stage_f_result or not stage_f_result.get('success'):
                detail = (stage_f_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage F失敗: {detail}' if detail else 'Stage F失敗'}

            # Stage G: UI Optimized Structuring
            self._update_document_progress(document_id, 0.60, 'UI最適化')
            logger.info("[Stage G] UI最適化開始")
            # ★G-1はF-5の結果のみを受け取る（直前ステージのみ）
            stage_g_result = self.stage_g.process(
                f5_result=stage_f_result
            )
            if not stage_g_result or not stage_g_result.get('success'):
                detail = (stage_g_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage G失敗: {detail}' if detail else 'Stage G失敗'}

            # Stage G の結果を DB に保存
            try:
                ui_data = stage_g_result.get('ui_data', {})
                final_metadata = stage_g_result.get('final_metadata', {})
                import json

                # デバッグ：保存前の final_metadata を確認
                logger.info("")
                logger.info("[Pipeline Manager] ========== DB保存前の final_metadata ==========")
                logger.info(f"  g11_output: {len(final_metadata.get('g11_output', []))}個")
                logger.info(f"  g14_output: {len(final_metadata.get('g14_output', []))}個")
                logger.info(f"  g17_output: {len(final_metadata.get('g17_output', []))}個")
                logger.info(f"  g21_output: {len(final_metadata.get('g21_output', []))}件")
                logger.info(f"  g22_output: {type(final_metadata.get('g22_output', {}))}")
                logger.info("=" * 70)

                self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                    'stage_g_structured_data': ui_data,  # JSONB - dict をそのまま渡す
                    'metadata': final_metadata,  # JSONB - dict をそのまま渡す
                    'g11_structured_tables': json.dumps(final_metadata.get('g11_output', []), ensure_ascii=False) if final_metadata.get('g11_output') else None,
                    'g14_reconstructed_tables': json.dumps(final_metadata.get('g14_output', []), ensure_ascii=False) if final_metadata.get('g14_output') else None,
                    'g17_table_analyses': json.dumps(final_metadata.get('g17_output', []), ensure_ascii=False) if final_metadata.get('g17_output') else None,
                    'g21_articles': json.dumps(final_metadata.get('g21_output', []), ensure_ascii=False) if final_metadata.get('g21_output') else None,
                    'g22_ai_extracted': json.dumps(final_metadata.get('g22_output', {}), ensure_ascii=False) if final_metadata.get('g22_output') else None,
                    'content_dates': stage_f_result.get('all_dates') or None,
                }).eq('id', document_id).execute()
                logger.info(f"[Stage G] ui_data を DB に保存: {document_id}")
                logger.info(f"[Stage G] G-11/G-14/G-17/G-21/G-22 を個別カラムに保存")
                logger.info(f"  ├─ G-11: {len(final_metadata.get('g11_output', []))}表")
                logger.info(f"  ├─ G-14: {len(final_metadata.get('g14_output', []))}表")
                logger.info(f"  ├─ G-17: {len(final_metadata.get('g17_output', []))}表")
                logger.info(f"  ├─ G-21: {len(final_metadata.get('g21_output', []))}記事")
                logger.info(f"  └─ G-22: {len(final_metadata.get('g22_output', {}).get('calendar_events', []))}イベント")
            except Exception as e:
                logger.warning(f"Stage G 結果の DB 保存エラー: {e}")

            # Stage H: 削除（G11/G21 で構造化済み）
            # Stage G の final_metadata をそのまま使用（すでに取得済み）

            if not final_metadata:
                logger.warning("[Stage G] final_metadata が空です")
                return {'success': False, 'error': 'Stage G: final_metadata が生成されませんでした'}

            logger.info(f"[Stage G] final_metadata を取得: articles={len(final_metadata.get('articles', []))}件")

            # Stage J: Chunking
            self._update_document_progress(document_id, 0.70, 'チャンク化')
            logger.info("[Stage J] チャンク化開始")

            from shared.common.processing.metadata_chunker import MetadataChunker
            metadata_chunker = MetadataChunker()

            g22_output = final_metadata.get('g22_output', {})
            all_dates = stage_f_result.get('all_dates') or []
            document_data = {
                'file_name': file_name,
                'doc_type': doc.get('doc_type'),
                'display_subject': doc.get('display_subject'),
                'display_post_text': doc.get('display_post_text'),
                'display_sender': doc.get('display_sender'),
                'display_sent_at': doc.get('display_sent_at'),
                'classroom_sender_email': doc.get('classroom_sender_email'),
                # F3: 最初の日付を document_date として使用
                'document_date': all_dates[0] if all_dates else None,
                # DB カラムから取得
                'person': doc.get('person'),
                'organizations': doc.get('organizations'),
                # G22: summary / tags / people
                'summary': g22_output.get('summary', ''),
                'tags': g22_output.get('tags', []),
                'people': g22_output.get('people', []),
                # G21: articles → text_blocks（title/body → title/content）
                'text_blocks': [
                    {'title': a.get('title', ''), 'content': a.get('body', '')}
                    for a in final_metadata.get('g21_output', [])
                    if a.get('body', '').strip()
                ],
                # G17: table analyses → structured_tables（semantic_title → table_title）
                'structured_tables': [
                    {'table_title': t.get('description', ''), 'semantic_title': (t.get('sections') or [{}])[0].get('semantic_title', ''), 'headers': t.get('headers', []), 'rows': t.get('rows', []), 'metadata': t.get('metadata', {})}
                    for t in final_metadata.get('g17_output', [])
                    if t.get('rows')
                ],
                # G22: calendar_events（date/time/event → event_date/event_time/event_name）
                'calendar_events': [
                    {'event_date': e.get('date', ''), 'event_time': e.get('time', ''), 'event_name': e.get('event', ''), 'location': e.get('location', '')}
                    for e in g22_output.get('calendar_events', [])
                ],
                # G22: tasks（item → task_name）
                'tasks': [
                    {'task_name': t.get('item', ''), 'deadline': t.get('deadline', ''), 'description': t.get('description', '')}
                    for t in g22_output.get('tasks', [])
                ],
                # G22: notices
                'notices': [
                    {'category': n.get('category', ''), 'content': n.get('content', '')}
                    for n in g22_output.get('notices', [])
                ],
            }

            chunks = metadata_chunker.create_metadata_chunks(document_data)

            # 既存チャンク削除
            try:
                self.db.client.table('10_ix_search_index').delete().eq('document_id', document_id).execute()
            except Exception as e:
                logger.warning(f"既存チャンク削除エラー（継続）: {e}")

            # Stage K: Embedding
            self._update_document_progress(document_id, 0.80, 'Embedding')
            logger.info("[Stage K] Embedding開始")
            stage_k_result = self.stage_k.embed_and_save(document_id, chunks)

            if not stage_k_result.get('success'):
                return {'success': False, 'error': f"Stage K失敗: {stage_k_result.get('failed_count', 0)}/{len(chunks)}チャンク保存失敗"}

            # Stage G の final_metadata を DB に保存
            try:
                self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                    'metadata': final_metadata,
                    'processing_status': 'completed',
                    'processing_progress': 1.0
                }).eq('id', document_id).execute()
                logger.info(f"[Stage G] metadata を DB に保存: articles={len(final_metadata.get('articles', []))}件, calendar_events={len(final_metadata.get('calendar_events', []))}件, tasks={len(final_metadata.get('tasks', []))}件")
            except Exception as e:
                logger.warning(f"metadata 保存エラー: {e}")
                return {'success': False, 'error': f'metadata 保存失敗: {e}'}

            logger.info(f"[パイプライン完了] A→B→D→E→F→G→J→K すべて成功")
            return {'success': True}

        finally:
            # ============================================
            # P0-2: temp削除は最後に1回だけ（doc_temp_dir 全体を削除）
            # GC後リトライ: fitz/pdfplumber がPDFを開いたままGC待ちの場合に
            # WinError 5（アクセス拒否）が発生するため、gc.collect() で解放してから再試行する
            # ============================================
            import shutil
            import gc
            if doc_temp_dir.exists():
                try:
                    shutil.rmtree(doc_temp_dir)
                    logger.info(f"[P0] doc固有temp削除完了: {doc_temp_dir}")
                except Exception as cleanup_error:
                    # GC後にリトライ（ファイルロック解放待ち）
                    gc.collect()
                    try:
                        shutil.rmtree(doc_temp_dir)
                        logger.info(f"[P0] doc固有temp削除完了（GCリトライ成功）: {doc_temp_dir}")
                    except Exception as retry_error:
                        logger.warning(f"[P0] temp削除失敗（無視して続行）: {retry_error}")

    async def run_batch(
        self,
        workspace: str = 'all',
        limit: int = 100,
        preserve_workspace: bool = True
    ):
        """
        バッチ処理のメインループ

        【リース方式】
        - dequeue_document RPC で1件ずつ原子化取得
        - 100ワーカーが同時に実行しても重複ゼロを保証
        - ワーカーが落ちてもリース期限後に別ワーカーが回収

        StateManagerを通じて状態を一元管理
        ExecutionPolicyで実行可否を判断（SSOT）
        """
        # ExecutionPolicy でグローバル停止をチェック（SSOT）
        policy_result = self.execution_policy.can_execute(workspace=workspace if workspace != 'all' else None)
        if not policy_result.allowed:
            logger.warning(f"バッチ実行拒否: {policy_result.deny_code} - {policy_result.deny_reason}")
            return

        # 【リース方式】処理対象数を事前取得（StateManager用の概算）
        pending_docs = self.get_pending_documents(workspace, limit)
        estimated_count = len(pending_docs)

        if estimated_count == 0:
            self.state_manager.add_log("処理対象のドキュメントがありません")
            return

        logger.info(f"[Lease] Estimated queued documents: {estimated_count}")

        # StateManagerで処理開始（ロック取得）
        if not self.state_manager.start_processing(min(estimated_count, limit)):
            logger.warning("処理開始に失敗しました（既に処理中か、ロック取得失敗）")
            return

        # loguruのカスタムハンドラー：pipelineログをstate_managerに転送
        state_manager = self.state_manager  # クロージャー用
        def log_to_state_manager(message):
            log_record = message.record
            level = log_record['level'].name
            msg = log_record['message']
            module_name = log_record['name']

            # state_managerからのログは除外（二重登録を防ぐ）
            if 'state_manager' in module_name:
                return

            # INFO, WARNING, ERRORのログを転送（元の実装と同じ）
            if level in ['INFO', 'WARNING', 'ERROR']:
                # skip_logger=Trueでデッドロック回避（add_logがタイムスタンプを付ける）
                state_manager.add_log(msg, level=level, skip_logger=True)

        # loguruにカスタムハンドラーを追加（フィルタなし、元の実装と同じ）
        self._log_handler_id = logger.add(
            log_to_state_manager,
            format="{message}"
        )

        # リソースマネージャーを初期化
        self.resource_manager = AdaptiveResourceManager(
            initial_max_parallel=1,  # 初期値は1、実行数が上限に達したら増加
            min_parallel=1,
            max_parallel_limit=100
        )

        # 定期的なDB同期タイマー
        sync_timer = None
        sync_interval = 5.0

        def periodic_sync():
            nonlocal sync_timer
            if not self.state_manager.get_status()['is_processing']:
                return

            try:
                memory_info = get_cgroup_memory()
                cpu_percent = get_cgroup_cpu()
                self.state_manager.sync_to_db(cpu_percent, memory_info)
            except Exception as e:
                logger.error(f"定期同期エラー: {e}")
            finally:
                if self.state_manager.get_status()['is_processing']:
                    sync_timer = threading.Timer(sync_interval, periodic_sync)
                    sync_timer.daemon = True
                    sync_timer.start()

        # 定期同期開始
        sync_timer = threading.Timer(sync_interval, periodic_sync)
        sync_timer.daemon = True
        sync_timer.start()

        # 非同期タスク管理
        active_tasks = set()
        processed_count = 0  # 処理した件数

        # サマリー用: 結果追跡
        batch_start_time = datetime.now()
        success_results: List[Dict[str, Any]] = []
        failed_results: List[Dict[str, Any]] = []

        try:
            # 【リース方式】1件ずつ dequeue してループ
            while processed_count < limit:
                # ExecutionPolicy で停止要求をチェック（SSOT）
                policy_check = self.execution_policy.can_execute(workspace=workspace if workspace != 'all' else None)
                if not policy_check.allowed:
                    if policy_check.deny_code == 'STOP_REQUESTED':
                        self.state_manager.add_log(f"停止要求により処理を中断: {policy_check.deny_reason}", 'WARNING')
                        break

                # 【リース方式】原子化されたデキュー（RPC）
                doc = self.dequeue_document(workspace)

                if doc is None:
                    # キューが空 or 全てロック中
                    if active_tasks:
                        # まだ実行中タスクがあれば待機
                        logger.debug("[Lease] No document available, waiting for active tasks...")
                        done, active_tasks = await asyncio.wait(
                            active_tasks,
                            return_when=asyncio.FIRST_COMPLETED
                        )
                        # 完了タスクの結果を収集
                        for task in done:
                            try:
                                result = task.result()
                                if isinstance(result, dict):
                                    if result.get('success'):
                                        success_results.append(result)
                                    else:
                                        failed_results.append(result)
                            except Exception as e:
                                failed_results.append({'id': '(不明)', 'title': '(不明)', 'error': str(e)})
                        continue
                    else:
                        # 全て処理完了
                        logger.info("[Lease] Queue empty, batch complete")
                        break

                processed_count += 1

                # DBから処理中ドキュメント数を取得（実行数の正）
                processing_count = self._get_processing_count()

                # リソース監視と調整
                memory_info = get_cgroup_memory()
                res_status = self.resource_manager.adjust_resources(
                    memory_info['percent'],
                    processing_count  # DBから取得した実行数を使用
                )

                # ループ状態をログ出力
                logger.info(
                    f"[LOOP] {processed_count}/{limit} | "
                    f"実行中: {processing_count}/{res_status['max_parallel']} | "
                    f"メモリ: {memory_info['percent']:.1f}% | "
                    f"スロットル: {res_status['throttle_delay']:.1f}s"
                )

                # StateManagerのリソース情報を更新
                self.state_manager.update_resource_control(
                    throttle_delay=res_status['throttle_delay'],
                    max_parallel=res_status['max_parallel'],
                    current_workers=len(active_tasks)  # 実際のタスク数を使用
                )

                # スロットル待機
                if res_status['throttle_delay'] > 0:
                    await asyncio.sleep(res_status['throttle_delay'])

                # 並列数制限（DBの実行数がmax_parallel以上なら待機）
                while processing_count >= res_status['max_parallel'] and active_tasks:
                    logger.debug(f"[WAIT] 並列上限到達 ({processing_count}/{res_status['max_parallel']}), タスク完了待ち...")
                    done, active_tasks = await asyncio.wait(
                        active_tasks,
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    # 完了タスクの結果を収集
                    for task in done:
                        try:
                            result = task.result()
                            if isinstance(result, dict):
                                if result.get('success'):
                                    success_results.append(result)
                                else:
                                    failed_results.append(result)
                        except Exception as e:
                            failed_results.append({'id': '(不明)', 'title': '(不明)', 'error': str(e)})
                    # タスク完了後にcurrent_workersを更新
                    self.state_manager.update_resource_control(
                        current_workers=len(active_tasks)
                    )
                    # 待機後に再取得
                    processing_count = self._get_processing_count()
                    logger.debug(f"[WAIT] タスク完了, 現在の実行数: {processing_count}")

                # 進捗更新
                self.state_manager.update_progress(index=processed_count)

                # タスク追加（dequeue で既に processing になっているので _mark_as_processing は不要）
                task = asyncio.create_task(
                    self._process_dequeued_document(doc, preserve_workspace)
                )
                active_tasks.add(task)

                # タスク追加後にcurrent_workersを更新
                self.state_manager.update_resource_control(
                    current_workers=len(active_tasks)
                )

                # イベントループに制御を渡す
                await asyncio.sleep(0)

            # 残りのタスクを待機
            if active_tasks:
                results = await asyncio.gather(*active_tasks, return_exceptions=True)
                # 結果を収集
                for result in results:
                    if isinstance(result, Exception):
                        failed_results.append({'id': '(不明)', 'title': '(不明)', 'error': str(result)})
                    elif isinstance(result, dict):
                        if result.get('success'):
                            success_results.append(result)
                        else:
                            failed_results.append(result)
                # 全タスク完了後にcurrent_workersを0に
                self.state_manager.update_resource_control(current_workers=0)

        finally:
            # loguruハンドラーを削除
            if self._log_handler_id is not None:
                logger.remove(self._log_handler_id)
                self._log_handler_id = None

            # タイマー停止
            if sync_timer:
                sync_timer.cancel()

            # 処理終了
            self.state_manager.finish_processing()

            # サマリー生成
            batch_end_time = datetime.now()
            if success_results or failed_results:
                log_dir = Path(__file__).resolve().parent.parent.parent / 'logs'
                generate_batch_summary(
                    log_dir=log_dir,
                    start_time=batch_start_time,
                    end_time=batch_end_time,
                    success_results=success_results,
                    failed_results=failed_results,
                    workspace=workspace,
                    limit=limit
                )


    async def _process_dequeued_document(
        self,
        doc: Dict[str, Any],
        preserve_workspace: bool = True
    ) -> Dict[str, Any]:
        """
        【リース方式】dequeue されたドキュメントを処理

        process_document との違い:
        - _mark_as_processing は呼ばない（dequeue で既に processing）
        - 完了/失敗時に ack_document / nack_document を使用

        【Per-Task Logging】
        TaskLoggerコンテキストマネージャにより、このタスクのログは
        logs/tasks/{task_id}_{timestamp}.log に出力される。
        """
        document_id = doc['id']
        file_name = doc.get('file_name', 'unknown')
        title = doc.get('title', '') or '(タイトル未生成)'

        # Per-Task Logging: タスク専用ログファイルに出力
        with TaskLogger(task_id=document_id) as task_log:
            # StateManagerの進捗を更新
            self.state_manager.update_progress(filename=title, stage='開始')
            self.state_manager.add_log(f"処理開始: {title}")

            try:
                # _mark_as_processing は不要（dequeue で既に設定済み）
                logger.info(f"ドキュメント情報: file_name={file_name}, title={title}")

                # file_urlがあれば添付ファイルあり
                has_attachment = doc.get('file_url')

                if has_attachment:
                    result = await self._process_with_attachment(doc, preserve_workspace)
                else:
                    result = await self._process_text_only(doc, preserve_workspace)

                if isinstance(result, bool):
                    success = result
                    error_msg = "処理失敗" if not success else None
                else:
                    success = result.get('success', False)
                    error_msg = result.get('error', "不明なエラー") if not success else None

                if success:
                    # screenshot_urlがあればPNG削除
                    screenshot_url = doc.get('screenshot_url')
                    if screenshot_url:
                        try:
                            match = re.search(r'/d/([a-zA-Z0-9_-]+)', screenshot_url)
                            if match:
                                png_file_id = match.group(1)
                                self.drive.trash_file(png_file_id)
                                self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                                    'screenshot_url': None
                                }).eq('id', document_id).execute()
                        except Exception as e:
                            logger.warning(f"PNG削除エラー（継続）: {e}")

                    # 【リース方式】ack_document で完了
                    self._mark_as_completed(document_id)
                    self.state_manager.update_progress(success_inc=1)
                    self.state_manager.add_log(f"成功: {title}")
                    logger.info(f"処理完了: success=True")
                    return {'success': True, 'id': document_id, 'title': title}
                else:
                    # 【リース方式】nack_document で失敗（retry=False）
                    self._mark_as_failed(document_id, error_msg)
                    self.state_manager.update_progress(error_inc=1)
                    self.state_manager.add_log(f"失敗: {title} - {error_msg}", 'ERROR')
                    logger.error(f"処理失敗: {error_msg}")
                    return {'success': False, 'id': document_id, 'title': title, 'error': error_msg}

            except Exception as e:
                error_msg = f"処理中エラー: {str(e)}"
                logger.error(error_msg)
                # 【リース方式】nack_document で失敗
                self._mark_as_failed(document_id, error_msg)
                self.state_manager.update_progress(error_inc=1)
                self.state_manager.add_log(f"システムエラー: {title} - {e}", 'ERROR')
                return {'success': False, 'id': document_id, 'title': title, 'error': error_msg}


# continuous_processing_loop は削除済み
# 【設計原則】常駐禁止 - バッチ1回実行（Cloud Run Jobs / ローカル両用）
# 代わりに process_queued_documents.py --run-request <id> --execute を使用
