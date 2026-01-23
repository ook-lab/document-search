"""
ドキュメント処理モジュール

DocumentProcessorクラスを提供
StateManager（SSOT）とAdaptiveResourceManagerを使用

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
from shared.pipeline import UnifiedDocumentPipeline
from shared.logging import TaskLogger  # Per-Task Logging
from .state_manager import StateManager, get_state_manager
from .resource_manager import AdaptiveResourceManager, get_cgroup_memory, get_cgroup_cpu
from .execution_policy import ExecutionPolicy, get_execution_policy


class DocumentProcessor:
    """
    ドキュメント処理クラス

    StateManagerを通じて状態を一元管理
    AdaptiveResourceManagerで並列数を動的調整
    """

    VIDEO_EXTENSIONS = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.mpeg', '.mpg']

    # リース設定
    DEFAULT_LEASE_SECONDS = 900  # 15分
    HEARTBEAT_INTERVAL = 60  # 60秒ごとにリース延長

    def __init__(self, use_service_role: bool = False, db: DatabaseClient = None):
        """
        DocumentProcessor の初期化

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

        self.pipeline = UnifiedDocumentPipeline(db_client=self.db)
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

            # RPC は更新行数を返す
            row_count = result.data if isinstance(result.data, int) else 0
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

            row_count = result.data if isinstance(result.data, int) else 0
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

            row_count = result.data if isinstance(result.data, int) else 0
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
        result = self.db.client.table('Rawdata_FILE_AND_MAIL').select('*').eq('id', doc_id).execute()
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

        doc = self.get_document_by_id(doc_id)
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
                'processing_stage': '開始',
                'processing_progress': 0.0
            }).eq('id', document_id).execute()
        except Exception as e:
            logger.error(f"処理中マークエラー: {e}")

    def _update_document_progress(self, document_id: str, stage: str, progress: float):
        """ドキュメントの進捗を更新"""
        try:
            self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_stage': stage,
                'processing_progress': progress
            }).eq('id', document_id).execute()
            logger.debug(f"進捗更新: {stage} ({progress*100:.0f}%)")

            # StateManagerの進捗も更新
            self.state_manager.update_progress(stage=stage, stage_progress=progress)
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
                    'processing_stage': '完了',
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
                    'processing_stage': 'エラー',
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

                # source_urlまたはsource_idがあれば添付ファイルあり
                has_attachment = doc.get('source_url') or doc.get('source_id')

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
        """テキストのみドキュメントを処理（Stage F v1.1契約対応）"""
        from shared.common.processing.metadata_chunker import MetadataChunker

        document_id = doc['id']
        file_name = doc.get('file_name', 'text_only')
        workspace_to_use = doc.get('workspace', 'unknown') if preserve_workspace else 'unknown'

        display_subject = doc.get('display_subject', '')
        display_post_text = doc.get('display_post_text', '')
        attachment_text = doc.get('attachment_text', '')

        text_parts = []
        if display_subject:
            text_parts.append(f"【件名】\n{display_subject}")
        if display_post_text:
            text_parts.append(f"【本文】\n{display_post_text}")
        if attachment_text:
            text_parts.append(f"【添付ファイル】\n{attachment_text}")

        combined_text = '\n\n'.join(text_parts)

        if not combined_text.strip():
            return {'success': False, 'error': 'テキストが空です'}

        # ============================================
        # Stage F相当: post_body構築（v1.1契約）
        # テキストのみドキュメントでもpost_bodyを処理
        # ============================================
        self._update_document_progress(document_id, 'Stage F: post_body構築', 0.2)
        logger.info(f"[Stage F] テキストのみモード: post_body構築中...")

        # post_body構築
        post_body = {
            "text": display_post_text or "",
            "source": "display_post_text",
            "char_count": len(display_post_text) if display_post_text else 0
        }

        # text_blocks構築（post_bodyを最優先ブロックとして追加）
        text_blocks = []

        # post_bodyブロック（常に先頭）
        if post_body.get("text"):
            text_blocks.append({
                "block_type": "post_body",
                "text": post_body["text"],
                "source": post_body["source"],
                "char_count": post_body["char_count"],
                "priority": "highest"
            })

        # 件名ブロック
        if display_subject:
            text_blocks.append({
                "block_type": "subject",
                "text": display_subject,
                "source": "display_subject",
                "char_count": len(display_subject),
                "priority": "high"
            })

        # 添付テキストブロック
        if attachment_text:
            text_blocks.append({
                "block_type": "attachment",
                "text": attachment_text,
                "source": "attachment_text",
                "char_count": len(attachment_text),
                "priority": "medium"
            })

        # stage_f_structure構築（v1.1契約フォーマット）
        stage_f_structure = {
            "schema_version": "stage_h_input.v1.1",
            "post_body": post_body,
            "full_text": combined_text,
            "text_blocks": text_blocks,
            "tables": [],
            "layout_elements": [],
            "visual_elements": [],
            "warnings": ["F_TEXT_ONLY_MODE: 添付ファイルなし、テキストのみ処理"],
            "_contract_violation": False,
            "_fallback_mode": True,
            "_text_only_mode": True
        }

        logger.info(f"[Stage F完了] post_body: {post_body['char_count']}文字, text_blocks: {len(text_blocks)}個")

        stage_h_config = self.pipeline.config.get_stage_config('stage_h', doc.get('doc_type', 'other'), workspace_to_use)

        # Stage H（stage_f_structure付きで呼び出し）
        self._update_document_progress(document_id, 'Stage H: 構造化', 0.3)
        stageh_result = self.pipeline.stage_h.process(
            file_name=file_name,
            doc_type=doc.get('doc_type', 'unknown'),
            workspace=workspace_to_use,
            combined_text=combined_text,
            prompt=stage_h_config['prompt'],
            model=stage_h_config['model'],
            stage_f_structure=stage_f_structure  # v1.1契約: post_body含む
        )

        if not stageh_result or not isinstance(stageh_result, dict):
            return {'success': False, 'error': 'Stage H失敗: 構造化結果が不正'}

        stageh_metadata = stageh_result.get('metadata', {})
        if stageh_metadata.get('extraction_failed'):
            return {'success': False, 'error': 'Stage H失敗: JSON抽出失敗'}

        document_date = stageh_result.get('document_date')
        tags = stageh_result.get('tags', [])

        # Stage J
        self._update_document_progress(document_id, 'Stage J: チャンク化', 0.6)
        metadata_chunker = MetadataChunker()
        document_data = {
            'file_name': file_name,
            'summary': '',
            'document_date': document_date,
            'tags': tags,
            'doc_type': doc.get('doc_type'),
            'display_subject': display_subject,
            'display_post_text': display_post_text,
            'display_sender': doc.get('display_sender'),
            'display_type': doc.get('display_type'),
            'display_sent_at': doc.get('display_sent_at'),
            'classroom_sender_email': doc.get('classroom_sender_email'),
            'attachment_text': attachment_text,
            'persons': stageh_metadata.get('persons', []) if isinstance(stageh_metadata, dict) else [],
            'organizations': stageh_metadata.get('organizations', []) if isinstance(stageh_metadata, dict) else [],
            'people': stageh_metadata.get('people', []) if isinstance(stageh_metadata, dict) else [],
            'text_blocks': stageh_metadata.get('text_blocks', []) if isinstance(stageh_metadata, dict) else [],
            'structured_tables': stageh_metadata.get('structured_tables', []) if isinstance(stageh_metadata, dict) else [],
            'weekly_schedule': stageh_metadata.get('weekly_schedule', []) if isinstance(stageh_metadata, dict) else [],
            'other_text': stageh_metadata.get('other_text', []) if isinstance(stageh_metadata, dict) else []
        }

        chunks = metadata_chunker.create_metadata_chunks(document_data)

        try:
            self.db.client.table('10_ix_search_index').delete().eq('document_id', document_id).execute()
        except Exception as e:
            logger.warning(f"既存チャンク削除エラー（継続）: {e}")

        # Stage K
        self._update_document_progress(document_id, 'Stage K: Embedding', 0.8)
        stage_k_result = self.pipeline.stage_k.embed_and_save(document_id, chunks)

        if not stage_k_result.get('success'):
            return {'success': False, 'error': f"Stage K失敗: {stage_k_result.get('failed_count', 0)}/{len(chunks)}チャンク保存失敗"}

        failed_count = stage_k_result.get('failed_count', 0)
        if failed_count > 0:
            return {'success': False, 'error': f"Stage K部分失敗: {failed_count}/{len(chunks)}チャンク保存失敗"}

        try:
            self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                'tags': tags,
                'document_date': document_date,
                'metadata': stageh_metadata
            }).eq('id', document_id).execute()
        except Exception as e:
            return {'success': False, 'error': f"ドキュメント更新エラー: {e}"}

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

        # source_urlからファイルIDを抽出（優先）、なければsource_idを使用
        drive_file_id = None
        source_url = doc.get('source_url')
        if source_url:
            # URLからファイルIDを抽出: https://drive.google.com/file/d/{FILE_ID}/view?...
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', source_url)
            if match:
                drive_file_id = match.group(1)
                logger.info(f"[source_url] ファイルID抽出: {drive_file_id}")

        # source_urlからIDが取れなければsource_idにフォールバック
        if not drive_file_id:
            drive_file_id = doc.get('source_id')

        if not drive_file_id:
            return {'success': False, 'error': 'source_urlまたはsource_idがありません'}

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
        self._update_document_progress(document_id, 'ダウンロード中', 0.1)
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

        # パイプライン実行
        self._update_document_progress(document_id, 'Stage E-K: 処理中', 0.3)
        try:
            workspace_to_use = doc.get('workspace', 'unknown') if preserve_workspace else 'unknown'

            result = await self.pipeline.process_document(
                file_path=Path(local_path),
                file_name=file_name,
                doc_type=doc.get('doc_type', 'other'),
                workspace=workspace_to_use,
                mime_type=mime_type,
                source_id=drive_file_id,
                existing_document_id=document_id,
                extra_metadata={
                    'display_subject': doc.get('display_subject'),
                    'display_post_text': doc.get('display_post_text'),
                    'attachment_text': doc.get('attachment_text'),
                    'display_sender': doc.get('display_sender'),
                    'display_sender_email': doc.get('display_sender_email'),
                    'display_type': doc.get('display_type'),
                    'display_sent_at': doc.get('display_sent_at'),
                    'classroom_sender_email': doc.get('classroom_sender_email')
                },
                progress_callback=progress_callback
            )

            return result

        finally:
            # ============================================
            # P0-2: temp削除は最後に1回だけ（doc_temp_dir 全体を削除）
            # ============================================
            import shutil
            if doc_temp_dir.exists():
                try:
                    shutil.rmtree(doc_temp_dir)
                    logger.info(f"[P0] doc固有temp削除完了: {doc_temp_dir}")
                except Exception as cleanup_error:
                    logger.warning(f"[P0] temp削除失敗（無視して続行）: {cleanup_error}")

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
                await asyncio.gather(*active_tasks, return_exceptions=True)
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


    async def _process_dequeued_document(
        self,
        doc: Dict[str, Any],
        preserve_workspace: bool = True
    ) -> bool:
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

                # source_urlまたはsource_idがあれば添付ファイルあり
                has_attachment = doc.get('source_url') or doc.get('source_id')

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
                else:
                    # 【リース方式】nack_document で失敗（retry=False）
                    self._mark_as_failed(document_id, error_msg)
                    self.state_manager.update_progress(error_inc=1)
                    self.state_manager.add_log(f"失敗: {title} - {error_msg}", 'ERROR')
                    logger.error(f"処理失敗: {error_msg}")

                return success

            except Exception as e:
                error_msg = f"処理中エラー: {str(e)}"
                logger.error(error_msg)
                # 【リース方式】nack_document で失敗
                self._mark_as_failed(document_id, error_msg)
                self.state_manager.update_progress(error_inc=1)
                self.state_manager.add_log(f"システムエラー: {title} - {e}", 'ERROR')
                return False


# continuous_processing_loop は削除済み
# 【設計原則】常駐禁止 - バッチ1回実行（Cloud Run Jobs / ローカル両用）
# 代わりに process_queued_documents.py --run-request <id> --execute を使用
