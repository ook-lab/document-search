"""
テーブル一覧を確認
家計簿システムおよびマスタテーブルの存在確認ユーティリティ
"""
import os
from supabase import create_client
from dotenv import load_dotenv

# プロジェクトルートの.envを読み込む
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

db = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

print("=" * 80)
print("テーブル一覧（家計簿・マスタ）")
print("=" * 80)

# 現在のテーブル命名規則に基づくテーブルリスト
# - Rawdata_*: 生データテーブル
# - MASTER_*: マスタデータテーブル
# - 10_ix_*: インデックステーブル
# - 99_lg_*: ログテーブル
known_tables = [
    # 家計簿関連（Rawdata）
    'Rawdata_RECEIPT_items',
    'Rawdata_RECEIPT_shops',
    'Rawdata_FILE_AND_MAIL',

    # マスタテーブル（MASTER_）
    'MASTER_Categories_expense',
    'MASTER_Categories_product',
    'MASTER_Rules_transaction_dict',
    'MASTER_Stores',

    # インデックス（10_ix_）
    '10_ix_search_index',

    # ログ（99_lg_）
    '99_lg_image_proc_log',
    '99_lg_correction_history',
]

found_tables = []

for table_name in known_tables:
    try:
        result = db.table(table_name).select('*').limit(1).execute()
        # エラーが出なければテーブル存在
        row_count_result = db.table(table_name).select('id', count='exact').limit(1).execute()
        count = row_count_result.count if hasattr(row_count_result, 'count') else 'N/A'
        found_tables.append((table_name, count))
        print(f"✅ {table_name:40s} ({count} 件)")
    except Exception as e:
        if 'PGRST205' not in str(e):  # テーブル存在しないエラー以外
            print(f"⚠️  {table_name:40s} (エラー: {str(e)[:50]})")
        else:
            print(f"❌ {table_name:40s} (テーブル不存在)")

# 追加のテーブルパターンを探索
print("\n" + "=" * 80)
print("その他の関連テーブルを探索中...")
print("=" * 80)

# 現在の命名規則に基づくパターン
other_patterns = [
    'ops_requests',       # 処理リクエスト管理
    'processing_lock',    # 処理ロック
    'worker_state',       # ワーカー状態
]

for table_name in other_patterns:
    try:
        result = db.table(table_name).select('*').limit(1).execute()
        row_count_result = db.table(table_name).select('id', count='exact').limit(1).execute()
        count = row_count_result.count if hasattr(row_count_result, 'count') else 'N/A'
        if table_name not in [t[0] for t in found_tables]:
            found_tables.append((table_name, count))
            print(f"✅ {table_name:40s} ({count} 件)")
    except Exception as e:
        if 'PGRST205' not in str(e):
            print(f"⚠️  {table_name:40s} (エラー: {str(e)[:50]})")

print("\n" + "=" * 80)
print(f"合計: {len(found_tables)} 個のテーブルが存在")
print("=" * 80)
