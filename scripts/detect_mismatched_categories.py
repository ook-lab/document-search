#!/usr/bin/env python3
"""
中分類と小分類のミスマッチを検出
例：中分類「乳製品」に「ソーセージ」など
"""
import sys
from pathlib import Path
import time
import json
import os
from collections import defaultdict

root_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import DatabaseClient
import logging
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv('GOOGLE_AI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash-exp')

def get_all_categories_with_products(db):
    """商品があるカテゴリをすべて取得"""
    logger.info("全カテゴリを取得中...")
    all_categories = []
    offset = 0

    while True:
        result = db.client.table('MASTER_Categories_product')\
            .select('id', 'name', 'large_category', 'medium_category', 'small_category')\
            .range(offset, offset + 999)\
            .execute()

        if not result.data:
            break

        all_categories.extend(result.data)
        offset += 1000

        if len(result.data) < 1000:
            break

    logger.info(f"全カテゴリ数: {len(all_categories)}")

    # 商品数を取得
    categories_with_count = []
    for cat in all_categories:
        if cat.get('medium_category') and cat.get('small_category'):
            count_result = db.client.table('Rawdata_NETSUPER_items')\
                .select('id', count='exact')\
                .eq('category_id', cat['id'])\
                .execute()
            product_count = count_result.count if count_result.count else 0

            if product_count > 0:
                cat['product_count'] = product_count
                categories_with_count.append(cat)

    logger.info(f"商品があるカテゴリ数: {len(categories_with_count)}")
    return categories_with_count

def check_category_mismatch(medium_category, small_category, product_samples):
    """Gemini AIで中分類と小分類のミスマッチを検出"""

    samples_text = "\n".join([f"- {p}" for p in product_samples[:5]])

    prompt = f"""中分類「{medium_category}」に小分類「{small_category}」が存在します。

この組み合わせは適切ですか？

商品例:
{samples_text}

判断基準:
1. 小分類が中分類の下位概念として論理的に正しいか
2. 商品例が中分類に該当するか
3. より適切な中分類が存在しないか

以下のJSON形式で回答してください:
{{
  "is_appropriate": true/false,
  "reason": "判断理由",
  "suggested_medium": "適切でない場合の推奨中分類（適切な場合はnull）"
}}

必ず有効なJSONのみを返してください。"""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=500,
            )
        )

        result_text = response.text.strip()

        # JSONを抽出
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        result = json.loads(result_text)
        return result

    except Exception as e:
        logger.error(f"Gemini判断エラー: {e}")
        if 'response' in locals():
            logger.error(f"レスポンス: {response.text}")
        return {"is_appropriate": True, "reason": "エラー", "suggested_medium": None}

def main():
    db = DatabaseClient(use_service_role=True)

    # カテゴリを取得
    categories = get_all_categories_with_products(db)

    # 中分類ごとにグループ化
    medium_groups = defaultdict(list)
    for cat in categories:
        medium = cat.get('medium_category')
        if medium:
            medium_groups[medium].append(cat)

    logger.info(f"\n中分類数: {len(medium_groups)}")

    # 各中分類の小分類をチェック
    mismatches = []
    checked_count = 0
    total_to_check = sum(len(cats) for cats in medium_groups.values())

    logger.info(f"チェック対象: {total_to_check}カテゴリ")
    logger.info("=" * 80)

    for medium, cats in sorted(medium_groups.items()):
        logger.info(f"\n中分類「{medium}」({len(cats)}小分類)")

        for cat in cats:
            small = cat.get('small_category')
            product_count = cat.get('product_count', 0)

            # サンプル商品を取得
            products = db.client.table('Rawdata_NETSUPER_items')\
                .select('product_name')\
                .eq('category_id', cat['id'])\
                .limit(5)\
                .execute()

            samples = [p['product_name'] for p in products.data]

            # レート制限対策（10個に1回6秒待機）
            checked_count += 1
            if checked_count % 10 == 0:
                time.sleep(6)
                logger.info(f"  進捗: {checked_count}/{total_to_check}")

            # Geminiでチェック
            result = check_category_mismatch(medium, small, samples)

            if not result['is_appropriate']:
                mismatch_info = {
                    'category_id': cat['id'],
                    'large_category': cat['large_category'],
                    'current_medium': medium,
                    'small_category': small,
                    'product_count': product_count,
                    'suggested_medium': result['suggested_medium'],
                    'reason': result['reason'],
                    'samples': samples[:3]
                }
                mismatches.append(mismatch_info)

                logger.info(f"  ⚠️  小分類「{small}」({product_count}件)")
                logger.info(f"     → 推奨: {result['suggested_medium']}")
                logger.info(f"     理由: {result['reason'][:100]}")

    # 結果サマリー
    logger.info(f"\n{'='*80}")
    logger.info(f"検出完了")
    logger.info(f"チェック数: {checked_count}カテゴリ")
    logger.info(f"ミスマッチ: {len(mismatches)}件")
    logger.info(f"{'='*80}")

    if mismatches:
        logger.info("\nミスマッチ一覧:")
        for i, m in enumerate(mismatches, 1):
            logger.info(f"\n{i}. {m['current_medium']} → {m['suggested_medium']}")
            logger.info(f"   小分類: {m['small_category']} ({m['product_count']}件)")
            logger.info(f"   商品例: {', '.join(m['samples'])}")
            logger.info(f"   理由: {m['reason']}")

        # JSON形式で保存
        with open('/tmp/category_mismatches.json', 'w', encoding='utf-8') as f:
            json.dump(mismatches, f, ensure_ascii=False, indent=2)
        logger.info(f"\n結果を /tmp/category_mismatches.json に保存しました")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
