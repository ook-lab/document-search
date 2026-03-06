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


def _assemble_gmail_text(slots: list, stage_e_result: dict) -> str:
    """
    B27 のスロットリストと E1 の OCR 結果を文章順に結合して文字列を返す。

    - text スロット: そのまま挿入
    - image スロット: E1 の non_table_content.blocks から OCR テキストを取得して挿入
    """
    # E1 の blocks から page（= slot_idx）順に OCR テキストを収集
    ocr_by_slot: dict = {}
    non_table_content = stage_e_result.get('non_table_content') or {}
    blocks = non_table_content.get('blocks') or []
    for block in blocks:
        page_idx = block.get('page', 0)
        text = (block.get('text') or '').strip()
        if text:
            ocr_by_slot.setdefault(page_idx, []).append(text)

    parts = []
    for slot in slots:
        if slot['type'] == 'text':
            text = (slot.get('text') or '').strip()
            if text:
                parts.append(text)
        elif slot['type'] == 'image':
            idx = slot.get('slot_idx', 0)
            ocr_texts = ocr_by_slot.get(idx, [])
            if ocr_texts:
                parts.append('\n'.join(ocr_texts))

    return '\n\n'.join(parts)


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
        self._gemini_key = gemini_key

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

    def get_pending_documents(self, source: str = 'all', limit: int = 100) -> List[Dict[str, Any]]:
        """
        pipeline_meta から pending ドキュメントを取得（dry-run・統計表示用）

        【注意】実際の処理では dequeue_pipeline() を使用すること
        """
        try:
            query = self.db.client.table('pipeline_meta').select(
                'id, raw_id, raw_table, person, source, processing_status'
            ).eq('processing_status', 'queued')
            if source != 'all':
                query = query.eq('source', source)
            result = query.order('created_at').limit(limit).execute()
            docs = result.data if result.data else []
            # タイトルを付与
            if docs:
                raw_ids = list({d['raw_id'] for d in docs if d.get('raw_id')})
                try:
                    ud = self.db.client.table('09_unified_documents').select(
                        'raw_id, raw_table, title'
                    ).in_('raw_id', raw_ids).execute()
                    title_map = {
                        (r['raw_id'], r['raw_table']): r['title']
                        for r in (ud.data or [])
                    }
                    for d in docs:
                        d['file_name'] = title_map.get(
                            (d.get('raw_id'), d.get('raw_table')), '(不明)'
                        )
                except Exception:
                    pass
            return docs
        except Exception as e:
            logger.error(f"get_pending_documents error: {e}")
            return []

    def get_document_by_id(self, doc_id: str) -> Dict[str, Any] | None:
        """pipeline_meta.id でドキュメントを取得"""
        try:
            result = self.db.client.table('pipeline_meta').select('*').eq('id', doc_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"get_document_by_id error: {e}")
            return None

    async def process_single_document(self, doc_id: str, preserve_workspace: bool = True) -> bool:
        """
        pipeline_meta.id で単一ドキュメントを処理（CLI用）

        Args:
            doc_id: pipeline_meta.id
            preserve_workspace: 未使用（後方互換のため残す）

        Returns:
            処理成功ならTrue
        """
        policy_result = self.execution_policy.can_execute(doc_id=doc_id)
        if not policy_result.allowed:
            logger.warning(f"実行拒否: {policy_result.deny_code} - {policy_result.deny_reason}")
            return False

        if not self.state_manager.start_processing(1):
            logger.warning("処理開始に失敗しました（既に処理中か、ロック取得失敗）")
            return False

        try:
            return await self.process_pipeline_meta_job(doc_id)
        finally:
            self.state_manager.finish_processing()

    def get_queue_stats(self, source: str = 'all') -> Dict[str, int]:
        """pipeline_meta から統計情報を取得"""
        try:
            query = self.db.client.table('pipeline_meta').select('processing_status')
            if source != 'all':
                query = query.eq('source', source)
            response = query.limit(100000).execute()

            stats = {'pending': 0, 'processing': 0, 'completed': 0, 'failed': 0, 'null': 0}
            for doc in (response.data or []):
                status = doc.get('processing_status')
                if status is None:
                    stats['null'] += 1
                else:
                    stats[status] = stats.get(status, 0) + 1

            stats['total'] = len(response.data or [])
            processed = stats['completed'] + stats['failed']
            stats['success_rate'] = round(stats['completed'] / processed * 100, 1) if processed > 0 else 0.0
            return stats

        except Exception as e:
            logger.error(f"統計取得エラー: {e}")
            return {}

    def _mark_as_processing(self, meta_id: str):
        """pipeline_meta を processing にマーク"""
        try:
            self.db.client.table('pipeline_meta').update({
                'processing_status': 'processing',
                'processing_progress': 0.0,
            }).eq('id', meta_id).execute()
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

    def _update_document_progress(self, meta_id: str, progress: float, log_message: str = None):
        """pipeline_meta の進捗を更新"""
        self._update_pipeline_meta_progress(meta_id, progress, log_message)
        if log_message:
            self.state_manager.update_progress(stage=log_message, stage_progress=progress)

    def _mark_as_completed(self, meta_id: str):
        """pipeline_meta を完了にマーク（ack_pipeline_meta 優先）"""
        if not self.ack_pipeline_meta(meta_id):
            try:
                self.db.client.table('pipeline_meta').update({
                    'processing_status': 'completed',
                    'processing_progress': 1.0,
                    'lease_owner': None,
                    'lease_until': None,
                    'completed_at': datetime.now(timezone.utc).isoformat(),
                }).eq('id', meta_id).execute()
                logger.debug(f"[PM] Fallback completed: {meta_id}")
            except Exception as e:
                logger.error(f"完了マークエラー: {e}")

    def _mark_as_failed(self, meta_id: str, error_message: str = ""):
        """pipeline_meta を失敗にマーク（nack_pipeline_meta 優先）"""
        if not self.nack_pipeline_meta(meta_id, error_message, retry=False):
            try:
                self.db.client.table('pipeline_meta').update({
                    'processing_status': 'failed',
                    'processing_progress': 0.0,
                    'lease_owner': None,
                    'lease_until': None,
                    'last_error_reason': error_message,
                    'failed_at': datetime.now(timezone.utc).isoformat(),
                }).eq('id', meta_id).execute()
                logger.debug(f"[PM] Fallback failed: {meta_id}")
            except Exception as e:
                logger.error(f"失敗マークエラー: {e}")

    def _get_processing_count(self) -> int:
        """pipeline_meta から処理中ドキュメント数を取得"""
        try:
            result = self.db.client.table('pipeline_meta').select(
                'id', count='exact'
            ).eq('processing_status', 'processing').execute()
            return result.count or 0
        except Exception as e:
            logger.error(f"処理中カウント取得エラー: {e}")
            return 0



    # ============================================================
    # pipeline_meta ベース処理（新スキーマ: 05_* raw テーブル向け）
    # ============================================================

    def dequeue_pipeline(self, raw_table: str) -> Optional[Dict[str, Any]]:
        """
        【pipeline_meta リース方式】原子化デキュー（RPC 経由）

        raw_table を指定して1件取得 + processing + リース設定を1操作で行う。

        Args:
            raw_table: 対象 raw テーブル名（例: '05_ikuya_waseaca_01_raw'）

        Returns:
            取得したメタ情報（meta_id, raw_id, raw_table, person, source, attempt_count）
            なければ None
        """
        try:
            result = self.db.client.rpc('dequeue_pipeline', {
                'p_raw_table':     raw_table,
                'p_lease_seconds': self.DEFAULT_LEASE_SECONDS,
                'p_owner':         self.owner,
            }).execute()

            if result.data and len(result.data) > 0:
                meta = result.data[0]
                logger.info(
                    f"[PM-Lease] Dequeued: meta_id={meta['meta_id']}, "
                    f"raw_id={meta['raw_id']}, owner={self.owner}"
                )
                return meta
            return None

        except Exception as e:
            logger.error(f"[PM-Lease] dequeue_pipeline RPC error: {e}")
            return None

    def ack_pipeline_meta(self, meta_id: str) -> bool:
        """pipeline_meta 処理完了を通知（owner 条件付き）"""
        try:
            result = self.db.client.rpc('ack_pipeline', {
                'p_meta_id': meta_id,
                'p_owner':   self.owner,
            }).execute()
            row_count = result.data[0] if isinstance(result.data, list) and result.data else (result.data or 0)
            if row_count == 0:
                logger.warning(f"[PM-Lease] ack_pipeline rowcount=0 (owner mismatch): {meta_id}")
                return False
            logger.info(f"[PM-Lease] Acked: meta_id={meta_id}")
            return True
        except Exception as e:
            logger.error(f"[PM-Lease] ack_pipeline RPC error: {e}")
            return False

    def nack_pipeline_meta(self, meta_id: str, error_message: str = None, retry: bool = True) -> bool:
        """pipeline_meta 処理失敗を通知（owner 条件付き）"""
        try:
            result = self.db.client.rpc('nack_pipeline', {
                'p_meta_id':       meta_id,
                'p_owner':         self.owner,
                'p_error_message': error_message,
                'p_retry':         retry,
            }).execute()
            row_count = result.data[0] if isinstance(result.data, list) and result.data else (result.data or 0)
            if row_count == 0:
                logger.warning(f"[PM-Lease] nack_pipeline rowcount=0 (owner mismatch): {meta_id}")
                return False
            logger.info(f"[PM-Lease] Nacked: meta_id={meta_id}, retry={retry}")
            return True
        except Exception as e:
            logger.error(f"[PM-Lease] nack_pipeline RPC error: {e}")
            return False

    def renew_pipeline_meta_lease(self, meta_id: str) -> bool:
        """pipeline_meta リース延長（ロングジョブ用）"""
        try:
            result = self.db.client.rpc('renew_pipeline_lease', {
                'p_meta_id':       meta_id,
                'p_owner':         self.owner,
                'p_lease_seconds': self.DEFAULT_LEASE_SECONDS,
            }).execute()
            row_count = result.data[0] if isinstance(result.data, list) and result.data else (result.data or 0)
            if row_count == 0:
                logger.warning(f"[PM-Lease] renew_pipeline_lease rowcount=0: {meta_id}")
                return False
            logger.debug(f"[PM-Lease] Renewed: meta_id={meta_id}")
            return True
        except Exception as e:
            logger.error(f"[PM-Lease] renew_pipeline_lease RPC error: {e}")
            return False

    def _update_pipeline_meta_progress(self, meta_id: str, progress: float, log_message: str = None):
        """pipeline_meta の進捗を更新"""
        try:
            self.db.client.table('pipeline_meta').update({
                'processing_progress': progress,
            }).eq('id', meta_id).execute()
            if log_message:
                logger.debug(f"[PM] 進捗更新: {log_message} ({progress*100:.0f}%)")
        except Exception as e:
            logger.error(f"[PM] pipeline_meta 進捗更新エラー: {e}")

    @staticmethod
    def _map_raw_to_pipeline_doc(raw_doc: dict, raw_table: str) -> dict:
        """
        raw テーブルのレコードをパイプライン互換形式に変換。

        パイプラインステージ（Stage A-G）は display_* / doc_type / person 等の
        フィールドを参照するため、各 raw テーブルのカラムをマッピングする。
        """
        if raw_table == '05_ikuya_waseaca_01_raw':
            return {
                'id':                raw_doc.get('id'),
                'file_name':         raw_doc.get('file_name'),
                'file_url':          raw_doc.get('file_url'),
                'title':             raw_doc.get('title', ''),
                'display_subject':   raw_doc.get('title', ''),
                'display_post_text': raw_doc.get('description', ''),
                'display_sender':    raw_doc.get('creator_name', ''),
                'display_sent_at':   raw_doc.get('created_at'),
                'doc_type':          raw_doc.get('source', '早稲アカオンライン'),
                'person':            raw_doc.get('person'),
                'organizations':     None,
                'workspace':         'waseda_academy',
                'mimeType':          None,
                'screenshot_url':    None,
            }
        if raw_table == '01_gmail_01_raw':
            return {
                'id':                    raw_doc.get('id'),
                'file_name':             None,
                'file_url':              None,
                'title':                 raw_doc.get('header_subject', ''),
                'display_subject':       raw_doc.get('header_subject', ''),
                'display_sender':        raw_doc.get('from_name', ''),
                'display_sender_email':  raw_doc.get('from_email', ''),
                'display_sent_at':       raw_doc.get('sent_at'),
                'display_post_text':     raw_doc.get('body_plain') or '',
                'doc_type':              'gmail-DM',
                'person':                raw_doc.get('person'),
                'organizations':         None,
                'workspace':             raw_doc.get('source', 'gmail'),
                'mimeType':              None,
            }
        # デフォルト: そのまま返す（将来の raw テーブル追加に備え）
        return dict(raw_doc)

    async def process_pipeline_meta_job(self, meta_id: str) -> bool:
        """
        pipeline_meta ベースのジョブを処理（新スキーマ向け）

        【フロー】
        1. pipeline_meta から raw_id, raw_table を取得
        2. raw_table から文書データを取得
        3. パイプライン全ステージを実行（A→B→D→E→F→G→J→K）
        4. G 中間データを pipeline_meta に保存
        5. G31: 09_unified_documents に書き込み（raw_table 指定）
        6. ack / nack

        Args:
            meta_id: pipeline_meta.id

        Returns:
            処理成功なら True
        """
        import json
        import shutil
        import gc
        from shared.pipeline.stage_g.g31_unified_writer import G31UnifiedWriter
        from shared.common.processing.metadata_chunker import MetadataChunker

        # --------------------------------------------------
        # 1. pipeline_meta 取得
        # --------------------------------------------------
        try:
            meta_res = self.db.client.table('pipeline_meta').select('*').eq('id', meta_id).single().execute()
            meta = meta_res.data
            if not meta:
                logger.error(f"[PM] pipeline_meta not found: {meta_id}")
                return False
        except Exception as e:
            logger.error(f"[PM] pipeline_meta 取得エラー: {e}")
            return False

        raw_id    = meta['raw_id']
        raw_table = meta['raw_table']

        # --------------------------------------------------
        # 2. raw テーブルから文書データ取得
        # --------------------------------------------------
        try:
            raw_res = self.db.client.table(raw_table).select('*').eq('id', str(raw_id)).single().execute()
            raw_doc = raw_res.data
            if not raw_doc:
                logger.error(f"[PM] raw_doc not found: {raw_table}/{raw_id}")
                return False
        except Exception as e:
            logger.error(f"[PM] raw_doc 取得エラー: {e}")
            return False

        doc       = self._map_raw_to_pipeline_doc(raw_doc, raw_table)
        file_name = doc.get('file_name', 'unknown')
        title     = doc.get('title', '') or '(タイトル未生成)'

        logger.info(f"[PM] ジョブ処理開始: meta_id={meta_id}")
        logger.info(f"[PM]   raw_table={raw_table}, raw_id={raw_id}")
        logger.info(f"[PM]   title={title}, file_url={doc.get('file_url')}")

        # processing 状態をマーク
        try:
            self.db.client.table('pipeline_meta').update({
                'processing_status': 'processing',
                'started_at': datetime.now(timezone.utc).isoformat(),
            }).eq('id', meta_id).execute()
        except Exception as e:
            logger.warning(f"[PM] status→processing 更新エラー（継続）: {e}")

        doc_temp_dir = self.temp_dir / str(raw_id)
        doc_temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # --------------------------------------------------
            # 添付ファイルあり / なし で分岐
            # --------------------------------------------------
            if raw_table == '01_gmail_01_raw':
                result = await self._process_pipeline_meta_gmail(
                    doc, meta_id, raw_table, raw_doc, doc_temp_dir
                )
            elif doc.get('file_url'):
                result = await self._process_pipeline_meta_with_attachment(
                    doc, meta_id, raw_table, raw_doc, doc_temp_dir
                )
            else:
                result = await self._process_pipeline_meta_text_only(
                    doc, meta_id, raw_table, raw_doc
                )

            if result.get('success'):
                self.ack_pipeline_meta(meta_id)
                logger.info(f"[PM] ジョブ完了: meta_id={meta_id}")
                return True
            else:
                error = result.get('error', '不明なエラー')
                self.nack_pipeline_meta(meta_id, error_message=error, retry=True)
                logger.error(f"[PM] ジョブ失敗（リトライ可）: meta_id={meta_id} - {error}")
                return False

        except Exception as e:
            error = f"処理中例外: {str(e)}"
            logger.error(f"[PM] 例外: {e}", exc_info=True)
            self.nack_pipeline_meta(meta_id, error_message=error, retry=True)
            return False

        finally:
            if doc_temp_dir.exists():
                try:
                    shutil.rmtree(doc_temp_dir)
                    logger.info(f"[PM] temp 削除完了: {doc_temp_dir}")
                except Exception as cleanup_err:
                    gc.collect()
                    try:
                        shutil.rmtree(doc_temp_dir)
                    except Exception:
                        logger.warning(f"[PM] temp 削除失敗（無視）: {cleanup_err}")

    async def _process_pipeline_meta_with_attachment(
        self,
        doc: Dict[str, Any],
        meta_id: str,
        raw_table: str,
        raw_doc: Dict[str, Any],
        doc_temp_dir: Path,
    ) -> Dict[str, Any]:
        """
        添付ファイルあり（PDF）のパイプライン処理（pipeline_meta 向け）

        G 中間データを pipeline_meta に保存し、
        G31 を raw_table 指定で呼び出す。
        """
        import json
        from shared.pipeline.stage_g.g31_unified_writer import G31UnifiedWriter
        from shared.common.processing.metadata_chunker import MetadataChunker

        file_name = doc.get('file_name', 'unknown')
        file_url  = doc.get('file_url', '')

        # Drive ファイルID を抽出
        drive_file_id = None
        if file_url:
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', file_url)
            if match:
                drive_file_id = match.group(1)

        if not drive_file_id:
            return {'success': False, 'error': 'file_url から Drive ファイルID を取得できません'}

        file_extension = Path(file_name).suffix.lower()
        if file_extension in self.VIDEO_EXTENSIONS:
            logger.info(f"[PM] 動画ファイルをスキップ: {file_name}")
            return {'success': True}

        # ダウンロード
        self._update_pipeline_meta_progress(meta_id, 0.1, 'ダウンロード')
        try:
            self.drive.download_file(drive_file_id, file_name, str(doc_temp_dir))
            local_path = doc_temp_dir / file_name
        except Exception as e:
            error_str = str(e)
            if 'File not found' in error_str or '404' in error_str:
                logger.warning(f"[PM] Drive にファイルなし。テキストのみ処理にフォールバック")
                return await self._process_pipeline_meta_text_only(doc, meta_id, raw_table, raw_doc)
            return {'success': False, 'error': f'ダウンロード失敗: {e}'}

        mime_type = doc.get('mimeType')
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(file_name)
        if not mime_type:
            mime_type = 'application/octet-stream'

        try:
            # Stage A
            self._update_pipeline_meta_progress(meta_id, 0.35, '書類種別判定')
            logger.info("[PM-Stage A] 書類種別判定開始")
            stage_a_result = self.stage_a.process(str(local_path))
            if not stage_a_result or not stage_a_result.get('success'):
                detail = (stage_a_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage A失敗: {detail}' if detail else 'Stage A失敗'}

            # Stage B
            self._update_pipeline_meta_progress(meta_id, 0.40, '物理構造抽出')
            logger.info("[PM-Stage B] 物理構造抽出開始")
            stage_b_result = self.stage_b.process(
                file_path=str(local_path),
                a_result=stage_a_result,
                log_dir=local_path.parent,
            )
            if not stage_b_result or not stage_b_result.get('success'):
                b_error = (stage_b_result or {}).get('error', '不明')
                return {'success': False, 'error': f'Stage B失敗: {b_error}'}

            purged_pdf_path = stage_b_result.get('purged_pdf_path')
            if not purged_pdf_path:
                return {'success': False, 'error': 'Stage B失敗: purged_pdf_path が生成されませんでした'}

            # Stage D / E: マルチページ
            _page_type_map = stage_a_result.get('page_type_map', {})
            if _page_type_map:
                content_pages = sorted(
                    idx for idx, ptype in _page_type_map.items()
                    if ptype != 'COVER'
                )
            else:
                content_pages = [0]

            all_d_results = []
            for page_idx, page_num in enumerate(content_pages):
                page_output_dir = doc_temp_dir / f"page_{page_num}"
                page_output_dir.mkdir(parents=True, exist_ok=True)

                progress_d = 0.45 + (page_idx / len(content_pages)) * 0.05
                self._update_pipeline_meta_progress(meta_id, progress_d, f'視覚構造解析 p{page_num+1}')
                logger.info(f"[PM-Stage D] ページ {page_num+1} 視覚構造解析")

                d_result = self.stage_d.process(
                    pdf_path=Path(purged_pdf_path),
                    purged_image_path=None,
                    page_num=page_num,
                    output_dir=page_output_dir,
                )
                if not d_result or not d_result.get('success'):
                    detail = (d_result or {}).get('error', '')
                    logger.warning(f"[PM-Stage D] ページ {page_num+1} 失敗: {detail} → スキップ")
                    continue
                all_d_results.append(d_result)

            if not all_d_results:
                return {'success': False, 'error': 'Stage D失敗: 全ページが失敗しました'}

            stage_d_result = self._merge_d_results(all_d_results)

            # Stage E
            self._update_pipeline_meta_progress(meta_id, 0.50, 'AI抽出')
            logger.info(f"[PM-Stage E] AI抽出開始: {len(all_d_results)}ページ分")
            stage_e_result = self.stage_e.process(
                purged_pdf_path=purged_pdf_path,
                stage_d_result=stage_d_result,
                output_dir=doc_temp_dir,
                stage_b_result=stage_b_result,
                session_id=meta_id,
            )
            if not stage_e_result or not stage_e_result.get('success'):
                detail = (stage_e_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage E失敗: {detail}' if detail else 'Stage E失敗'}

            # Stage F
            self._update_pipeline_meta_progress(meta_id, 0.55, 'データ統合')
            logger.info("[PM-Stage F] データ統合開始")
            stage_f_result = self.stage_f.process(
                stage_a_result=stage_a_result,
                stage_b_result=stage_b_result,
                stage_d_result=stage_d_result,
                stage_e_result=stage_e_result,
                rawdata_record=doc,
                session_id=meta_id,
            )
            if not stage_f_result or not stage_f_result.get('success'):
                detail = (stage_f_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage F失敗: {detail}' if detail else 'Stage F失敗'}

            # Stage G（document_id=meta_id で G 中間データをクラッシュ時に pipeline_meta へ保存）
            self._update_pipeline_meta_progress(meta_id, 0.60, 'UI最適化')
            logger.info("[PM-Stage G] UI最適化開始")
            stage_g = G1Controller(document_id=meta_id, api_key=self._gemini_key)
            stage_g_result = stage_g.process(f5_result=stage_f_result)
            if not stage_g_result or not stage_g_result.get('success'):
                detail = (stage_g_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage G失敗: {detail}' if detail else 'Stage G失敗'}

            ui_data        = stage_g_result.get('ui_data', {})
            final_metadata = stage_g_result.get('final_metadata', {})

            if not final_metadata:
                return {'success': False, 'error': 'Stage G: final_metadata が生成されませんでした'}

            # G31: 09_unified_documents へ書き込み（raw_table 指定）
            g31 = G31UnifiedWriter(self.db)
            g31_result = g31.process(
                raw_data=raw_doc,
                raw_table=raw_table,
                ui_data=ui_data,
            )
            if not g31_result.get('success'):
                logger.warning(f"[PM-G31] 09_unified_documents 書き込み失敗: {g31_result.get('error')}")
            unified_doc_id = g31_result.get('doc_id')

            # G 中間データを 09_unified_documents.meta に保存（検索用）
            if unified_doc_id:
                try:
                    g_meta = {}
                    if final_metadata.get('g11_output'): g_meta['g11_output'] = final_metadata['g11_output']
                    if final_metadata.get('g14_output'): g_meta['g14_output'] = final_metadata['g14_output']
                    if final_metadata.get('g17_output'): g_meta['g17_output'] = final_metadata['g17_output']
                    if final_metadata.get('g21_output'): g_meta['g21_output'] = final_metadata['g21_output']
                    if final_metadata.get('g22_output'): g_meta['g22_output'] = final_metadata['g22_output']
                    if g_meta:
                        existing = self.db.client.table('09_unified_documents').select('meta').eq('id', unified_doc_id).execute()
                        current_meta = (existing.data[0].get('meta') or {}) if existing.data else {}
                        self.db.client.table('09_unified_documents').update(
                            {'meta': {**current_meta, **g_meta}}
                        ).eq('id', unified_doc_id).execute()
                        logger.info(f"[PM-G31] G中間データを 09_unified_documents.meta に保存完了")
                except Exception as e:
                    logger.warning(f"[PM-G31] 09_unified_documents.meta 保存エラー（継続）: {e}")

            # Stage J: チャンク化
            self._update_pipeline_meta_progress(meta_id, 0.70, 'チャンク化')
            metadata_chunker = MetadataChunker()
            g22_output = final_metadata.get('g22_output', {})
            all_dates  = stage_f_result.get('all_dates') or []
            document_data = {
                'file_name':       file_name,
                'doc_type':        doc.get('doc_type'),
                'display_subject': doc.get('display_subject'),
                'display_post_text': doc.get('display_post_text'),
                'display_sender':  doc.get('display_sender'),
                'display_sent_at': doc.get('display_sent_at'),
                'document_date':   all_dates[0] if all_dates else None,
                'person':          doc.get('person'),
                'organizations':   doc.get('organizations'),
                'summary':         g22_output.get('summary', ''),
                'tags':            g22_output.get('tags', []),
                'people':          g22_output.get('people', []),
                'text_blocks': [
                    {'title': a.get('title', ''), 'content': a.get('body', '')}
                    for a in final_metadata.get('g21_output', [])
                    if a.get('body', '').strip()
                ],
                'structured_tables': [
                    {'table_title': t.get('description', ''), 'semantic_title': (t.get('sections') or [{}])[0].get('semantic_title', ''), 'headers': t.get('headers', []), 'rows': t.get('rows', []), 'metadata': t.get('metadata', {})}
                    for t in final_metadata.get('g17_output', [])
                    if t.get('rows')
                ],
                'calendar_events': [
                    {'event_date': e.get('date', ''), 'event_time': e.get('time', ''), 'event_name': e.get('event', ''), 'location': e.get('location', '')}
                    for e in g22_output.get('calendar_events', [])
                ],
                'tasks': [
                    {'task_name': t.get('item', ''), 'deadline': t.get('deadline', ''), 'description': t.get('description', '')}
                    for t in g22_output.get('tasks', [])
                ],
                'notices': [
                    {'category': n.get('category', ''), 'content': n.get('content', '')}
                    for n in g22_output.get('notices', [])
                ],
            }
            chunks = metadata_chunker.create_metadata_chunks(document_data)

            # Stage K: Embedding
            self._update_pipeline_meta_progress(meta_id, 0.80, 'Embedding')
            logger.info("[PM-Stage K] Embedding 開始")
            stage_k_result = self.stage_k.embed_and_save(
                doc_id=unified_doc_id,
                chunks=chunks,
                delete_existing=True,
            )
            if not stage_k_result.get('success'):
                return {'success': False, 'error': f"Stage K失敗: {stage_k_result.get('failed_count', 0)}/{len(chunks)}チャンク保存失敗"}

            logger.info("[PM] A→B→D→E→F→G→G31→J→K すべて成功")
            return {'success': True}

        finally:
            pass  # temp 削除は process_pipeline_meta_job の finally で行う

    async def _process_pipeline_meta_gmail(
        self,
        doc: Dict[str, Any],
        meta_id: str,
        raw_table: str,
        raw_doc: Dict[str, Any],
        doc_temp_dir: Path,
    ) -> Dict[str, Any]:
        """
        Gmail メールのパイプライン処理（B26/B27 → E1 → F1 → G1 → G31 → J → K）

        - HTML あり: B27 → 画像 OCR (E1) → テキスト結合
        - テキストのみ: B26 → 段落整形
        Stage A/D スキップ
        """
        from shared.pipeline.stage_g.g31_unified_writer import G31UnifiedWriter
        from shared.common.processing.metadata_chunker import MetadataChunker
        from shared.pipeline.stage_b.b26_gmail_text import B26GmailTextProcessor
        from shared.pipeline.stage_b.b27_gmail_html import B27GmailHTMLProcessor

        file_name = 'gmail'
        body_html = raw_doc.get('body_html') or ''

        self._update_pipeline_meta_progress(meta_id, 0.10, 'メール解析')

        body_plain_chars = len(raw_doc.get('body_plain') or '')

        if body_html:
            logger.info("[PM-Gmail] HTML メール: B27 処理開始")
            b27 = B27GmailHTMLProcessor()
            b_result = b27.process(raw_doc, temp_dir=doc_temp_dir)

            text_slots  = [s for s in b_result.get('slots', []) if s['type'] == 'text']
            image_slots = [s for s in b_result.get('slots', []) if s['type'] == 'image']
            plain_chars = len(b_result.get('plain_text') or '')
            logger.info(f"[PM-Gmail] B27完了: テキストスロット={len(text_slots)}件 / 画像スロット={len(image_slots)}件 / plain_text={plain_chars}文字")
            for i, ts in enumerate(text_slots):
                ts_text = (ts.get('text') or '').strip()
                logger.debug(f"[PM-Gmail]   テキストスロット[{i}] ({len(ts_text)}文字):\n{ts_text}")

            if b_result['image_paths']:
                logger.info(f"[PM-Gmail] E1(AI/OCR)開始: {len(b_result['image_paths'])}枚")
                self._update_pipeline_meta_progress(meta_id, 0.25, '画像OCR')
                stage_d_mock = {
                    'non_table_image_paths': b_result['image_paths'],
                    'tables': [],
                    'page_index': 0,
                }
                stage_e_result = self.stage_e.process(
                    purged_pdf_path=None,
                    stage_d_result=stage_d_mock,
                    output_dir=str(doc_temp_dir),
                    min_gemini_chars=50,
                    session_id=meta_id,
                )
                # E1 結果の詳細ログ（画像ごとの抽出文字数）
                blocks = (stage_e_result.get('non_table_content') or {}).get('blocks') or []
                for blk in blocks:
                    blk_text  = (blk.get('text') or '').strip()
                    blk_chars = len(blk_text)
                    page_idx  = blk.get('page', '?')
                    if blk_chars:
                        logger.info(f"[PM-Gmail]   画像[{page_idx}]: AI抽出={blk_chars}文字")
                        logger.debug(f"[PM-Gmail]   画像[{page_idx}] 内容:\n{blk_text}")
                    else:
                        logger.warning(f"[PM-Gmail]   画像[{page_idx}]: AI抽出=0文字（空）")
                total_ocr_chars = sum(len((b.get('text') or '').strip()) for b in blocks)
                logger.info(f"[PM-Gmail] E1完了: 合計OCR文字数={total_ocr_chars}文字")

                assembled_text = _assemble_gmail_text(b_result['slots'], stage_e_result)
            else:
                logger.info("[PM-Gmail] 画像なし: plain_text を使用")
                assembled_text = b_result['plain_text']
        else:
            logger.info("[PM-Gmail] テキストメール: B26 処理開始")
            b26 = B26GmailTextProcessor()
            assembled_text = b26.process(raw_doc)['assembled_text']

        assembled_chars = len(assembled_text or '')
        logger.info(f"[PM-Gmail] テキスト結合完了: assembled={assembled_chars}文字 / 元body_plain={body_plain_chars}文字")
        logger.debug(f"[PM-Gmail] assembled_text 内容:\n{assembled_text}")

        if not assembled_text and not doc.get('display_subject'):
            return {'success': False, 'error': 'Gmail メール: テキストが空です'}

        # F1: assembled_text を display_post_text に渡す
        self._update_pipeline_meta_progress(meta_id, 0.40, 'データ統合')
        logger.info("[PM-Gmail] Stage F 開始")
        rawdata_for_f = {**doc, 'display_post_text': assembled_text}
        stage_f_result = self.stage_f.process(rawdata_record=rawdata_for_f, session_id=meta_id)
        if not stage_f_result or not stage_f_result.get('success'):
            detail = (stage_f_result or {}).get('error', '')
            return {'success': False, 'error': f'Stage F失敗: {detail}' if detail else 'Stage F失敗'}

        # Stage G
        self._update_pipeline_meta_progress(meta_id, 0.55, 'UI最適化')
        logger.info("[PM-Gmail] Stage G 開始")
        stage_g = G1Controller(document_id=meta_id, api_key=self._gemini_key)
        stage_g_result = stage_g.process(f5_result=stage_f_result)
        if not stage_g_result or not stage_g_result.get('success'):
            detail = (stage_g_result or {}).get('error', '')
            return {'success': False, 'error': f'Stage G失敗: {detail}' if detail else 'Stage G失敗'}

        ui_data        = stage_g_result.get('ui_data', {})
        final_metadata = stage_g_result.get('final_metadata', {})

        logger.info(
            f"[PM-Gmail] Stage G完了: "
            f"sections={len(ui_data.get('sections') or [])}件 / "
            f"tables={len(ui_data.get('tables') or [])}件 / "
            f"timeline={len(ui_data.get('timeline') or [])}件 / "
            f"actions={len(ui_data.get('actions') or [])}件 / "
            f"notices={len(ui_data.get('notices') or [])}件"
        )
        for sec in (ui_data.get('sections') or []):
            logger.debug(f"[PM-Gmail]   section: {sec.get('title', '')} / {len(sec.get('body') or '')}文字")
        for ev in (ui_data.get('timeline') or []):
            logger.debug(f"[PM-Gmail]   timeline: {ev.get('date', '')} {ev.get('event', '')}")
        for act in (ui_data.get('actions') or []):
            logger.debug(f"[PM-Gmail]   action: {act.get('label', '')} → {act.get('url', '')}")

        if not final_metadata:
            return {'success': False, 'error': 'Stage G: final_metadata が生成されませんでした'}

        # G31: 09_unified_documents へ書き込み
        g31 = G31UnifiedWriter(self.db)
        g31_result = g31.process(
            raw_data=raw_doc,
            raw_table=raw_table,
            ui_data=ui_data,
        )
        if not g31_result.get('success'):
            logger.warning(f"[PM-G31] 09_unified_documents 書き込み失敗: {g31_result.get('error')}")
        unified_doc_id = g31_result.get('doc_id')

        # G 中間データを 09_unified_documents.meta に保存
        if unified_doc_id:
            try:
                g_meta = {}
                if final_metadata.get('g11_output'): g_meta['g11_output'] = final_metadata['g11_output']
                if final_metadata.get('g14_output'): g_meta['g14_output'] = final_metadata['g14_output']
                if final_metadata.get('g17_output'): g_meta['g17_output'] = final_metadata['g17_output']
                if final_metadata.get('g21_output'): g_meta['g21_output'] = final_metadata['g21_output']
                if final_metadata.get('g22_output'): g_meta['g22_output'] = final_metadata['g22_output']
                if g_meta:
                    existing = self.db.client.table('09_unified_documents').select('meta').eq('id', unified_doc_id).execute()
                    current_meta = (existing.data[0].get('meta') or {}) if existing.data else {}
                    self.db.client.table('09_unified_documents').update(
                        {'meta': {**current_meta, **g_meta}}
                    ).eq('id', unified_doc_id).execute()
                    logger.info("[PM-G31] G中間データを 09_unified_documents.meta に保存完了")
            except Exception as e:
                logger.warning(f"[PM-G31] 09_unified_documents.meta 保存エラー（継続）: {e}")

        # Stage J
        self._update_pipeline_meta_progress(meta_id, 0.65, 'チャンク化')
        metadata_chunker = MetadataChunker()
        g22_output = final_metadata.get('g22_output', {})
        all_dates  = stage_f_result.get('all_dates') or []
        document_data = {
            'file_name':         file_name,
            'doc_type':          doc.get('doc_type'),
            'display_subject':   doc.get('display_subject'),
            'display_post_text': assembled_text,
            'display_sender':    doc.get('display_sender'),
            'display_sent_at':   doc.get('display_sent_at'),
            'document_date':     all_dates[0] if all_dates else None,
            'person':            doc.get('person'),
            'organizations':     doc.get('organizations'),
            'summary':           g22_output.get('summary', ''),
            'tags':              g22_output.get('tags', []),
            'people':            g22_output.get('people', []),
            'text_blocks': [
                {'title': a.get('title', ''), 'content': a.get('body', '')}
                for a in final_metadata.get('g21_output', [])
                if a.get('body', '').strip()
            ],
            'structured_tables': [
                {
                    'table_title': t.get('description', ''),
                    'semantic_title': (t.get('sections') or [{}])[0].get('semantic_title', ''),
                    'headers': t.get('headers', []),
                    'rows': t.get('rows', []),
                    'metadata': t.get('metadata', {}),
                }
                for t in final_metadata.get('g17_output', [])
                if t.get('rows')
            ],
            'calendar_events': [
                {'event_date': e.get('date', ''), 'event_time': e.get('time', ''), 'event_name': e.get('event', ''), 'location': e.get('location', '')}
                for e in g22_output.get('calendar_events', [])
            ],
            'tasks': [
                {'task_name': t.get('item', ''), 'deadline': t.get('deadline', ''), 'description': t.get('description', '')}
                for t in g22_output.get('tasks', [])
            ],
            'notices': [
                {'category': n.get('category', ''), 'content': n.get('content', '')}
                for n in g22_output.get('notices', [])
            ],
        }
        chunks = metadata_chunker.create_metadata_chunks(document_data)

        # Stage K
        self._update_pipeline_meta_progress(meta_id, 0.80, 'Embedding')
        stage_k_result = self.stage_k.embed_and_save(
            doc_id=unified_doc_id,
            chunks=chunks,
            delete_existing=True,
        )
        if not stage_k_result.get('success'):
            return {'success': False, 'error': f"Stage K失敗: {stage_k_result.get('failed_count', 0)}/{len(chunks)}チャンク保存失敗"}

        logger.info("[PM-Gmail] B26/B27→E1→F→G→G31→J→K すべて成功")
        return {'success': True}

    async def _process_pipeline_meta_text_only(
        self,
        doc: Dict[str, Any],
        meta_id: str,
        raw_table: str,
        raw_doc: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        テキストのみドキュメントのパイプライン処理（pipeline_meta 向け）

        G 中間データを pipeline_meta に保存し、
        G31 を raw_table 指定で呼び出す。
        """
        from shared.pipeline.stage_g.g31_unified_writer import G31UnifiedWriter
        from shared.common.processing.metadata_chunker import MetadataChunker

        file_name = doc.get('file_name', 'text_only')

        if not any([
            doc.get('display_subject'),
            doc.get('display_post_text'),
            doc.get('display_sender'),
            doc.get('display_sent_at'),
        ]):
            return {'success': False, 'error': 'テキストが空です'}

        # Stage F（テキストのみ: A/B/D/E スキップ）
        self._update_pipeline_meta_progress(meta_id, 0.2, 'データ統合')
        logger.info("[PM-Stage F] テキストのみ: データ統合開始")
        stage_f_result = self.stage_f.process(rawdata_record=doc, session_id=meta_id)
        if not stage_f_result or not stage_f_result.get('success'):
            detail = (stage_f_result or {}).get('error', '')
            return {'success': False, 'error': f'Stage F失敗: {detail}' if detail else 'Stage F失敗'}

        # Stage G（document_id=meta_id で G 中間データをクラッシュ時に pipeline_meta へ保存）
        self._update_pipeline_meta_progress(meta_id, 0.40, 'UI最適化')
        logger.info("[PM-Stage G] UI最適化開始")
        stage_g = G1Controller(document_id=meta_id, api_key=self._gemini_key)
        stage_g_result = stage_g.process(f5_result=stage_f_result)
        if not stage_g_result or not stage_g_result.get('success'):
            detail = (stage_g_result or {}).get('error', '')
            return {'success': False, 'error': f'Stage G失敗: {detail}' if detail else 'Stage G失敗'}

        ui_data        = stage_g_result.get('ui_data', {})
        final_metadata = stage_g_result.get('final_metadata', {})

        if not final_metadata:
            return {'success': False, 'error': 'Stage G: final_metadata が生成されませんでした'}

        # G31: 09_unified_documents へ書き込み（raw_table 指定）
        g31 = G31UnifiedWriter(self.db)
        g31_result = g31.process(
            raw_data=raw_doc,
            raw_table=raw_table,
            ui_data=ui_data,
        )
        if not g31_result.get('success'):
            logger.warning(f"[PM-G31] 09_unified_documents 書き込み失敗: {g31_result.get('error')}")
        unified_doc_id = g31_result.get('doc_id')

        # G 中間データを 09_unified_documents.meta に保存（検索用）
        if unified_doc_id:
            try:
                g_meta = {}
                if final_metadata.get('g11_output'): g_meta['g11_output'] = final_metadata['g11_output']
                if final_metadata.get('g14_output'): g_meta['g14_output'] = final_metadata['g14_output']
                if final_metadata.get('g17_output'): g_meta['g17_output'] = final_metadata['g17_output']
                if final_metadata.get('g21_output'): g_meta['g21_output'] = final_metadata['g21_output']
                if final_metadata.get('g22_output'): g_meta['g22_output'] = final_metadata['g22_output']
                if g_meta:
                    existing = self.db.client.table('09_unified_documents').select('meta').eq('id', unified_doc_id).execute()
                    current_meta = (existing.data[0].get('meta') or {}) if existing.data else {}
                    self.db.client.table('09_unified_documents').update(
                        {'meta': {**current_meta, **g_meta}}
                    ).eq('id', unified_doc_id).execute()
                    logger.info(f"[PM-G31] G中間データを 09_unified_documents.meta に保存完了")
            except Exception as e:
                logger.warning(f"[PM-G31] 09_unified_documents.meta 保存エラー（継続）: {e}")

        # Stage J
        self._update_pipeline_meta_progress(meta_id, 0.60, 'チャンク化')
        metadata_chunker = MetadataChunker()
        g22_output = final_metadata.get('g22_output', {})
        all_dates  = stage_f_result.get('all_dates') or []
        document_data = {
            'file_name':       file_name,
            'doc_type':        doc.get('doc_type'),
            'display_subject': doc.get('display_subject'),
            'display_post_text': doc.get('display_post_text'),
            'display_sender':  doc.get('display_sender'),
            'display_sent_at': doc.get('display_sent_at'),
            'document_date':   all_dates[0] if all_dates else None,
            'person':          doc.get('person'),
            'organizations':   doc.get('organizations'),
            'summary':         g22_output.get('summary', ''),
            'tags':            g22_output.get('tags', []),
            'people':          g22_output.get('people', []),
            'text_blocks': [
                {'title': a.get('title', ''), 'content': a.get('body', '')}
                for a in final_metadata.get('g21_output', [])
                if a.get('body', '').strip()
            ],
            'structured_tables': [
                {'table_title': t.get('description', ''), 'semantic_title': (t.get('sections') or [{}])[0].get('semantic_title', ''), 'headers': t.get('headers', []), 'rows': t.get('rows', []), 'metadata': t.get('metadata', {})}
                for t in final_metadata.get('g17_output', [])
                if t.get('rows')
            ],
            'calendar_events': [
                {'event_date': e.get('date', ''), 'event_time': e.get('time', ''), 'event_name': e.get('event', ''), 'location': e.get('location', '')}
                for e in g22_output.get('calendar_events', [])
            ],
            'tasks': [
                {'task_name': t.get('item', ''), 'deadline': t.get('deadline', ''), 'description': t.get('description', '')}
                for t in g22_output.get('tasks', [])
            ],
            'notices': [
                {'category': n.get('category', ''), 'content': n.get('content', '')}
                for n in g22_output.get('notices', [])
            ],
        }
        chunks = metadata_chunker.create_metadata_chunks(document_data)

        # Stage K
        self._update_pipeline_meta_progress(meta_id, 0.80, 'Embedding')
        stage_k_result = self.stage_k.embed_and_save(
            doc_id=unified_doc_id,
            chunks=chunks,
            delete_existing=True,
        )
        if not stage_k_result.get('success'):
            return {'success': False, 'error': f"Stage K失敗: {stage_k_result.get('failed_count', 0)}/{len(chunks)}チャンク保存失敗"}

        logger.info("[PM] F→G→G31→J→K すべて成功（テキストのみ）")
        return {'success': True}

    async def run_batch(
        self,
        source: str = 'all',
        limit: int = 100,
        preserve_workspace: bool = True
    ):
        """
        pipeline_meta ベースのバッチ処理メインループ

        【リース方式】
        - dequeue_pipeline RPC で原子デキュー（複数 raw_table をラウンドロビン）
        - 100ワーカーが同時に実行しても重複ゼロを保証

        StateManagerを通じて状態を一元管理
        ExecutionPolicyで実行可否を判断（SSOT）
        """
        # ExecutionPolicy でグローバル停止をチェック（SSOT）
        policy_result = self.execution_policy.can_execute(workspace=source if source != 'all' else None)
        if not policy_result.allowed:
            logger.warning(f"バッチ実行拒否: {policy_result.deny_code} - {policy_result.deny_reason}")
            return

        # queued の raw_table 一覧を取得（ラウンドロビン用）
        try:
            q = self.db.client.table('pipeline_meta').select('raw_table').eq('processing_status', 'queued')
            if source != 'all':
                q = q.eq('source', source)
            qt = q.execute()
            active_raw_tables = list({r['raw_table'] for r in (qt.data or []) if r.get('raw_table')})
        except Exception as e:
            logger.error(f"[Batch] raw_table 取得エラー: {e}")
            return

        if not active_raw_tables:
            self.state_manager.add_log("処理対象のドキュメントがありません")
            return

        # 処理対象数の概算（StateManager用）
        pending_docs = self.get_pending_documents(source, min(limit, 200))
        estimated_count = len(pending_docs) or limit

        logger.info(f"[Batch] pending raw_tables: {active_raw_tables}, estimated={estimated_count}")

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

        rt_cursor = 0  # ラウンドロビン用カーソル

        try:
            # pipeline_meta から1件ずつ dequeue してループ
            while processed_count < limit and active_raw_tables:
                # ExecutionPolicy で停止要求をチェック（SSOT）
                policy_check = self.execution_policy.can_execute(workspace=source if source != 'all' else None)
                if not policy_check.allowed:
                    if policy_check.deny_code == 'STOP_REQUESTED':
                        self.state_manager.add_log(f"停止要求により処理を中断: {policy_check.deny_reason}", 'WARNING')
                        break

                # ラウンドロビンで raw_table を選択してデキュー
                raw_table = active_raw_tables[rt_cursor % len(active_raw_tables)]
                meta = self.dequeue_pipeline(raw_table)

                if meta is None:
                    # この raw_table は空 → リストから除外
                    active_raw_tables.remove(raw_table)
                    if not active_raw_tables:
                        logger.info("[Batch] 全キュー空。完了待ちへ")
                        break
                    if rt_cursor >= len(active_raw_tables):
                        rt_cursor = 0
                    if active_tasks:
                        logger.debug("[Batch] テーブル空、実行中タスク待機中...")
                        done, active_tasks = await asyncio.wait(
                            active_tasks, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in done:
                            try:
                                r = task.result()
                                (success_results if r else failed_results).append(
                                    {'id': '(不明)', 'title': '(不明)'}
                                )
                            except Exception as e:
                                failed_results.append({'id': '(不明)', 'title': '(不明)', 'error': str(e)})
                    continue

                rt_cursor = (rt_cursor + 1) % len(active_raw_tables)
                meta_id = meta.get('meta_id') or meta.get('id')
                processed_count += 1

                # DBから処理中ドキュメント数を取得（実行数の正）
                processing_count = self._get_processing_count()

                # リソース監視と調整
                memory_info = get_cgroup_memory()
                res_status = self.resource_manager.adjust_resources(
                    memory_info['percent'],
                    processing_count
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
                    current_workers=len(active_tasks)
                )

                # スロットル待機
                if res_status['throttle_delay'] > 0:
                    await asyncio.sleep(res_status['throttle_delay'])

                # 並列数制限（DBの実行数がmax_parallel以上なら待機）
                while processing_count >= res_status['max_parallel'] and active_tasks:
                    logger.debug(f"[WAIT] 並列上限到達 ({processing_count}/{res_status['max_parallel']}), タスク完了待ち...")
                    done, active_tasks = await asyncio.wait(
                        active_tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        try:
                            r = task.result()
                            (success_results if r else failed_results).append(
                                {'id': '(不明)', 'title': '(不明)'}
                            )
                        except Exception as e:
                            failed_results.append({'id': '(不明)', 'title': '(不明)', 'error': str(e)})
                    self.state_manager.update_resource_control(current_workers=len(active_tasks))
                    processing_count = self._get_processing_count()
                    logger.debug(f"[WAIT] タスク完了, 現在の実行数: {processing_count}")

                # 進捗更新
                self.state_manager.update_progress(index=processed_count)

                # タスク追加（dequeue_pipeline で既に processing になっているので _mark_as_processing は不要）
                task = asyncio.create_task(self.process_pipeline_meta_job(meta_id))
                active_tasks.add(task)

                self.state_manager.update_resource_control(current_workers=len(active_tasks))
                await asyncio.sleep(0)

            # 残りのタスクを待機
            if active_tasks:
                results = await asyncio.gather(*active_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        failed_results.append({'id': '(不明)', 'title': '(不明)', 'error': str(result)})
                    elif result:
                        success_results.append({'id': '(不明)', 'title': '(不明)'})
                    else:
                        failed_results.append({'id': '(不明)', 'title': '(不明)'})
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
                    workspace=source,
                    limit=limit
                )



# continuous_processing_loop は削除済み
# 【設計原則】常駐禁止 - バッチ1回実行（Cloud Run Jobs / ローカル両用）
# 代わりに process_queued_documents.py --source <source> --limit <n> --execute を使用
