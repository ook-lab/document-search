#!/usr/bin/env python3
"""
中分類＝小分類の商品を適切な小分類に自動振り分け
Gemini AIを使用して適切な分類を提案し、データベースを更新
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

# 環境変数読み込み
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Gemini設定
genai.configure(api_key=os.getenv('GOOGLE_AI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash-exp')

def get_duplicate_categories(db):
    """中分類＝小分類のカテゴリを取得"""
    logger.info("中分類＝小分類のカテゴリを取得中...")

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

    # 中分類＝小分類を抽出
    duplicate_cats = []
    for cat in all_categories:
        medium = cat.get('medium_category', '')
        small = cat.get('small_category', '')

        if medium and small and medium == small:
            # 商品数を確認
            count_result = db.client.table('Rawdata_NETSUPER_items')\
                .select('id', count='exact')\
                .eq('category_id', cat['id'])\
                .execute()
            product_count = count_result.count if count_result.count else 0

            if product_count > 0:
                cat['product_count'] = product_count
                duplicate_cats.append(cat)

    logger.info(f"商品がある中分類＝小分類のカテゴリ: {len(duplicate_cats)}件")
    return duplicate_cats, all_categories

def get_available_small_categories(all_categories, medium_category):
    """指定した中分類で利用可能な別の小分類を取得"""
    available = []
    for cat in all_categories:
        if (cat.get('medium_category') == medium_category and
            cat.get('small_category') != medium_category):
            small = cat.get('small_category')
            if small and small not in available:
                available.append(small)
    return sorted(available)

def get_products_for_category(db, category_id, limit=50):
    """指定カテゴリの商品を取得"""
    result = db.client.table('Rawdata_NETSUPER_items')\
        .select('id', 'product_name', 'general_name')\
        .eq('category_id', category_id)\
        .limit(limit)\
        .execute()
    return result.data

def classify_with_gemini(products, medium_category, available_small_categories):
    """Geminiで商品を分類"""

    products_text = "\n".join([
        f"{i+1}. {p['product_name']} (一般名詞: {p.get('general_name', 'なし')})"
        for i, p in enumerate(products)
    ])

    categories_text = "\n".join([f"- {cat}" for cat in available_small_categories]) if available_small_categories else "（利用可能な小分類なし）"

    prompt = f"""以下の商品は、中分類「{medium_category}」で小分類も「{medium_category}」になっています。
これは不適切なので、より具体的な小分類に振り分けてください。

利用可能な既存の小分類:
{categories_text}

商品リスト:
{products_text}

各商品について:
1. 既存の小分類で適切なものがあればそれを選択
2. なければ新しい適切な小分類名を提案（必ず中分類「{medium_category}」の下位概念として適切なものにする）

以下のJSON形式で回答:
{{
  "classifications": [
    {{
      "product_name": "商品名",
      "small_category": "小分類名",
      "is_new": false
    }}
  ]
}}

必ず有効なJSONのみを返してください。"""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8000,
            )
        )

        result_text = response.text.strip()

        # JSONを抽出
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        result = json.loads(result_text)
        return result['classifications']

    except Exception as e:
        logger.error(f"Gemini分類エラー: {e}")
        if 'response' in locals():
            logger.error(f"レスポンス: {response.text}")
        return None

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

def main():
    db = DatabaseClient(use_service_role=True)

    # 1. 重複カテゴリを取得
    duplicate_cats, all_categories = get_duplicate_categories(db)

    if not duplicate_cats:
        logger.info("✅ 中分類＝小分類のカテゴリはありません")
        return

    logger.info(f"\n処理対象: {len(duplicate_cats)}カテゴリ")
    for cat in duplicate_cats[:10]:
        logger.info(f"  {cat['name']}: {cat['product_count']}件")

    # 統計
    total_products = sum(cat['product_count'] for cat in duplicate_cats)
    logger.info(f"\n総商品数: {total_products}件")

    # 2. カテゴリごとに処理
    updated_count = 0
    error_count = 0

    for cat_idx, dup_cat in enumerate(duplicate_cats, 1):
        medium_category = dup_cat['medium_category']
        large_category = dup_cat['large_category']
        category_id = dup_cat['id']
        product_count = dup_cat['product_count']

        logger.info(f"\n{'='*80}")
        logger.info(f"[{cat_idx}/{len(duplicate_cats)}] {dup_cat['name']} ({product_count}件)")
        logger.info(f"{'='*80}")

        # 利用可能な小分類を取得
        available_small = get_available_small_categories(all_categories, medium_category)
        logger.info(f"既存小分類: {len(available_small)}個")
        if available_small:
            logger.info(f"  {', '.join(available_small[:5])}" + (f" ... 他{len(available_small)-5}個" if len(available_small) > 5 else ""))

        # 商品を取得（バッチ処理）
        batch_size = 30
        offset = 0

        while offset < product_count:
            products = db.client.table('Rawdata_NETSUPER_items')\
                .select('id', 'product_name', 'general_name')\
                .eq('category_id', category_id)\
                .range(offset, offset + batch_size - 1)\
                .execute()

            if not products.data:
                break

            logger.info(f"\n  バッチ {offset+1}~{min(offset+batch_size, product_count)}/{product_count}")

            # Geminiで分類（レート制限対策で6秒待機）
            time.sleep(6)
            classifications = classify_with_gemini(products.data, medium_category, available_small)

            if not classifications:
                logger.error("  分類失敗、スキップ")
                offset += batch_size
                error_count += len(products.data)
                continue

            # 各商品を更新
            for i, classification in enumerate(classifications):
                if i >= len(products.data):
                    break

                product = products.data[i]
                new_small_category = classification.get('small_category', medium_category)
                is_new = classification.get('is_new', False)

                # カテゴリIDを取得/作成
                try:
                    new_category_id = get_or_create_category(
                        db, large_category, medium_category, new_small_category
                    )

                    # 商品を更新
                    db.client.table('Rawdata_NETSUPER_items').update({
                        'category_id': new_category_id,
                        'small_category': new_small_category
                    }).eq('id', product['id']).execute()

                    updated_count += 1

                    marker = "🆕" if is_new else "✅"
                    logger.info(f"    {marker} {product['product_name'][:40]} → {new_small_category}")

                except Exception as e:
                    logger.error(f"    ❌ 更新エラー: {product['product_name'][:40]} - {e}")
                    error_count += 1

            offset += batch_size

    logger.info(f"\n{'='*80}")
    logger.info(f"完了")
    logger.info(f"更新成功: {updated_count}件")
    logger.info(f"エラー: {error_count}件")
    logger.info(f"{'='*80}")

if __name__ == "__main__":
    # ⚠️ 安全ガード：誤実行防止
    print("\n" + "="*70)
    print("⚠️  警告: このスクリプトは高額なAPI料金が発生します")
    print("⚠️  処理内容: 中分類=小分類の商品を30件バッチでGemini分類")
    print("⚠️  max_output_tokens: 8000トークン/バッチ")
    print("⚠️  推定コスト: 商品数により変動（100件で約50-100円）")
    print("="*70)
    confirm = input("\n本当に実行しますか？ (YES と大文字で入力): ")
    if confirm != "YES":
        print("❌ 実行を中止しました")
        sys.exit(0)
    print("\n✅ 実行を開始します...\n")

    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
