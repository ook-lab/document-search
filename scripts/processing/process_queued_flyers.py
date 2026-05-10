"""
チラシ処理パイプライン

Rawdata_FLYER_shopsテーブルの processing_status='pending' のチラシを処理

処理フロー:
1. Pre-processing: 画像ファイルダウンロード
2. Stage E-K (Unified Pipeline): 統合パイプラインで処理
   - Stage F: Vision解析（OCR・レイアウト・商品抽出）
   - Stage H: 構造化データ生成
   - Stage I: 統合・要約
   - Stage J-K: チャンク化・ベクトル化・保存

使い方:
    # 全てのpendingチラシを処理（デフォルト10件）
    python process_queued_flyers.py

    # 処理件数を指定
    python process_queued_flyers.py --limit=50

    # 特定の店舗のみ処理
    python process_queued_flyers.py --store="フーディアム 武蔵小杉"

    # ドライラン（確認のみ）
    python process_queued_flyers.py --dry-run
"""

import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger
import hashlib

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "services" / "data-ingestion"))

from dms.common.database.client import DatabaseClient
from dms.common.connectors.google_drive import GoogleDriveConnector
from dms.pipeline import UnifiedDocumentPipeline


class FlyerProcessor:
    """チラシ処理パイプライン"""

    def __init__(self, temp_dir: str = "./temp"):
        """
        Args:
            temp_dir: 一時ファイル保存ディレクトリ
        """
        self.db = DatabaseClient()
        self.drive = GoogleDriveConnector()

        # 統合パイプラインを初期化
        self.pipeline = UnifiedDocumentPipeline(db_client=self.db)

        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        logger.info("FlyerProcessor初期化完了（G_unified_pipeline使用）")

    def get_pending_flyers(
        self,
        limit: int = 10,
        store_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        処理待ちのチラシを取得

        Args:
            limit: 取得件数
            store_name: 店舗名（指定された場合のみ）

        Returns:
            チラシ情報のリスト
        """
        try:
            query = self.db.client.table('Rawdata_FLYER_shops').select('*').eq(
                'processing_status', 'pending'
            )

            if store_name:
                query = query.eq('organization', store_name)

            result = query.limit(limit).execute()

            if result.data:
                logger.info(f"処理待ちチラシ: {len(result.data)}件")
                return result.data

            return []

        except Exception as e:
            logger.error(f"チラシ取得エラー: {e}")
            return []

    async def process_single_flyer(
        self,
        flyer_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        1件のチラシを処理（G_unified_pipeline使用）

        Args:
            flyer_doc: チラシドキュメント情報

        Returns:
            処理結果
        """
        flyer_doc_id = flyer_doc['id']
        file_name = flyer_doc.get('file_name') or ''
        source_id = flyer_doc.get('source_id')  # Google Drive ID
        organization = flyer_doc.get('organization') or ''

        logger.info(f"\n{'='*80}")
        logger.info(f"チラシ処理開始: {file_name}")
        logger.info(f"  店舗: {organization}")
        logger.info(f"  ID: {flyer_doc_id}")
        logger.info(f"{'='*80}")

        result = {
            'flyer_doc_id': flyer_doc_id,
            'file_name': file_name,
            'success': False,
            'products_count': 0,
            'chunks_count': 0,
            'error': None
        }

        local_path = None

        try:
            # ステータスを 'processing' に更新
            self._update_status(flyer_doc_id, 'processing')

            # ============================================
            # Pre-processing: ファイルダウンロード
            # ============================================
            logger.info("[Pre-processing] 画像ダウンロード中...")
            local_path = self.drive.download_file(source_id, file_name, self.temp_dir)

            if not local_path or not Path(local_path).exists():
                raise Exception("画像ダウンロード失敗")

            logger.info(f"  ダウンロード完了: {local_path}")

            # ============================================
            # G_unified_pipeline で処理
            # ============================================
            logger.info("[G_unified_pipeline] チラシ処理開始...")

            # doc_type="flyer" で統合パイプラインを実行
            # → config/prompts/stage_f/flyer.md
            # → config/prompts/stage_g/flyer.md
            # → config/prompts/stage_h/flyer.md
            # → config/prompts/stage_i/flyer.md
            pipeline_result = await self.pipeline.process_document(
                file_path=Path(local_path),
                file_name=file_name,
                doc_type='flyer',  # ← これで自動的にチラシ用プロンプト・モデルが選択される
                workspace='shopping',
                mime_type='image/jpeg',  # チラシは通常JPEG
                source_id=source_id,
                extra_metadata={
                    'organization': organization,
                    'flyer_title': flyer_doc.get('flyer_title'),
                    'flyer_period': flyer_doc.get('flyer_period'),
                    'page_number': flyer_doc.get('page_number'),
                    'flyer_doc_id': flyer_doc_id
                }
            )

            if not pipeline_result.get('success'):
                raise Exception(f"パイプライン処理失敗: {pipeline_result.get('error')}")

            document_id = pipeline_result['document_id']
            chunks_count = pipeline_result.get('chunks_count', 0)

            logger.info(f"[G_unified_pipeline完了] document_id={document_id}, chunks={chunks_count}")

            # ============================================
            # 成功
            # ============================================
            self._update_status(flyer_doc_id, 'completed')

            result.update({
                'success': True,
                'document_id': document_id,
                'chunks_count': chunks_count
            })

            logger.info(f"✅ チラシ処理成功: {file_name}")
            return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ チラシ処理エラー: {error_msg}", exc_info=True)

            self._update_status(flyer_doc_id, 'error', error_message=error_msg)

            result['error'] = error_msg
            return result

        finally:
            # 一時ファイル削除
            if local_path and Path(local_path).exists():
                try:
                    Path(local_path).unlink()
                    logger.debug(f"一時ファイル削除: {local_path}")
                except Exception as e:
                    logger.warning(f"一時ファイル削除失敗: {e}")

    async def _save_products(
        self,
        flyer_doc_id: str,
        products: List[Dict[str, Any]],
        page_number: int
    ) -> int:
        """
        商品情報をRawdata_FLYER_itemsテーブルに保存

        Args:
            flyer_doc_id: チラシドキュメントID
            products: 商品リスト
            page_number: ページ番号

        Returns:
            保存成功件数
        """
        success_count = 0

        for product in products:
            try:
                # 商品名の正規化（検索用）
                product_name = product.get('product_name', '')
                product_name_normalized = product_name.lower().strip()

                product_data = {
                    'flyer_document_id': flyer_doc_id,
                    'product_name': product_name,
                    'product_name_normalized': product_name_normalized,
                    'price': product.get('price'),
                    'original_price': product.get('original_price'),
                    'discount_rate': product.get('discount_rate'),
                    'price_unit': product.get('price_unit', '円'),
                    'price_text': product.get('price_text'),
                    'category': product.get('category', 'その他'),
                    'subcategory': product.get('subcategory'),
                    'brand': product.get('brand'),
                    'quantity': product.get('quantity'),
                    'origin': product.get('origin'),
                    'is_special_offer': product.get('is_special_offer', False),
                    'offer_type': product.get('offer_type'),
                    'page_number': page_number,
                    'extracted_text': product.get('extracted_text'),
                    'confidence': product.get('confidence', 0.5),
                    'metadata': {
                        'extraction_date': datetime.now().isoformat(),
                        'extraction_model': 'gemini-2.0-flash-exp'
                    }
                }

                result = await self.db.insert_document('Rawdata_FLYER_items', product_data)
                if result:
                    success_count += 1

            except Exception as e:
                logger.error(f"商品保存エラー: {e}")
                logger.debug(f"商品データ: {product}")

        return success_count

    def _update_status(
        self,
        flyer_doc_id: str,
        status: str,
        error: str = None
    ):
        """
        チラシの処理ステータスを更新

        Args:
            flyer_doc_id: チラシドキュメントID
            status: ステータス（processing, completed, failed）
            error: エラーメッセージ
        """
        try:
            update_data = {
                'processing_status': status,
                'updated_at': datetime.now().isoformat()
            }

            if error:
                update_data['processing_error'] = error

            self.db.client.table('Rawdata_FLYER_shops').update(update_data).eq(
                'id', flyer_doc_id
            ).execute()

        except Exception as e:
            logger.error(f"ステータス更新エラー: {e}")

    async def process_pending_flyers(
        self,
        limit: int = 10,
        store_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        処理待ちのチラシを一括処理

        Args:
            limit: 処理件数
            store_name: 店舗名（指定された場合のみ）

        Returns:
            処理結果のサマリー
        """
        logger.info("=" * 80)
        logger.info("チラシ処理パイプライン開始")
        logger.info("=" * 80)

        # 処理待ちチラシを取得
        pending_flyers = self.get_pending_flyers(limit, store_name)

        if not pending_flyers:
            logger.info("処理待ちのチラシはありません")
            return {'total': 0, 'success': 0, 'failed': 0, 'total_products': 0, 'total_chunks': 0}

        logger.info(f"処理対象: {len(pending_flyers)}件")

        results = []
        for i, flyer in enumerate(pending_flyers, 1):
            logger.info(f"\n[{i}/{len(pending_flyers)}] 処理中...")
            result = await self.process_single_flyer(flyer)
            results.append(result)

        # サマリー
        success_count = sum(1 for r in results if r['success'])
        failed_count = len(results) - success_count
        total_products = sum(r.get('products_count', 0) for r in results)
        total_chunks = sum(r.get('chunks_count', 0) for r in results)

        logger.info("\n" + "=" * 80)
        logger.info("処理完了")
        logger.info(f"  成功: {success_count}/{len(results)}")
        logger.info(f"  失敗: {failed_count}/{len(results)}")
        logger.info(f"  抽出商品数: {total_products}件")
        logger.info(f"  生成チャンク数: {total_chunks}個")
        logger.info("=" * 80)

        return {
            'total': len(results),
            'success': success_count,
            'failed': failed_count,
            'total_products': total_products,
            'total_chunks': total_chunks,
            'results': results
        }


async def main():
    """メインエントリーポイント"""
    # コマンドライン引数のパース
    dry_run = '--dry-run' in sys.argv
    limit = 10
    store_name = None

    for arg in sys.argv:
        if arg.startswith('--limit='):
            try:
                limit = int(arg.split('=')[1])
            except:
                pass
        elif arg.startswith('--store='):
            store_name = arg.split('=')[1]

    processor = FlyerProcessor()

    if dry_run:
        logger.info("🔍 DRY RUN モード: 実際の処理は行いません")
        pending = processor.get_pending_flyers(limit, store_name)
        logger.info(f"処理対象: {len(pending)}件")
        for flyer in pending:
            logger.info(f"  - {flyer.get('organization')}: {flyer.get('file_name')}")
        return

    result = await processor.process_pending_flyers(limit, store_name)

    # 結果を表示
    print("\n" + "=" * 80)
    print("🛒 チラシ処理結果")
    print("=" * 80)
    print(f"処理件数: {result['total']}")
    print(f"成功: {result['success']}")
    print(f"失敗: {result['failed']}")
    print(f"抽出商品数: {result['total_products']}")
    print(f"生成チャンク数: {result['total_chunks']}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
