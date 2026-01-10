#!/usr/bin/env python3
"""特定のドキュメントを処理"""

import asyncio
from shared.common.database.client import DatabaseClient
from shared.pipeline.pipeline import UnifiedDocumentPipeline
from shared.common.connectors.google_drive import GoogleDriveConnector
from pathlib import Path

async def process_specific_documents(doc_ids: list):
    db = DatabaseClient()
    pipeline = UnifiedDocumentPipeline()
    drive = GoogleDriveConnector()
    temp_dir = Path('temp')
    temp_dir.mkdir(exist_ok=True)

    for doc_id in doc_ids:
        # ドキュメント情報取得
        result = db.client.table('Rawdata_FILE_AND_MAIL').select('*').eq('id', doc_id).execute()
        if not result.data:
            print(f'❌ ドキュメントが見つかりません: {doc_id}')
            continue

        doc = result.data[0]
        file_name = doc.get('file_name', 'unknown')
        source_id = doc.get('source_id')

        print(f'\n処理開始: {file_name}')
        print(f'Document ID: {doc_id}')

        # ファイルダウンロード
        try:
            local_path = drive.download_file(source_id, str(temp_dir), file_name)
        except Exception as e:
            print(f'❌ ダウンロード失敗: {e}')
            continue

        # パイプライン処理
        try:
            result = pipeline.process_document(
                file_path=str(local_path),
                source_id=source_id,
                file_name=file_name,
                workspace=doc.get('workspace', 'unknown'),
                doc_type=doc.get('doc_type', 'unknown'),
                existing_document_id=doc_id
            )

            if result.get('success'):
                print(f'✅ 処理成功: {file_name}')
            else:
                print(f'❌ 処理失敗: {result.get("error")}')
        except Exception as e:
            print(f'❌ エラー: {e}')
        finally:
            # 一時ファイル削除
            if local_path.exists():
                local_path.unlink()

async def main():
    doc_ids = [
        'd06aaace-ed96-4434-b884-1fc41c7bbfb1',  # 学年便り　12月.pdf
        'b62a69e6-389a-4358-8ec4-efc501aea98b'   # 洗足学園小保健室山元12月 ほけんだより.pdf
    ]

    await process_specific_documents(doc_ids)
    print('\n処理完了')

if __name__ == '__main__':
    asyncio.run(main())
