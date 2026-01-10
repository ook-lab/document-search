"""
全ドキュメントをpendingに戻して生成データを削除するスクリプト
"""
from shared.common.database.client import DatabaseClient

def reset_all_documents():
    db = DatabaseClient()

    # 全ドキュメント数を確認
    print("全ドキュメント数を確認中...")
    result = db.client.table('Rawdata_FILE_AND_MAIL')\
        .select('id', count='exact')\
        .execute()

    total = result.count
    print(f"全ドキュメント数: {total}件\n")

    # completed状態のドキュメント数
    completed = db.client.table('Rawdata_FILE_AND_MAIL')\
        .select('id', count='exact')\
        .eq('processing_status', 'completed')\
        .execute()

    print(f"completed状態: {completed.count}件")

    # 全ドキュメントをpendingに戻し、生成データを削除
    print(f"\n全{total}件をpendingに戻して生成データを削除中...")

    # バッチ処理で更新
    db.client.table('Rawdata_FILE_AND_MAIL').update({
        'processing_status': 'pending',
        'title': None,
        'stage_i_structured': None,
        'stage_j_chunks_json': None,
        'document_date': None
    }).neq('id', '00000000-0000-0000-0000-000000000000').execute()

    print(f"\n[完了] 全{total}件をpendingに戻しました")
    print("生成データ（title, stage_i_structured, stage_j_chunks_json, document_date）を削除しました")

    # 確認
    pending_count = db.client.table('Rawdata_FILE_AND_MAIL')\
        .select('id', count='exact')\
        .eq('processing_status', 'pending')\
        .execute()

    print(f"\n現在のpending状態: {pending_count.count}件")

if __name__ == '__main__':
    reset_all_documents()
