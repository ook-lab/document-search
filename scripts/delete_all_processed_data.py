"""
Stage E以降で生成された全データを削除

削除対象:
1. Rawdata_FILE_AND_MAIL:
   - attachment_text (Stage E)
   - metadata (Stage H)
   - summary (Stage I)
   - tags (Stage I)
   - document_date (Stage I)

2. 10_ix_search_index:
   - 全チャンク

3. processing_statusをpendingにリセット
"""
from shared.common.database.client import DatabaseClient

db = DatabaseClient()

print("=" * 80)
print("Stage E以降のデータ削除")
print("=" * 80)

# 現状確認
print("\n[1] Rawdata_FILE_AND_MAIL の状態:")
docs = db.client.table('Rawdata_FILE_AND_MAIL').select('*').limit(5).execute()
if docs.data:
    sample = docs.data[0]
    print(f"  attachment_text: {'あり' if sample.get('attachment_text') else 'なし'}")
    print(f"  metadata: {'あり' if sample.get('metadata') else 'なし'}")
    print(f"  summary: {'あり' if sample.get('summary') else 'なし'}")
    print(f"  tags: {'あり' if sample.get('tags') else 'なし'}")
    print(f"  document_date: {'あり' if sample.get('document_date') else 'なし'}")

total_docs = db.client.table('Rawdata_FILE_AND_MAIL').select('id', count='exact').execute()
print(f"\n  総ドキュメント数: {total_docs.count}件")

print("\n[2] 10_ix_search_index の状態:")
total_chunks = db.client.table('10_ix_search_index').select('id', count='exact').execute()
print(f"  総チャンク数: {total_chunks.count}件")

print("\n" + "=" * 80)
print("⚠️  以下のデータを削除します:")
print("=" * 80)
print(f"1. Rawdata_FILE_AND_MAIL ({total_docs.count}件):")
print("   - attachment_text → NULL")
print("   - metadata → NULL")
print("   - summary → NULL")
print("   - tags → NULL")
print("   - document_date → NULL")
print("   - processing_status → 'pending'")
print(f"\n2. 10_ix_search_index ({total_chunks.count}件):")
print("   - 全チャンク削除")

print("\n続行しますか？ (yes/no): ", end="")
answer = input()

if answer.lower() != 'yes':
    print("\nキャンセルしました。")
    exit(0)

print("\n削除開始...")

# ========================================
# 1. Rawdata_FILE_AND_MAIL のフィールドをクリア
# ========================================
print("\n[1] Rawdata_FILE_AND_MAIL のフィールドをクリア中...")

# 全ドキュメントのIDを取得
all_docs = db.client.table('Rawdata_FILE_AND_MAIL').select('id').execute()

updated = 0
for doc in all_docs.data:
    db.client.table('Rawdata_FILE_AND_MAIL').update({
        'attachment_text': None,
        'metadata': None,
        'summary': None,
        'tags': None,
        'document_date': None,
        'processing_status': 'pending'
    }).eq('id', doc['id']).execute()

    updated += 1
    if updated % 50 == 0:
        print(f"  更新: {updated}/{total_docs.count}件")

print(f"✅ Rawdata_FILE_AND_MAIL 更新完了: {updated}件")

# ========================================
# 2. 10_ix_search_index の全チャンク削除
# ========================================
print("\n[2] 10_ix_search_index の全チャンク削除中...")

deleted = 0
while True:
    # 100件ずつ取得
    response = db.client.table('10_ix_search_index').select('id').limit(100).execute()

    if not response.data:
        break

    # 1件ずつ削除
    for chunk in response.data:
        try:
            db.client.table('10_ix_search_index').delete().eq('id', chunk['id']).execute()
            deleted += 1
            if deleted % 100 == 0:
                print(f"  削除: {deleted}/{total_chunks.count}件")
        except Exception as e:
            print(f"  エラー (ID={chunk['id']}): {e}")

    if len(response.data) < 100:
        break

print(f"✅ 10_ix_search_index 削除完了: {deleted}件")

# 確認
print("\n" + "=" * 80)
print("削除結果確認")
print("=" * 80)

remaining_chunks = db.client.table('10_ix_search_index').select('id', count='exact').execute()
print(f"残りチャンク数: {remaining_chunks.count}件")

pending_docs = db.client.table('Rawdata_FILE_AND_MAIL').select('id', count='exact').eq('processing_status', 'pending').execute()
print(f"pending ドキュメント数: {pending_docs.count}件")

print("\n✅ 全削除完了")
