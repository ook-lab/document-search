"""
チラシ画像処理パイプライン

Gemini 2.5 Pro Visionを使用してチラシ画像から商品情報を抽出し、
Rawdata_FLYER_itemsテーブルに保存する。

処理フロー:
1. Rawdata_FLYER_shops から processing_status='pending' のチラシを取得
2. Gemini 2.5 Pro Vision でチラシ画像から商品情報を抽出
3. Rawdata_FLYER_items テーブルに商品データを保存
4. Rawdata_FLYER_shops の processing_status を 'completed' に更新
"""
import os
import sys
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger
import traceback

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.common.database.client import DatabaseClient
from shared.ai.llm_client.llm_client import LLMClient


# 商品抽出用のプロンプトスキーマ
PRODUCT_EXTRACTION_PROMPT = """
あなたはスーパーマーケットのチラシから商品情報を抽出する専門家です。

チラシ画像から以下の情報を含む商品リストをJSON形式で抽出してください：

- product_name: 商品名（必須）
- price: 価格（数値、単位なし）
- original_price: 元の価格（割引前、ある場合のみ）
- discount_rate: 割引率（%、ある場合のみ）
- price_unit: 価格の単位（例: "円", "円/100g"）
- price_text: 価格の元のテキスト（例: "298円", "特価"）
- category: カテゴリ（野菜、肉、魚、日用品、飲料、冷凍食品、菓子、調味料、その他）
- brand: ブランド名（ある場合のみ）
- quantity: 数量・容量（例: "100g", "1パック", "500ml"）
- origin: 産地（ある場合のみ）
- is_special_offer: 特売品かどうか（true/false）
- offer_type: 特売タイプ（タイムセール、日替わり、週末限定など、ある場合のみ）
- extracted_text: この商品に関する元のテキスト
- confidence: 抽出の信頼度（0.0〜1.0）

**重要な注意事項:**
1. すべての商品を漏れなく抽出してください
2. 価格は数値のみ抽出（例: "298円" → 298）
3. カテゴリは上記のいずれかに分類
4. 商品名は正確に抽出（ブランド名を含む）
5. 特売・セール品は is_special_offer を true に設定
6. 情報が不明な場合は null を設定

**出力形式:**
```json
{
  "products": [
    {
      "product_name": "国産キャベツ",
      "price": 98,
      "price_unit": "円",
      "price_text": "98円",
      "category": "野菜",
      "quantity": "1玉",
      "origin": "国産",
      "is_special_offer": true,
      "offer_type": "日替わり",
      "extracted_text": "国産キャベツ 1玉 98円 日替わり特価",
      "confidence": 0.95
    }
  ],
  "total_products": 1
}
```

チラシ情報:
- 店舗: {store_name}
- タイトル: {flyer_title}
- 期間: {flyer_period}
- ページ: {page_number}

それでは、画像から商品情報を抽出してください。
"""


class FlyerProcessor:
    """チラシ画像処理プロセッサー"""

    def __init__(self, temp_dir: str = "./temp"):
        """
        Args:
            temp_dir: 一時ファイル保存ディレクトリ
        """
        self.llm_client = LLMClient()
        self.drive = GoogleDriveConnector()
        self.db = DatabaseClient()

        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        logger.info("FlyerProcessor初期化完了")

    async def get_pending_flyers(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        処理待ちのチラシを取得

        Args:
            limit: 取得件数

        Returns:
            チラシ情報のリスト
        """
        try:
            result = self.db.client.table('Rawdata_FLYER_shops').select('*').eq(
                'processing_status', 'pending'
            ).limit(limit).execute()

            if result.data:
                logger.info(f"処理待ちチラシ: {len(result.data)}件")
                return result.data

            return []

        except Exception as e:
            logger.error(f"チラシ取得エラー: {e}")
            return []

    async def extract_products_from_image(
        self,
        flyer_doc: Dict[str, Any],
        image_path: str
    ) -> Optional[Dict[str, Any]]:
        """
        Gemini 2.5 Pro Visionでチラシ画像から商品情報を抽出

        Args:
            flyer_doc: チラシドキュメント情報
            image_path: ローカル画像パス

        Returns:
            抽出結果 {'products': [...], 'total_products': N}
        """
        try:
            # プロンプト生成
            prompt = PRODUCT_EXTRACTION_PROMPT.format(
                store_name=flyer_doc.get('organization', '不明'),
                flyer_title=flyer_doc.get('flyer_title', '不明'),
                flyer_period=flyer_doc.get('flyer_period', '不明'),
                page_number=flyer_doc.get('page_number', 1)
            )

            logger.info(f"Gemini Vision で商品抽出開始: {flyer_doc.get('file_name')}")

            # Gemini 2.5 Pro Vision で画像を処理
            result = await self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=image_path,
                model="gemini-2.0-flash-exp",  # Gemini 2.5 Pro Vision
                response_format="json",
                log_context={'app': 'tokubai', 'stage': 'flyer-extract'}
            )

            # JSONパース
            try:
                products_data = json.loads(result)
                logger.info(f"商品抽出完了: {products_data.get('total_products', 0)}件")
                return products_data
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON パースエラー: {json_err}")
                logger.debug(f"レスポンス: {result[:500]}")
                return None

        except Exception as e:
            logger.error(f"商品抽出エラー: {e}", exc_info=True)
            return None

    async def save_products_to_db(
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
                # 商品名を取得
                product_name = product.get('product_name', '')

                # カテゴリの正規化
                category_map = {
                    '野菜': '野菜',
                    '果物': '果物',
                    '肉': '肉',
                    '魚': '魚',
                    '日用品': '日用品',
                    '飲料': '飲料',
                    '冷凍食品': '冷凍食品',
                    '菓子': '菓子',
                    '調味料': '調味料',
                }
                category = category_map.get(product.get('category', 'その他'), 'その他')

                product_data = {
                    'flyer_document_id': flyer_doc_id,
                    'product_name': product_name,
                    'price': product.get('price'),
                    'original_price': product.get('original_price'),
                    'discount_rate': product.get('discount_rate'),
                    'price_unit': product.get('price_unit', '円'),
                    'price_text': product.get('price_text'),
                    'category': category,
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
                        'extraction_model': 'gemini-2.5-pro-vision'
                    }
                }

                result = await self.db.insert_document('Rawdata_FLYER_items', product_data)
                if result:
                    success_count += 1
                    logger.debug(f"商品保存成功: {product_name}")

            except Exception as e:
                logger.error(f"商品保存エラー: {e}")
                logger.debug(f"商品データ: {product}")

        logger.info(f"商品保存完了: {success_count}/{len(products)}件")
        return success_count

    async def update_flyer_status(
        self,
        flyer_doc_id: str,
        status: str,
        attachment_text: str = None,
        error: str = None
    ):
        """
        チラシの処理ステータスを更新

        Args:
            flyer_doc_id: チラシドキュメントID
            status: ステータス（completed, failed）
            attachment_text: 抽出したテキスト
            error: エラーメッセージ
        """
        try:
            update_data = {
                'processing_status': status,
                'updated_at': datetime.now().isoformat()
            }

            if attachment_text:
                update_data['attachment_text'] = attachment_text

            if error:
                update_data['processing_error'] = error

            if status == 'completed':
                update_data['processing_stage'] = 'products_extracted'

            self.db.client.table('Rawdata_FLYER_shops').update(update_data).eq(
                'id', flyer_doc_id
            ).execute()

            logger.info(f"チラシステータス更新: {status}")

        except Exception as e:
            logger.error(f"ステータス更新エラー: {e}")

    async def process_single_flyer(self, flyer_doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        1件のチラシを処理

        Args:
            flyer_doc: チラシドキュメント情報

        Returns:
            処理結果
        """
        flyer_doc_id = flyer_doc['id']
        file_name = flyer_doc.get('file_name', '不明')
        source_id = flyer_doc.get('source_id')  # Google Drive ID

        logger.info(f"=== チラシ処理開始: {file_name} ===")

        result = {
            'flyer_doc_id': flyer_doc_id,
            'file_name': file_name,
            'success': False,
            'products_count': 0,
            'error': None
        }

        local_path = None

        try:
            # 1. Google Driveから画像をダウンロード
            logger.info("画像ダウンロード中...")
            local_path = self.drive.download_file(source_id, file_name, self.temp_dir)

            if not local_path or not Path(local_path).exists():
                raise Exception("画像ダウンロード失敗")

            # 2. Gemini Vision で商品情報を抽出
            products_data = await self.extract_products_from_image(flyer_doc, local_path)

            if not products_data or not products_data.get('products'):
                logger.warning("商品が抽出できませんでした")
                await self.update_flyer_status(flyer_doc_id, 'completed', attachment_text="商品情報なし")
                result['success'] = True
                return result

            # 3. 商品をDBに保存
            products = products_data['products']
            page_number = flyer_doc.get('page_number', 1)

            saved_count = await self.save_products_to_db(flyer_doc_id, products, page_number)

            # 4. チラシのステータスを更新
            # 抽出したテキストをまとめる
            all_texts = [p.get('extracted_text', '') for p in products]
            attachment_text = '\n'.join(filter(None, all_texts))

            await self.update_flyer_status(flyer_doc_id, 'completed', attachment_text=attachment_text)

            result['success'] = True
            result['products_count'] = saved_count
            logger.info(f"=== チラシ処理完了: {file_name} ({saved_count}件の商品) ===")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"チラシ処理エラー: {error_msg}", exc_info=True)

            await self.update_flyer_status(flyer_doc_id, 'failed', error=error_msg)
            result['error'] = error_msg

        finally:
            # 一時ファイル削除
            if local_path and Path(local_path).exists():
                Path(local_path).unlink()
                logger.debug(f"一時ファイル削除: {local_path}")

        return result

    async def process_pending_flyers(self, limit: int = 10) -> Dict[str, Any]:
        """
        処理待ちのチラシを一括処理

        Args:
            limit: 処理件数

        Returns:
            処理結果のサマリー
        """
        logger.info("=" * 60)
        logger.info("チラシ処理パイプライン開始")
        logger.info("=" * 60)

        # 処理待ちチラシを取得
        pending_flyers = await self.get_pending_flyers(limit)

        if not pending_flyers:
            logger.info("処理待ちのチラシはありません")
            return {'total': 0, 'success': 0, 'failed': 0}

        logger.info(f"処理対象: {len(pending_flyers)}件")

        results = []
        for i, flyer in enumerate(pending_flyers, 1):
            logger.info(f"[{i}/{len(pending_flyers)}] 処理中: {flyer.get('file_name')}")
            result = await self.process_single_flyer(flyer)
            results.append(result)

        # サマリー
        success_count = sum(1 for r in results if r['success'])
        failed_count = len(results) - success_count
        total_products = sum(r.get('products_count', 0) for r in results)

        logger.info("=" * 60)
        logger.info("処理完了")
        logger.info(f"  成功: {success_count}/{len(results)}")
        logger.info(f"  失敗: {failed_count}/{len(results)}")
        logger.info(f"  抽出商品数: {total_products}件")
        logger.info("=" * 60)

        return {
            'total': len(results),
            'success': success_count,
            'failed': failed_count,
            'total_products': total_products,
            'results': results
        }


async def main():
    """メインエントリーポイント"""
    processor = FlyerProcessor()
    result = await processor.process_pending_flyers(limit=100)

    # 結果を表示
    print("\n" + "=" * 80)
    print("🛒 チラシ商品抽出結果")
    print("=" * 80)
    print(f"処理件数: {result['total']}")
    print(f"成功: {result['success']}")
    print(f"失敗: {result['failed']}")
    print(f"抽出商品数: {result['total_products']}")
    print("=" * 80)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
