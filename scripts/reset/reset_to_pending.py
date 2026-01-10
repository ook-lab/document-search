"""
processing 状態のドキュメントを pending に戻す
"""
from shared.common.database.client import DatabaseClient

def reset_to_pending(workspace='ikuya_classroom'):
    db = DatabaseClient()

    # processing 状態のドキュメントを取得
    result = db.client.table('Rawdata_FILE_AND_MAIL')\
        .select('id, file_name, title')\
        .eq('workspace', workspace)\
        .eq('processing_status', 'processing')\
        .execute()

    if not result.data:
        print(f"processing 状態のドキュメントが見つかりません (workspace: {workspace})")
        return

    print(f"processing 状態のドキュメント: {len(result.data)}件")
    for doc in result.data:
        title = doc.get('title', '(タイトル未生成)')
        print(f"  - {title}")

    # pending に戻す
    for doc in result.data:
        db.client.table('Rawdata_FILE_AND_MAIL')\
            .update({'processing_status': 'pending'})\
            .eq('id', doc['id'])\
            .execute()

    print(f"\n[OK] {len(result.data)}件を pending に戻しました")

if __name__ == '__main__':
    import sys
    workspace = sys.argv[1] if len(sys.argv) > 1 else 'ikuya_classroom'
    reset_to_pending(workspace)
