"""
ドキュメントのmetadataを確認するスクリプト
"""
import sys
import json
from pathlib import Path

# プロジェクトルートをPYTHONPATHに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.common.db_client import DatabaseClient

def check_document(doc_id: str):
    """ドキュメントのmetadataを確認"""
    db = DatabaseClient(use_service_role=True)

    doc = db.get_document_by_id(doc_id)
    if not doc:
        print(f'❌ ドキュメントが見つかりませんでした: {doc_id}')
        return

    print(f'\n=== ドキュメント情報 ===')
    print(f'ID: {doc_id}')
    print(f'ファイル名: {doc.get("file_name")}')
    print(f'doc_type: {doc.get("doc_type")}')
    print(f'processing_status: {doc.get("processing_status")}')

    # metadataを取得
    metadata = doc.get('metadata', {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except:
            print('⚠️ metadataのJSON解析に失敗')
            metadata = {}

    print(f'\n=== metadata のキー ===')
    print(f'キー一覧: {list(metadata.keys())}')

    # articles の確認
    if 'articles' in metadata:
        articles = metadata['articles']
        print(f'\n✅ articles あり: {len(articles)}件')
        for i, article in enumerate(articles[:3]):
            title = article.get('title', '(タイトルなし)')
            body = article.get('body', '')
            print(f'\n  [{i}] タイトル: {title}')
            print(f'      本文: {body[:200]}...' if len(body) > 200 else f'      本文: {body}')
    else:
        print('\n❌ articles なし')

    # text_blocks の確認
    if 'text_blocks' in metadata:
        text_blocks = metadata['text_blocks']
        print(f'\n⚠️ text_blocks あり: {len(text_blocks)}件（本来は articles に変換されるべき）')
        for i, block in enumerate(text_blocks[:3]):
            text = block.get('text', '') if isinstance(block, dict) else str(block)
            print(f'  [{i}] {text[:200]}...' if len(text) > 200 else f'  [{i}] {text}')
    else:
        print('\n✅ text_blocks なし（正しい）')

    # calendar_events と tasks の確認
    if 'calendar_events' in metadata:
        print(f'\n✅ calendar_events あり: {len(metadata["calendar_events"])}件')
    else:
        print('\n⚠️ calendar_events なし')

    if 'tasks' in metadata:
        print(f'\n✅ tasks あり: {len(metadata["tasks"])}件')
    else:
        print('\n⚠️ tasks なし')

    # 全文の総文字数を計算
    total_chars = 0
    if 'articles' in metadata:
        for article in metadata['articles']:
            body = article.get('body', '')
            total_chars += len(body)
    elif 'text_blocks' in metadata:
        for block in metadata['text_blocks']:
            text = block.get('text', '') if isinstance(block, dict) else str(block)
            total_chars += len(text)

    print(f'\n=== 全文統計 ===')
    print(f'総文字数: {total_chars}文字')

    if total_chars < 100:
        print('⚠️ 警告: 全文が極端に少ないです。データが失われている可能性があります。')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('使用法: python scripts/debug/check_document_metadata.py <document_id>')
        sys.exit(1)

    doc_id = sys.argv[1]
    check_document(doc_id)
