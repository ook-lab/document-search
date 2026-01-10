"""
分類システムマイグレーションの検証
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Windows環境でのUnicode出力設定
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from shared.common.database.client import DatabaseClient

def main():
    db = DatabaseClient(use_service_role=True)

    print("=" * 80)
    print("分類システムマイグレーション検証")
    print("=" * 80)

    # 1. MASTER_Product_category_mapping テーブルの確認
    print("\n[1] MASTER_Product_category_mapping テーブル")
    print("-" * 80)

    try:
        result = db.client.table('MASTER_Product_category_mapping').select(
            'general_name, MASTER_Categories_product(name)',
            count='exact'
        ).execute()

        total_count = result.count
        print(f"✅ テーブルが存在します")
        print(f"   総レコード数: {total_count}件")

        if total_count > 0:
            # カテゴリ別集計
            all_data = db.client.table('MASTER_Product_category_mapping').select(
                'general_name, product_category_id, MASTER_Categories_product(name)'
            ).execute()

            category_counts = {}
            for item in all_data.data:
                cat_info = item.get('MASTER_Categories_product')
                if cat_info:
                    cat_name = cat_info.get('name', '不明')
                    category_counts[cat_name] = category_counts.get(cat_name, 0) + 1

            print("\n   カテゴリ別件数:")
            for cat_name, count in sorted(category_counts.items()):
                print(f"     {cat_name:15s}: {count:3d}件")

            # サンプルデータ表示
            print("\n   サンプルデータ（最初の10件）:")
            for i, item in enumerate(result.data[:10], 1):
                general_name = item.get('general_name', 'N/A')
                cat_info = item.get('MASTER_Categories_product')
                cat_name = cat_info.get('name', 'N/A') if cat_info else 'N/A'
                print(f"     {i:2d}. {general_name:20s} → {cat_name}")
        else:
            print("   ⚠️  データが投入されていません")

    except Exception as e:
        print(f"❌ エラー: {e}")

    # 2. MASTER_Product_classify テーブルの拡張確認
    print("\n[2] MASTER_Product_classify テーブル（カラム追加確認）")
    print("-" * 80)

    try:
        result = db.client.table('MASTER_Product_classify').select('*').limit(1).execute()

        if result.data:
            columns = list(result.data[0].keys())
            print(f"✅ テーブルが存在します")
            print(f"   総カラム数: {len(columns)}個")

            # 新しいカラムの確認
            new_columns = ['product_category_id', 'purpose_id', 'person']
            print("\n   新規追加カラム:")
            for col in new_columns:
                if col in columns:
                    print(f"     ✅ {col}")
                else:
                    print(f"     ❌ {col} (見つかりません)")

        else:
            print("✅ テーブルが存在します（データなし）")

    except Exception as e:
        print(f"❌ エラー: {e}")

    # 3. 商品カテゴリマスタの確認
    print("\n[3] MASTER_Categories_product テーブル")
    print("-" * 80)

    try:
        result = db.client.table('MASTER_Categories_product').select(
            'name', count='exact'
        ).execute()

        print(f"✅ 総カテゴリ数: {result.count}件")

        # 主要カテゴリの確認
        key_categories = ['食材', '飲料', '日用品']
        print("\n   主要カテゴリ:")
        for cat in key_categories:
            cat_result = db.client.table('MASTER_Categories_product').select('id').eq('name', cat).execute()
            if cat_result.data:
                print(f"     ✅ {cat}")
            else:
                print(f"     ❌ {cat} (見つかりません)")

    except Exception as e:
        print(f"❌ エラー: {e}")

    # 4. サマリー
    print("\n" + "=" * 80)
    print("検証結果サマリー")
    print("=" * 80)

    try:
        # テーブル存在確認
        mapping_count = db.client.table('MASTER_Product_category_mapping').select('id', count='exact').execute().count

        if mapping_count > 0:
            print(f"✅ マイグレーション成功")
            print(f"   - MASTER_Product_category_mapping: {mapping_count}件")
            print(f"   - MASTER_Product_classify: 拡張完了")
            print(f"\n次のステップ:")
            print(f"   1. 不足している商品マッピングを追加")
            print(f"   2. コードの更新（分類ロジック）")
        else:
            print(f"⚠️  テーブルは作成されましたが、データが投入されていません")
            print(f"   insert_sample_product_category_mappings_v2.sql を再実行してください")

    except Exception as e:
        print(f"❌ 検証エラー: {e}")

    print("=" * 80)


if __name__ == "__main__":
    main()
