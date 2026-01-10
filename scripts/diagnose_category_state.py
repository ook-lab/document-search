"""
カテゴリ階層の現状診断
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
    print("カテゴリ階層の現状診断")
    print("=" * 80)

    # 全カテゴリを取得
    result = db.client.table('MASTER_Categories_product').select('*').order('created_at').execute()
    categories = result.data

    print(f"\n総カテゴリ数: {len(categories)}件")
    print("\n" + "=" * 80)

    # 大分類（parent_id IS NULL）
    large_categories = [cat for cat in categories if cat.get('parent_id') is None]
    print(f"\n【大分類】 {len(large_categories)}件")
    for cat in large_categories:
        print(f"  ID: {cat['id']}")
        print(f"  名前: {cat['name']}")
        print(f"  説明: {cat.get('description', '')}")
        print()

    # 中分類を取得（parent_idが大分類のIDと一致）
    large_ids = [cat['id'] for cat in large_categories]
    mid_categories = [cat for cat in categories if cat.get('parent_id') in large_ids]

    print("=" * 80)
    print(f"\n【中分類】 {len(mid_categories)}件")
    for cat in mid_categories:
        parent = next((c for c in large_categories if c['id'] == cat['parent_id']), None)
        parent_name = parent['name'] if parent else 'Unknown'
        print(f"  ID: {cat['id']}")
        print(f"  名前: {cat['name']} (親: {parent_name})")
        print(f"  説明: {cat.get('description', '')}")
        print()

    # 小分類を取得（parent_idが中分類のIDと一致）
    mid_ids = [cat['id'] for cat in mid_categories]
    small_categories = [cat for cat in categories if cat.get('parent_id') in mid_ids]

    print("=" * 80)
    print(f"\n【小分類】 {len(small_categories)}件")

    if small_categories:
        # 中分類ごとにグループ化して表示
        for mid_cat in mid_categories:
            mid_id = mid_cat['id']
            mid_name = mid_cat['name']
            small_in_mid = [cat for cat in small_categories if cat['parent_id'] == mid_id]

            if small_in_mid:
                parent = next((c for c in large_categories if c['id'] == mid_cat['parent_id']), None)
                parent_name = parent['name'] if parent else 'Unknown'
                print(f"\n  [{parent_name} > {mid_name}] ({len(small_in_mid)}件)")
                for cat in small_in_mid:
                    print(f"    - {cat['name']}: {cat.get('description', '')}")
    else:
        print("  ⚠️ 小分類が1つも存在しません")

    # 特定の中分類をチェック
    print("\n" + "=" * 80)
    print("重要な中分類の確認")
    print("=" * 80)

    important_mid = ['野菜', '果物', '肉類', '魚介類']
    for name in important_mid:
        found = next((cat for cat in mid_categories if cat['name'] == name), None)
        if found:
            small_count = len([cat for cat in small_categories if cat.get('parent_id') == found['id']])
            print(f"  ✅ {name}: ID={found['id']}, 小分類={small_count}件")
        else:
            print(f"  ❌ {name}: 見つかりません")

    # 階層が正しくない可能性のあるカテゴリを検出
    print("\n" + "=" * 80)
    print("階層エラーの可能性")
    print("=" * 80)

    orphans = [cat for cat in categories if cat.get('parent_id') and cat['parent_id'] not in [c['id'] for c in categories]]
    if orphans:
        print(f"  ⚠️ 親が存在しないカテゴリ: {len(orphans)}件")
        for cat in orphans:
            print(f"    - {cat['name']} (parent_id: {cat['parent_id']})")
    else:
        print("  ✅ 親が存在しないカテゴリはありません")

    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
