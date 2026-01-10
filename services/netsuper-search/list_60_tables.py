"""
60番台のテーブル一覧を確認
"""
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

db = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

print("=" * 80)
print("60番台のテーブル一覧")
print("=" * 80)

# PostgreSQLのシステムカタログから60番台のテーブルを取得
# Supabaseの制限により、直接クエリできないので、既知のテーブルをチェック
known_tables = [
    'Rawdata_RECEIPT_items',
    'Rawdata_RECEIPT_shops',
    '60_ms_categories',
    '60_ms_situations',
    '60_ms_product_dict',
    '60_ms_ocr_aliases',
    '60_ag_daily_summary',
    '60_ag_monthly_summary',
]

tables_60 = []

for table_name in known_tables:
    try:
        result = db.table(table_name).select('*').limit(1).execute()
        # エラーが出なければテーブル存在
        row_count_result = db.table(table_name).select('id', count='exact').limit(1).execute()
        count = row_count_result.count if hasattr(row_count_result, 'count') else 'N/A'
        tables_60.append((table_name, count))
        print(f"✅ {table_name:40s} ({count} 件)")
    except Exception as e:
        if 'PGRST205' not in str(e):  # テーブル存在しないエラー以外
            print(f"⚠️  {table_name:40s} (エラー: {str(e)[:30]})")

# 他にも60番台があるか確認するため、パターンでチェック
print("\n" + "=" * 80)
print("その他の60番台テーブルを探索中...")
print("=" * 80)

# よくあるパターン（60_rd_standardized_itemsは削除済み）
other_patterns = [
    '60_rd_items',
    '60_rd_raw_items',
    '60_ms_stores',
    '60_ms_payment_methods',
    '60_ix_',
    '60_lg_',
]

for pattern in other_patterns:
    for suffix in ['', '_backup', '_old', '_new']:
        table_name = pattern + suffix
        try:
            result = db.table(table_name).select('*').limit(1).execute()
            row_count_result = db.table(table_name).select('id', count='exact').limit(1).execute()
            count = row_count_result.count if hasattr(row_count_result, 'count') else 'N/A'
            if table_name not in [t[0] for t in tables_60]:
                tables_60.append((table_name, count))
                print(f"✅ {table_name:40s} ({count} 件)")
        except:
            pass

print("\n" + "=" * 80)
print(f"合計: {len(tables_60)} 個の60番台テーブル")
print("=" * 80)
