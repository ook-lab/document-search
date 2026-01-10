"""
既存レシートを削除してGoogle Driveから再取り込み

処理フロー:
1. データベースから既存レシートを削除（CASCADE削除で子・孫も削除）
2. Google Drive から画像を再取得
3. 統合パイプラインで再処理
"""

import asyncio
from pathlib import Path
from typing import List, Dict
from loguru import logger

from shared.common.database.client import DatabaseClient
from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.pipeline import UnifiedDocumentPipeline
from shared.kakeibo.config import INBOX_EASY_FOLDER_ID, INBOX_HARD_FOLDER_ID, TEMP_DIR


class ReceiptReimporter:
    """レシート削除 & 再取り込み"""

    def __init__(self):
        self.db = DatabaseClient(use_service_role=True)
        self.drive = GoogleDriveConnector()
        self.pipeline = UnifiedDocumentPipeline()
        self.temp_dir = Path(TEMP_DIR)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def get_all_receipts(self) -> List[Dict]:
        """データベースから全レシートを取得"""
        result = self.db.client.table("Rawdata_RECEIPT_shops").select("*").order("created_at", desc=True).execute()
        return result.data

    def delete_receipt(self, receipt_id: str) -> bool:
        """
        レシートを削除（CASCADE削除で子・孫も自動削除）

        Args:
            receipt_id: レシートID

        Returns:
            削除成功したかどうか
        """
        try:
            # 削除前の情報を取得
            receipt = self.db.client.table("Rawdata_RECEIPT_shops").select("*").eq("id", receipt_id).execute()
            if not receipt.data:
                logger.warning(f"レシートが見つかりません: {receipt_id}")
                return False

            receipt_info = receipt.data[0]
            logger.info(f"削除対象: {receipt_info.get('shop_name')} - {receipt_info.get('transaction_date')}")

            # レシートを削除（CASCADE削除により子・孫も削除される）
            self.db.client.table("Rawdata_RECEIPT_shops").delete().eq("id", receipt_id).execute()

            # ログテーブルからも削除
            self.db.client.table("99_lg_image_proc_log").delete().eq("receipt_id", receipt_id).execute()

            logger.success(f"✅ 削除完了: {receipt_id}")
            return True

        except Exception as e:
            logger.error(f"削除エラー: {receipt_id} - {e}")
            return False

    def list_receipt_images(self, folder_id: str) -> List[Dict]:
        """Google Drive フォルダから画像ファイル一覧を取得"""
        try:
            query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png' or mimeType='image/heic') and trashed=false"
            files = self.drive.service.files().list(
                q=query,
                fields="files(id, name, mimeType, createdTime, modifiedTime)",
                orderBy="createdTime desc",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute()

            return files.get('files', [])

        except Exception as e:
            logger.error(f"ファイル一覧取得エラー: {e}")
            return []

    async def process_receipt_image(
        self,
        file_id: str,
        file_name: str,
        mime_type: str,
        source_folder: str
    ) -> Dict:
        """レシート画像を処理"""
        local_path = None
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"📄 処理開始: {file_name}")
            logger.info(f"  ファイルID: {file_id}")
            logger.info(f"  ソース: {source_folder}")

            # 既存のログエントリを削除（重複エラー防止）
            try:
                self.db.client.table("99_lg_image_proc_log").delete().eq("file_name", file_name).execute()
            except Exception:
                pass  # ログエントリが存在しない場合は無視

            # ファイルをダウンロード
            local_path = self.drive.download_file(file_id, file_name, self.temp_dir)
            logger.info(f"  ダウンロード完了: {local_path}")

            # 統合パイプラインで処理
            result = await self.pipeline.process_document(
                file_path=Path(local_path),
                file_name=file_name,
                doc_type='kakeibo',
                workspace='household',
                mime_type=mime_type,
                source_id=file_id,
                existing_document_id=None,
                extra_metadata={'source_folder': source_folder}
            )

            if result.get('success'):
                logger.success(f"✅ 処理成功: {file_name}")
                logger.info(f"  receipt_id: {result.get('receipt_id')}")
                logger.info(f"  transaction_ids: {len(result.get('transaction_ids', []))}件")
                return {'success': True, 'file_name': file_name, 'result': result}
            else:
                error_msg = result.get('error', 'unknown error')
                logger.error(f"❌ 処理失敗: {file_name} - {error_msg}")
                return {'success': False, 'file_name': file_name, 'error': error_msg}

        except Exception as e:
            logger.error(f"❌ 処理エラー: {file_name} - {e}")
            logger.exception(e)
            return {'success': False, 'file_name': file_name, 'error': str(e)}

        finally:
            if local_path and Path(local_path).exists():
                Path(local_path).unlink()

    async def delete_and_reimport_all(self, limit: int = 100):
        """既存レシートを削除して再取り込み"""
        logger.info("\n" + "="*80)
        logger.info("既存レシート削除 & 再取り込み開始")
        logger.info("="*80)

        # 1. 既存レシートを全て削除
        logger.info("\n🗑️  既存レシート削除中...")
        existing_receipts = self.get_all_receipts()
        logger.info(f"  削除対象: {len(existing_receipts)}件")

        deleted_count = 0
        for receipt in existing_receipts:
            if self.delete_receipt(receipt['id']):
                deleted_count += 1

        logger.info(f"  削除完了: {deleted_count}件")

        # 2. Google Driveから再取り込み
        stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'easy': 0,
            'hard': 0
        }

        # Inbox Easy から取得
        if INBOX_EASY_FOLDER_ID:
            logger.info(f"\n📂 Inbox Easy フォルダから取得中...")
            easy_files = self.list_receipt_images(INBOX_EASY_FOLDER_ID)
            logger.info(f"  → {len(easy_files)}件の画像ファイルを検出")

            for i, file_info in enumerate(easy_files[:limit]):
                stats['total'] += 1
                stats['easy'] += 1

                result = await self.process_receipt_image(
                    file_id=file_info['id'],
                    file_name=file_info['name'],
                    mime_type=file_info['mimeType'],
                    source_folder='inbox_easy'
                )

                if result['success']:
                    stats['success'] += 1
                else:
                    stats['failed'] += 1

                logger.info(f"\n進捗: {i+1}/{len(easy_files[:limit])} (Easy)")

        # Inbox Hard から取得
        if INBOX_HARD_FOLDER_ID:
            logger.info(f"\n📂 Inbox Hard フォルダから取得中...")
            hard_files = self.list_receipt_images(INBOX_HARD_FOLDER_ID)
            logger.info(f"  → {len(hard_files)}件の画像ファイルを検出")

            for i, file_info in enumerate(hard_files[:limit]):
                stats['total'] += 1
                stats['hard'] += 1

                result = await self.process_receipt_image(
                    file_id=file_info['id'],
                    file_name=file_info['name'],
                    mime_type=file_info['mimeType'],
                    source_folder='inbox_hard'
                )

                if result['success']:
                    stats['success'] += 1
                else:
                    stats['failed'] += 1

                logger.info(f"\n進捗: {i+1}/{len(hard_files[:limit])} (Hard)")

        # 最終結果
        logger.info("\n" + "="*80)
        logger.info("削除 & 再取り込み完了")
        logger.info("="*80)
        logger.info(f"削除:   {deleted_count}件")
        logger.info(f"合計:   {stats['total']}件")
        logger.info(f"成功:   {stats['success']}件")
        logger.info(f"失敗:   {stats['failed']}件")
        logger.info(f"Easy:   {stats['easy']}件")
        logger.info(f"Hard:   {stats['hard']}件")
        logger.info("="*80)


async def main():
    """メイン処理"""
    import sys

    # --limit オプションの処理
    limit = 100
    for arg in sys.argv:
        if arg.startswith('--limit='):
            try:
                limit = int(arg.split('=')[1])
            except:
                pass

    reimporter = ReceiptReimporter()
    await reimporter.delete_and_reimport_all(limit=limit)


if __name__ == "__main__":
    asyncio.run(main())
