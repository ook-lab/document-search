"""
ドキュメント処理モジュール

DocumentProcessorクラスを提供
StateManager（SSOT）とAdaptiveResourceManagerを使用
"""
import asyncio
import mimetypes
import re
import threading
from typing import List, Dict, Any
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from shared.common.database.client import DatabaseClient
from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.pipeline import UnifiedDocumentPipeline
from .state_manager import StateManager, get_state_manager
from .resource_manager import AdaptiveResourceManager, get_cgroup_memory, get_cgroup_cpu


class DocumentProcessor:
    """
    ドキュメント処理クラス

    StateManagerを通じて状態を一元管理
    AdaptiveResourceManagerで並列数を動的調整
    """

    VIDEO_EXTENSIONS = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.mpeg', '.mpg']

    def __init__(self):
        self.db = DatabaseClient()
        self.pipeline = UnifiedDocumentPipeline(db_client=self.db)
        self.drive = GoogleDriveConnector()
        self.temp_dir = Path("./temp")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # StateManagerを取得（SSOT）
        self.state_manager = get_state_manager()

        # リソースマネージャー（インスタンス毎に作成）
        self.resource_manager = None

    def get_pending_documents(self, workspace: str = 'all', limit: int = 100) -> List[Dict[str, Any]]:
        """processing_status='pending' のドキュメントを取得"""
        query = self.db.client.table('Rawdata_FILE_AND_MAIL').select('*').eq('processing_status', 'pending')

        if workspace != 'all':
            query = query.eq('workspace', workspace)

        result = query.limit(limit).execute()
        return result.data if result.data else []

    def get_queue_stats(self, workspace: str = 'all') -> Dict[str, int]:
        """統計情報を取得"""
        try:
            query = self.db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status, workspace')

            if workspace != 'all':
                query = query.eq('workspace', workspace)

            response = query.execute()

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
        """完了にマーク"""
        try:
            self.db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'completed',
                'processing_stage': '完了',
                'processing_progress': 1.0
            }).eq('id', document_id).execute()
        except Exception as e:
            logger.error(f"完了マークエラー: {e}")

    def _mark_as_failed(self, document_id: str, error_message: str = ""):
        """エラーにマーク"""
        try:
            update_data = {
                'processing_status': 'failed',
                'processing_stage': 'エラー',
                'processing_progress': 0.0
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
        except Exception as e:
            logger.error(f"失敗マークエラー: {e}")

    async def process_document(
        self,
        doc: Dict[str, Any],
        preserve_workspace: bool = True,
        progress_callback=None
    ) -> bool:
        """単一ドキュメントを処理"""
        document_id = doc['id']
        file_name = doc.get('file_name', 'unknown')
        title = doc.get('title', '') or '(タイトル未生成)'

        completed_or_failed = False

        # StateManagerの進捗を更新
        self.state_manager.update_progress(filename=title, stage='開始')
        self.state_manager.add_log(f"処理開始: {title}")

        try:
            self._mark_as_processing(document_id)

            drive_file_id = doc.get('source_id')

            if drive_file_id:
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
            else:
                self._mark_as_failed(document_id, error_msg)
                completed_or_failed = True
                self.state_manager.update_progress(error_inc=1)
                self.state_manager.add_log(f"失敗: {title} - {error_msg}", 'ERROR')

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
        """テキストのみドキュメントを処理"""
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

        stage_h_config = self.pipeline.config.get_stage_config('stage_h', doc.get('doc_type', 'other'), workspace_to_use)

        # Stage H
        self._update_document_progress(document_id, 'Stage H: 構造化', 0.3)
        stageh_result = self.pipeline.stage_h.process(
            file_name=file_name,
            doc_type=doc.get('doc_type', 'unknown'),
            workspace=workspace_to_use,
            combined_text=combined_text,
            prompt=stage_h_config['prompt'],
            model=stage_h_config['model']
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
        drive_file_id = doc.get('source_id')

        if not drive_file_id:
            return {'success': False, 'error': 'source_idがありません'}

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

        # ダウンロード
        self._update_document_progress(document_id, 'ダウンロード中', 0.1)
        try:
            self.drive.download_file(download_file_id, download_file_name, str(self.temp_dir))
            local_path = self.temp_dir / download_file_name
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
            if local_path.exists():
                local_path.unlink()
                logger.debug(f"一時ファイル削除: {local_path}")

    async def run_batch(
        self,
        workspace: str = 'all',
        limit: int = 100,
        preserve_workspace: bool = True
    ):
        """
        バッチ処理のメインループ

        StateManagerを通じて状態を一元管理
        """
        # pendingドキュメントを取得
        docs = self.get_pending_documents(workspace, limit)

        if not docs:
            self.state_manager.add_log("処理対象のドキュメントがありません")
            return

        # StateManagerで処理開始（ロック取得）
        if not self.state_manager.start_processing(len(docs)):
            logger.warning("処理開始に失敗しました（既に処理中か、ロック取得失敗）")
            return

        # リソースマネージャーを初期化
        self.resource_manager = AdaptiveResourceManager(
            initial_max_parallel=2,
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

        try:
            for i, doc in enumerate(docs, 1):
                # 停止リクエストをチェック
                if self.state_manager.is_stop_requested():
                    self.state_manager.add_log("停止リクエストにより処理を中断します", 'WARNING')
                    break

                # リソース監視と調整
                memory_info = get_cgroup_memory()
                res_status = self.resource_manager.adjust_resources(
                    memory_info['percent'],
                    len(active_tasks)
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

                # 並列数制限
                while len(active_tasks) >= res_status['max_parallel']:
                    done, active_tasks = await asyncio.wait(
                        active_tasks,
                        return_when=asyncio.FIRST_COMPLETED
                    )

                # 進捗更新
                self.state_manager.update_progress(index=i)

                # タスク追加
                task = asyncio.create_task(
                    self.process_document(doc, preserve_workspace)
                )
                active_tasks.add(task)

                # イベントループに制御を渡す
                await asyncio.sleep(0)

            # 残りのタスクを待機
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)

        finally:
            # タイマー停止
            if sync_timer:
                sync_timer.cancel()

            # 処理終了
            self.state_manager.finish_processing()


async def continuous_processing_loop():
    """継続的な処理ループ（自動処理用）"""
    processor = DocumentProcessor()
    state_manager = get_state_manager()

    logger.info("自動処理ループを開始します")

    while True:
        try:
            if state_manager.is_stop_requested():
                logger.info("停止リクエストにより自動処理ループを終了します")
                break

            docs = processor.get_pending_documents(workspace='all', limit=10)

            if docs:
                await processor.run_batch(workspace='all', limit=10)
            else:
                logger.debug("処理対象のドキュメントがありません（5秒後に再チェック）")

            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"処理ループでエラー: {e}")
            state_manager.reset()
            await asyncio.sleep(10)
