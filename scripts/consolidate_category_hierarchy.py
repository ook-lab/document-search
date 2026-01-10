#!/usr/bin/env python3
"""
カテゴリ階層の統合
複数の中分類に存在する同じ小分類を、最も適切な中分類に統合
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
    return categories_with_count, all_categories

def find_duplicate_small_categories(categories_with_count):
    """複数の中分類に存在する小分類を検出"""
    small_to_mediums = defaultdict(lambda: defaultdict(int))

    for cat in categories_with_count:
        small = cat.get('small_category')
        medium = cat.get('medium_category')
        large = cat.get('large_category')

        if small and medium:
            key = (small, large)  # 小分類と大分類でグループ化
            small_to_mediums[key][medium] += cat['product_count']

    # 複数の中分類に存在する小分類を抽出
    duplicates = {}
    for (small, large), mediums in small_to_mediums.items():
        if len(mediums) > 1:
            duplicates[(small, large)] = dict(mediums)

    return duplicates

def decide_best_medium_category(small_category, large_category, medium_counts, db):
    """Gemini AIで最適な中分類を決定"""

    # 各中分類のサンプル商品を取得
    samples = {}
    for medium, count in medium_counts.items():
        # このmedium-smallの組み合わせのカテゴリIDを取得
        cat_result = db.client.table('MASTER_Categories_product')\
            .select('id')\
            .eq('large_category', large_category)\
            .eq('medium_category', medium)\
            .eq('small_category', small_category)\
            .limit(1)\
            .execute()

        if cat_result.data:
            cat_id = cat_result.data[0]['id']
            # サンプル商品を取得
            products = db.client.table('Rawdata_NETSUPER_items')\
                .select('product_name', 'general_name')\
                .eq('category_id', cat_id)\
                .limit(5)\
                .execute()

            samples[medium] = [p['product_name'] for p in products.data]

    # Geminiで判断
    medium_list = "\n".join([
        f"- {medium}: {count}件\n  商品例: {', '.join(samples.get(medium, [])[:3])}"
        for medium, count in sorted(medium_counts.items(), key=lambda x: -x[1])
    ])

    prompt = f"""大分類「{large_category}」において、小分類「{small_category}」が以下の複数の中分類に存在しています:

{medium_list}

この小分類「{small_category}」に最も適切な中分類を1つ選んでください。

判断基準:
1. 商品内容と中分類の整合性
2. 商品数
3. カテゴリの一般的な階層構造

以下のJSON形式で回答してください:
{{
  "best_medium": "最適な中分類名",
  "reason": "選択理由"
}}

必ず有効なJSONのみを返してください。"""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=1000,
            )
        )

        result_text = response.text.strip()

        # JSONを抽出
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        result = json.loads(result_text)
        return result['best_medium'], result.get('reason', '')

    except Exception as e:
        logger.error(f"Gemini判断エラー: {e}")
        if 'response' in locals():
            logger.error(f"レスポンス: {response.text}")
        # エラー時は商品数が最も多い中分類を選択
        return max(medium_counts.items(), key=lambda x: x[1])[0], "商品数が最多"

def get_or_create_category(db, large_category, medium_category, small_category):
    """カテゴリを取得または作成"""
    category_name = f"{large_category}>{medium_category}>{small_category}"

    # 既存カテゴリを検索
    result = db.client.table('MASTER_Categories_product')\
        .select('id')\
        .eq('name', category_name)\
        .execute()

    if result.data:
        return result.data[0]['id']

    # 新規作成
    new_cat = {
        'name': category_name,
        'large_category': large_category,
        'medium_category': medium_category,
        'small_category': small_category,
        'parent_id': None
    }

    result = db.client.table('MASTER_Categories_product').insert(new_cat).execute()

    if result.data:
        logger.info(f"✅ 新規カテゴリ作成: {category_name}")
        return result.data[0]['id']

    raise Exception(f"カテゴリ作成失敗: {category_name}")

def consolidate_products(db, small_category, large_category, best_medium, all_mediums, all_categories):
    """商品を統合"""
    # 正しいカテゴリIDを取得/作成
    correct_category_id = get_or_create_category(db, large_category, best_medium, small_category)

    updated_count = 0

    # 他の中分類から商品を移動
    for medium in all_mediums:
        if medium == best_medium:
            continue

        # この中分類-小分類の組み合わせのカテゴリIDを取得
        old_cats = [c for c in all_categories
                   if c.get('large_category') == large_category
                   and c.get('medium_category') == medium
                   and c.get('small_category') == small_category]

        for old_cat in old_cats:
            old_category_id = old_cat['id']

            # この古いカテゴリの商品をすべて取得
            products = db.client.table('Rawdata_NETSUPER_items')\
                .select('id')\
                .eq('category_id', old_category_id)\
                .execute()

            # 商品を新しいカテゴリに移動
            for product in products.data:
                db.client.table('Rawdata_NETSUPER_items').update({
                    'category_id': correct_category_id
                }).eq('id', product['id']).execute()

                updated_count += 1

            logger.info(f"  移動: {medium} → {best_medium}: {len(products.data)}件")

    return updated_count

def main():
    db = DatabaseClient(use_service_role=True)

    # 1. カテゴリを取得
    categories_with_count, all_categories = get_all_categories_with_products(db)

    # 2. 重複する小分類を検出
    duplicates = find_duplicate_small_categories(categories_with_count)

    logger.info(f"\n複数の中分類に存在する小分類: {len(duplicates)}個")

    if not duplicates:
        logger.info("✅ 重複する小分類はありません")
        return

    # 統計
    total_affected = sum(sum(counts.values()) for counts in duplicates.values())
    logger.info(f"影響を受ける商品数: {total_affected}件")

    # 3. 各重複について処理
    total_updated = 0

    for idx, ((small, large), medium_counts) in enumerate(sorted(duplicates.items()), 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"[{idx}/{len(duplicates)}] 大分類「{large}」小分類「{small}」")
        logger.info(f"中分類: {list(medium_counts.keys())}")
        logger.info(f"総商品数: {sum(medium_counts.values())}件")
        logger.info(f"{'='*80}")

        # Geminiで最適な中分類を判断（レート制限対策で6秒待機）
        time.sleep(6)
        best_medium, reason = decide_best_medium_category(small, large, medium_counts, db)

        logger.info(f"✅ 最適な中分類: {best_medium}")
        logger.info(f"   理由: {reason}")

        # 商品を統合
        updated = consolidate_products(db, small, large, best_medium,
                                      list(medium_counts.keys()), all_categories)

        total_updated += updated
        logger.info(f"   更新: {updated}件")

    logger.info(f"\n{'='*80}")
    logger.info(f"完了")
    logger.info(f"総更新件数: {total_updated}件")
    logger.info(f"{'='*80}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
