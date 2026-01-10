"""
レシートデータとネットスーパー商品データを全削除

⚠️ 警告：この操作は元に戻せません ⚠️

削除対象：
1. Rawdata_RECEIPT_shops（レシート）
2. Rawdata_RECEIPT_items（取引明細、正規化データを含む）
3. Rawdata_NETSUPER_items（商品マスタ）
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from shared.common.database.client import DatabaseClient


def confirm_deletion():
    """削除の確認"""
    print("="*80)
    print("⚠️  警告：データ全削除 ⚠️")
    print("="*80)
    print("\n以下のテーブルから全データを削除します：")
    print("  1. Rawdata_RECEIPT_items（取引明細、正規化データを含む）")
    print("  2. Rawdata_RECEIPT_shops（レシート）")
    print("  3. Rawdata_NETSUPER_items（商品マスタ）")
    print("\n⚠️  この操作は元に戻せません！ ⚠️\n")

    response = input("本当に削除しますか？ (yes/no): ")
    return response.lower() == 'yes'


def delete_all_data():
    """全データを削除"""
    db = DatabaseClient(use_service_role=True)

    print("\n" + "="*80)
    print("削除開始...")
    print("="*80)

    stats = {}

    # 1. Rawdata_RECEIPT_items（子テーブル）を削除
    print("\n[1/4] Rawdata_RECEIPT_items を削除中...")
    result = db.client.table('Rawdata_RECEIPT_items').select('id', count='exact').execute()
    count = result.count
    stats['transactions'] = count

    if count > 0:
        db.client.table('Rawdata_RECEIPT_items').delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()
        print(f"  ✅ {count}件を削除")
    else:
        print("  ℹ️  削除対象なし")

    # 2. Rawdata_RECEIPT_shops（親テーブル）を削除
    print("\n[2/4] Rawdata_RECEIPT_shops を削除中...")
    result = db.client.table('Rawdata_RECEIPT_shops').select('id', count='exact').execute()
    count = result.count
    stats['receipts'] = count

    if count > 0:
        db.client.table('Rawdata_RECEIPT_shops').delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()
        print(f"  ✅ {count}件を削除")
    else:
        print("  ℹ️  削除対象なし")

    # 3. 99_lg_gemini_classification_log（分類ログ）を削除（外部キー制約のため先に削除）
    print("\n[3/4] 99_lg_gemini_classification_log を削除中...")
    try:
        result = db.client.table('99_lg_gemini_classification_log').select('id', count='exact').execute()
        count = result.count
        stats['classification_log'] = count

        if count > 0:
            db.client.table('99_lg_gemini_classification_log').delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()
            print(f"  ✅ {count}件を削除")
        else:
            print("  ℹ️  削除対象なし")
    except Exception as e:
        print(f"  ⚠️  警告: {e}")
        stats['classification_log'] = 0

    # 4. Rawdata_NETSUPER_items（商品マスタ）を削除
    print("\n[4/4] Rawdata_NETSUPER_items を削除中...")
    result = db.client.table('Rawdata_NETSUPER_items').select('id', count='exact').execute()
    count = result.count
    stats['products'] = count

    if count > 0:
        db.client.table('Rawdata_NETSUPER_items').delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()
        print(f"  ✅ {count}件を削除")
    else:
        print("  ℹ️  削除対象なし")

    # 結果サマリー
    print("\n" + "="*80)
    print("削除完了")
    print("="*80)
    print(f"Rawdata_RECEIPT_items:             {stats['transactions']}件")
    print(f"Rawdata_RECEIPT_shops:                 {stats['receipts']}件")
    print(f"99_lg_gemini_classification_log: {stats.get('classification_log', 0)}件")
    print(f"Rawdata_NETSUPER_items:                 {stats['products']}件")
    print("="*80)


def main():
    """メイン処理"""
    if confirm_deletion():
        delete_all_data()
    else:
        print("\n❌ 削除をキャンセルしました")


if __name__ == "__main__":
    main()
