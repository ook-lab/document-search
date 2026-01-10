"""
10_ix_search_index テーブルの全チャンクを削除

再処理前に実行して、重複を防ぎます。
"""
from shared.common.database.client import DatabaseClient

db = DatabaseClient()

# 現在のチャンク数を確認
print("=" * 80)
print("チャンク削除確認")
print("=" * 80)

response = db.client.table('10_ix_search_index').select('id', count='exact').execute()
total_chunks = response.count

print(f"\n現在のチャンク数: {total_chunks}件")

if total_chunks == 0:
    print("\n既にチャンクは削除されています。")
    exit(0)

# chunk_metadataの状態を確認
with_metadata = db.client.table('10_ix_search_index').select('id', count='exact').not_.is_('chunk_metadata', 'null').execute()
without_metadata = total_chunks - with_metadata.count

print(f"  chunk_metadata あり: {with_metadata.count}件")
print(f"  chunk_metadata なし: {without_metadata}件")

print("\n" + "=" * 80)
print("⚠️  警告: 全てのチャンクを削除します")
print("=" * 80)
print("\n続行しますか？ (yes/no): ", end="")

answer = input()

if answer.lower() != 'yes':
    print("\nキャンセルしました。")
    exit(0)

print("\n削除中...")

# 全チャンクを削除（バッチ処理）
# Supabaseは一度に全削除できないので、小さいバッチで削除
batch_size = 100
deleted = 0

while True:
    # 最初の100件を取得
    response = db.client.table('10_ix_search_index').select('id').limit(batch_size).execute()

    if not response.data:
        break

    # 1件ずつ削除（確実に削除）
    for chunk in response.data:
        try:
            db.client.table('10_ix_search_index').delete().eq('id', chunk['id']).execute()
            deleted += 1
            if deleted % 100 == 0:
                print(f"削除: {deleted}/{total_chunks}件")
        except Exception as e:
            print(f"エラー (ID={chunk['id']}): {e}")

    if len(response.data) < batch_size:
        break

print(f"\n✅ 削除完了: {deleted}件")

# 確認
final_count = db.client.table('10_ix_search_index').select('id', count='exact').execute()
print(f"残りのチャンク数: {final_count.count}件")
