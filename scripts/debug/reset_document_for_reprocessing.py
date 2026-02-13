"""
ドキュメントを再処理対象にするスクリプト

Usage:
    python scripts/debug/reset_document_for_reprocessing.py <document_id>
"""
import sys
from pathlib import Path

# プロジェクトルートをPYTHONPATHに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.common.db_client import DatabaseClient

def reset_document(doc_id: str):
    """ドキュメントを再処理対象にする"""
    db = DatabaseClient(use_service_role=True)

    # processing_status を failed に変更して再処理対象にする
    result = db.client.table('Rawdata_FILE_AND_MAIL').update({
        'processing_status': 'failed',
        'processing_error': 'Manual reprocess request - fixing metadata structure'
    }).eq('id', doc_id).execute()

    if result.data:
        print('✅ ドキュメントを再処理対象にしました')
        print(f'Doc ID: {doc_id}')
        print('\n次のコマンドで再処理を実行してください:')
        print('python scripts/processing/process_queued_documents.py')
    else:
        print(f'❌ ドキュメントが見つかりませんでした: {doc_id}')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('使用法: python scripts/debug/reset_document_for_reprocessing.py <document_id>')
        sys.exit(1)

    doc_id = sys.argv[1]
    reset_document(doc_id)
