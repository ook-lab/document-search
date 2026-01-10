"""
既存の本文チャンク（content_small, content_large）を削除

実行方法:
    python delete_content_chunks.py --limit 5
"""
import sys
from pathlib import Path
import logging
import argparse

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import DatabaseClient


def delete_content_chunks(limit: int = None):
    """
    本文チャンク（content_small, content_large）を削除

    Args:
        limit: 処理する文書数の上限
    """
    logger.info("="*80)
    logger.info("本文チャンク削除処理開始")
    logger.info("="*80)

    # データベース接続
    db = DatabaseClient(use_service_role=True)

    # attachment_text有りの文書を取得
    query = db.client.table('Rawdata_FILE_AND_MAIL').select('id, file_name').not_.is_('attachment_text', 'null')

    if limit:
        query = query.limit(limit)

    docs_result = query.execute()

    if not docs_result.data:
        logger.info("処理対象の文書がありません")
        return

    logger.info(f"処理対象文書: {len(docs_result.data)}件")

    total_deleted = 0

    for i, doc in enumerate(docs_result.data, 1):
        doc_id = doc['id']
        file_name = doc['file_name']

        logger.info(f"  [{i}/{len(docs_result.data)}] {file_name}")

        try:
            # content_small チャンクを削除
            small_result = db.client.table('10_ix_search_index').delete().eq('document_id', doc_id).eq('chunk_type', 'content_small').execute()
            small_count = len(small_result.data) if small_result.data else 0

            # content_large チャンクを削除
            large_result = db.client.table('10_ix_search_index').delete().eq('document_id', doc_id).eq('chunk_type', 'content_large').execute()
            large_count = len(large_result.data) if large_result.data else 0

            deleted_count = small_count + large_count
            total_deleted += deleted_count

            logger.info(f"    削除: content_small={small_count}個, content_large={large_count}個")

        except Exception as e:
            logger.error(f"    ❌ エラー: {e}")

    logger.info("")
    logger.info("="*80)
    logger.info(f"✅ 削除完了: 合計{total_deleted}個のチャンクを削除")
    logger.info("="*80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='本文チャンク削除')
    parser.add_argument('--limit', type=int, help='処理する文書数の上限')
    args = parser.parse_args()

    delete_content_chunks(limit=args.limit)
