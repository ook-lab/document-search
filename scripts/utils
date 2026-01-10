"""
既存文書に本文チャンクを追加

現在データベースにある文書のうち、メタデータチャンクしか持っていない文書に対して、
本文（attachment_text）のチャンクを追加で作成します。

実行方法:
    python reprocess_existing_documents_add_content_chunks.py [--limit 10] [--dry-run]
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

from A_common.database.client import DatabaseClient
from C_ai_common.llm_client.llm_client import LLMClient
from A_common.utils.chunking import TextChunker


def reprocess_documents(limit: int = None, dry_run: bool = False):
    """
    既存文書に本文チャンクを追加

    Args:
        limit: 処理する文書数の上限（Noneの場合は全件）
        dry_run: Trueの場合は実際の処理は行わず、対象を表示するのみ
    """
    logger.info("=" * 80)
    logger.info("既存文書の本文チャンク追加処理開始")
    logger.info("=" * 80)

    db = DatabaseClient(use_service_role=True)
    llm_client = LLMClient()

    # 本文チャンクが存在しない文書を取得
    logger.info("本文チャンクがない文書を検索中...")

    # まず全文書を取得
    all_docs_query = db.client.table('Rawdata_FILE_AND_MAIL').select('id, file_name, attachment_text')

    # attachment_textが存在する文書のみ
    all_docs_query = all_docs_query.not_.is_('attachment_text', 'null')

    if limit:
        all_docs_query = all_docs_query.limit(limit * 2)  # 余裕を持って取得

    all_docs = all_docs_query.execute()

    logger.info(f"attachment_text有りの文書: {len(all_docs.data)}件")

    # 本文チャンクがあるかチェック
    docs_to_process = []
    for doc in all_docs.data:
        doc_id = doc['id']

        # この文書のcontent_smallチャンクがあるか確認
        content_chunks = db.client.table('10_ix_search_index').select('id').eq('document_id', doc_id).eq('chunk_type', 'content_small').limit(1).execute()

        if not content_chunks.data:
            # 本文チャンクがない
            docs_to_process.append(doc)

            if limit and len(docs_to_process) >= limit:
                break

    logger.info(f"本文チャンクがない文書: {len(docs_to_process)}件")

    if not docs_to_process:
        logger.info("✅ 処理対象の文書がありません")
        return

    if dry_run:
        logger.warning("🔍 DRY RUN モード: 実際の処理は行いません")
        logger.info("\n処理対象の文書:")
        for i, doc in enumerate(docs_to_process[:10], 1):
            text_len = len(doc.get('attachment_text', ''))
            logger.info(f"  {i}. {doc['file_name']} ({text_len}文字)")
        if len(docs_to_process) > 10:
            logger.info(f"  ... 他 {len(docs_to_process) - 10}件")
        return

    # 本文チャンク作成
    logger.info(f"\n本文チャンク作成開始: {len(docs_to_process)}件")

    success_count = 0
    failed_count = 0

    for i, doc in enumerate(docs_to_process, 1):
        doc_id = doc['id']
        file_name = doc['file_name']
        attachment_text = doc.get('attachment_text', '')

        if not attachment_text or len(attachment_text.strip()) < 50:
            logger.warning(f"  [{i}/{len(docs_to_process)}] スキップ: {file_name} (本文が短い)")
            continue

        logger.info(f"  [{i}/{len(docs_to_process)}] 処理中: {file_name} ({len(attachment_text)}文字)")

        try:
            # 既存のチャンク数を取得（chunk_indexの開始位置を決定）
            existing_chunks = db.client.table('10_ix_search_index').select('chunk_index').eq('document_id', doc_id).order('chunk_index', desc=True).limit(1).execute()

            if existing_chunks.data:
                current_chunk_index = existing_chunks.data[0]['chunk_index'] + 1
            else:
                current_chunk_index = 0

            # 小チャンク作成（150文字ずつ、オーバーラップ30文字）
            chunker = TextChunker(chunk_size=150, chunk_overlap=30)
            small_chunks = chunker.split_text(attachment_text)

            logger.info(f"    小チャンク数: {len(small_chunks)}個")

            # 小チャンクをembedding化して保存
            small_chunk_count = 0
            for chunk_dict in small_chunks:
                chunk_text = chunk_dict['chunk_text']
                if not chunk_text.strip():
                    continue

                try:
                    # Embedding生成
                    chunk_embedding = llm_client.generate_embedding(chunk_text)

                    # search_indexに保存
                    chunk_doc = {
                        'document_id': doc_id,
                        'chunk_index': current_chunk_index,
                        'chunk_content': chunk_text,
                        'chunk_size': len(chunk_text),
                        'chunk_type': 'content_small',
                        'embedding': chunk_embedding,
                        'search_weight': 1.0
                    }

                    db.client.table('10_ix_search_index').insert(chunk_doc).execute()
                    current_chunk_index += 1
                    small_chunk_count += 1
                except Exception as e:
                    logger.error(f"    小チャンク保存エラー: {e}")

            logger.info(f"    ✅ 完了: 小チャンク{small_chunk_count}個")
            success_count += 1

        except Exception as e:
            logger.error(f"    ❌ エラー: {e}")
            failed_count += 1

    logger.info("\n" + "=" * 80)
    logger.info(f"✅ 処理完了: 成功{success_count}件 / 失敗{failed_count}件")
    logger.info("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='既存文書に本文チャンクを追加')
    parser.add_argument('--limit', type=int, help='処理する文書数の上限')
    parser.add_argument('--dry-run', action='store_true', help='実際の処理は行わず、対象を表示するのみ')
    args = parser.parse_args()

    reprocess_documents(limit=args.limit, dry_run=args.dry_run)
