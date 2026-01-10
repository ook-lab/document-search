"""
processing_status を pending にリセットするスクリプト
"""
from shared.common.database.client import DatabaseClient

def reset_to_pending(workspace: str = 'all', limit: int = None, auto_confirm: bool = False):
    """
    processing_status を pending にリセット

    Args:
        workspace: 対象ワークスペース ('all' で全て)
        limit: リセットする最大件数 (None で無制限)
        auto_confirm: 確認をスキップして自動実行
    """
    db = DatabaseClient()

    # 現在の状態を確認
    print("=" * 80)
    print("現在の processing_status 集計:")
    print("=" * 80)

    try:
        response = db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status').execute()

        status_counts = {}
        for doc in response.data:
            status = doc.get('processing_status', 'null')
            status_counts[status] = status_counts.get(status, 0) + 1

        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}件")
    except Exception as e:
        print(f"ERROR: 集計失敗: {e}")
        return

    print()

    # リセット対象を確認
    try:
        query = db.client.table('Rawdata_FILE_AND_MAIL').select('id, file_name, workspace, processing_status')

        if workspace != 'all':
            query = query.eq('workspace', workspace)

        # completed または failed のドキュメントを取得
        query = query.in_('processing_status', ['completed', 'failed'])

        if limit:
            query = query.limit(limit)

        response = query.execute()
        docs = response.data

        if not docs:
            print("リセット対象のドキュメントがありません。")
            return

        print(f"リセット対象: {len(docs)}件")
        print()

        # 確認
        print("以下のドキュメントを pending にリセットします:")
        for i, doc in enumerate(docs[:10], 1):
            print(f"  {i}. {doc.get('file_name', 'unknown')} ({doc.get('workspace', 'unknown')}) - {doc.get('processing_status')}")

        if len(docs) > 10:
            print(f"  ... 他 {len(docs) - 10}件")

        print()

        if not auto_confirm:
            confirm = input("実行しますか? (yes/no): ")
            if confirm.lower() != 'yes':
                print("キャンセルしました。")
                return
        else:
            print("自動実行モード（--yes 指定）")


        # リセット実行
        print()
        print("リセット中...")

        for doc in docs:
            try:
                db.client.table('Rawdata_FILE_AND_MAIL').update({
                    'processing_status': 'pending'
                }).eq('id', doc['id']).execute()
                print(f"✅ {doc.get('file_name', 'unknown')}")
            except Exception as e:
                print(f"❌ {doc.get('file_name', 'unknown')}: {e}")

        print()
        print(f"✅ リセット完了: {len(docs)}件")

    except Exception as e:
        print(f"ERROR: リセット失敗: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='processing_status を pending にリセット')
    parser.add_argument('--workspace', default='all', help='対象ワークスペース (デフォルト: all)')
    parser.add_argument('--limit', type=int, help='リセットする最大件数')
    parser.add_argument('--yes', action='store_true', help='確認をスキップ')

    args = parser.parse_args()

    reset_to_pending(workspace=args.workspace, limit=args.limit, auto_confirm=args.yes)
