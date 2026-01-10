"""単一ドキュメントを処理"""
import asyncio
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from process_queued_documents import DocumentProcessor

async def main():
    doc_id = '2a16467c-435b-44ab-80f8-d9f8c1670495'

    processor = DocumentProcessor()

    # ドキュメント取得
    doc_result = processor.db.client.table('Rawdata_FILE_AND_MAIL').select('*').eq('id', doc_id).execute()

    if not doc_result.data:
        print(f"ERROR: ドキュメントが見つかりません: {doc_id}")
        return

    doc = doc_result.data[0]
    print(f"処理開始: {doc.get('file_name', 'unknown')}")
    print(f"Document ID: {doc_id}")
    print(f"Workspace: {doc.get('workspace')}")
    print(f"Status: {doc.get('processing_status')}")
    print("=" * 80)

    # 処理実行
    success = await processor.process_document(doc, preserve_workspace=True)

    if success:
        print("\n✅ 処理成功")
    else:
        print("\n❌ 処理失敗")

if __name__ == '__main__':
    asyncio.run(main())
