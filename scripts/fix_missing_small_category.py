"""
small_categoryが欠けている商品を修正

general_nameはあるがsmall_categoryがない商品に対して、
Gemini 2.5 Flashでsmall_categoryとkeywordsを生成
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.common.database.client import DatabaseClient
from shared.ai.llm_client.llm_client import LLMClient
import logging

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SmallCategoryFixer:
    """small_category修正クラス"""

    def __init__(self):
        self.db = DatabaseClient(use_service_role=True)
        self.llm_client = LLMClient()

    async def fetch_products_missing_small_category(self, limit: int = None) -> List[Dict]:
        """
        general_nameはあるがsmall_categoryがない商品を取得

        Args:
            limit: 取得件数

        Returns:
            商品リスト
        """
        logger.info("small_categoryが欠けている商品を取得中...")

        query = self.db.client.table('Rawdata_NETSUPER_items').select(
            'id, product_name, general_name, keywords'
        ).not_.is_('general_name', 'null').is_('small_category', 'null')

        if limit:
            query = query.limit(limit)

        result = query.execute()

        logger.info(f"対象商品: {len(result.data)}件")
        return result.data

    async def generate_small_category_and_keywords(
        self,
        product_name: str,
        general_name: str,
        existing_keywords: List[str] = None
    ) -> Dict:
        """
        small_categoryとkeywordsを生成

        Args:
            product_name: 商品名
            general_name: 一般名詞
            existing_keywords: 既存のキーワード

        Returns:
            {"small_category": str, "keywords": list}
        """
        keywords_text = ""
        if existing_keywords:
            keywords_text = f"\n既存キーワード: {', '.join(existing_keywords)}"

        prompt = f"""あなたは商品分類の専門家です。以下の商品の「小カテゴリ（small_category）」と「キーワード（keywords）」を推定してください。

## 商品情報
商品名: {product_name}
一般名詞: {general_name}{keywords_text}

## 出力形式
以下のJSON形式で回答してください。

{{
  "small_category": "小カテゴリ（例: 乳製品、パン類、野菜）",
  "keywords": ["キーワード1", "キーワード2", "キーワード3"],
  "confidence": 0.85
}}

**small_category**: 食品の大まかな分類
**keywords**: 検索に役立つ3-5個の単語（既存のキーワードがあれば活用）
**confidence**: 0.0〜1.0の信頼度
"""

        try:
            response = self.llm_client.call_model(
                tier="stageh_extraction",
                prompt=prompt,
                model_name="gemini-2.5-flash-lite",
                response_format="json"
            )

            if response.get("success"):
                content = json.loads(response.get("content", "{}"))
                return {
                    "small_category": content.get("small_category"),
                    "keywords": content.get("keywords", existing_keywords or []),
                    "confidence": content.get("confidence", 0.5)
                }

        except Exception as e:
            logger.error(f"生成エラー: {e}")
            return {
                "small_category": None,
                "keywords": existing_keywords or [],
                "confidence": 0.0
            }

    async def process_products(self, limit: int = None):
        """
        商品を一括処理

        Args:
            limit: 処理件数上限
        """
        logger.info("="*80)
        logger.info("small_category修正開始")
        logger.info("="*80)

        # 対象商品を取得
        products = await self.fetch_products_missing_small_category(limit=limit)

        if not products:
            logger.info("修正が必要な商品はありません")
            return

        stats = {
            'total': len(products),
            'success': 0,
            'error': 0
        }

        for i, product in enumerate(products, 1):
            try:
                product_id = product['id']
                product_name = product.get('product_name', '')
                general_name = product.get('general_name', '')
                existing_keywords = product.get('keywords', [])

                logger.info(f"[{i}/{stats['total']}] {product_name}")
                logger.info(f"  general_name: {general_name}")

                # small_categoryとkeywordsを生成
                result = await self.generate_small_category_and_keywords(
                    product_name,
                    general_name,
                    existing_keywords
                )

                small_category = result.get("small_category")
                keywords = result.get("keywords", [])

                logger.info(f"  ✓ small_category: {small_category}")
                logger.info(f"  ✓ keywords: {keywords}")

                # データベース更新
                self.db.client.table('Rawdata_NETSUPER_items').update({
                    'small_category': small_category,
                    'keywords': keywords
                }).eq('id', product_id).execute()

                stats['success'] += 1

                # 進捗表示
                if i % 50 == 0:
                    logger.info(f"進捗: {i}/{stats['total']} ({i*100//stats['total']}%)")

                # レート制限対策
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"[{i}/{stats['total']}] エラー: {e}")
                stats['error'] += 1

        # 結果サマリー
        logger.info("="*80)
        logger.info("修正完了")
        logger.info("="*80)
        logger.info(f"対象商品:    {stats['total']}件")
        logger.info(f"成功:        {stats['success']}件")
        logger.info(f"エラー:      {stats['error']}件")
        logger.info("="*80)


async def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(description='small_categoryが欠けている商品を修正')
    parser.add_argument('--limit', type=int, help='処理件数上限', default=None)
    args = parser.parse_args()

    fixer = SmallCategoryFixer()
    await fixer.process_products(limit=args.limit)


if __name__ == "__main__":
    asyncio.run(main())
