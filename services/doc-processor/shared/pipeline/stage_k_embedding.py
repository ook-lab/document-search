"""
Stage K: Embedding (ベクトル化)

チャンクをベクトル化して 10_ix_search_index に保存
- 役割: チャンクをベクトル化
- モデル: OpenAI text-embedding-3-small (1536次元)
- 書き込み先: 10_ix_search_index (doc_id = 09_unified_documents.id)
"""
from typing import Dict, Any, List, Optional
from loguru import logger

from shared.ai.llm_client.llm_client import LLMClient
from shared.common.database.client import DatabaseClient


class StageKEmbedding:
    """Stage K: ベクトル化"""

    def __init__(self, llm_client: LLMClient, db_client: DatabaseClient):
        self.llm_client = llm_client
        self.db = db_client

    def embed_and_save(
        self,
        doc_id: str,
        chunks: List[Dict[str, Any]],
        person: Optional[str] = None,
        source: Optional[str] = None,
        category: Optional[str] = None,
        delete_existing: bool = True,
    ) -> Dict[str, Any]:
        """
        チャンクをベクトル化して 10_ix_search_index に保存

        Args:
            doc_id:          09_unified_documents.id
            chunks:          チャンクリスト
            person:          09_unified_documents.person（非正規化）
            source:          09_unified_documents.source（非正規化）
            category:        09_unified_documents.category（非正規化）
            delete_existing: 既存チャンクを先に削除するか

        Returns:
            {'success': bool, 'saved_count': int, 'failed_count': int}
        """
        logger.info(f"[Stage K] ベクトル化開始: doc_id={doc_id}, chunks={len(chunks)}")

        # person/source/category が未指定なら 09 から取得
        if person is None or source is None or category is None:
            try:
                row = (
                    self.db.client
                    .table('09_unified_documents')
                    .select('person, source, category')
                    .eq('id', doc_id)
                    .execute()
                )
                if row.data:
                    r = row.data[0]
                    person   = person   or r.get('person')
                    source   = source   or r.get('source')
                    category = category or r.get('category')
            except Exception as e:
                logger.warning(f"[Stage K] 09 からの取得失敗（継続）: {e}")

        # 既存チャンクを削除
        if delete_existing:
            try:
                self.db.client.table('10_ix_search_index').delete().eq('doc_id', doc_id).execute()
                logger.info(f"[Stage K] 既存チャンク削除: doc_id={doc_id}")
            except Exception as e:
                logger.warning(f"[Stage K] 既存チャンク削除エラー（継続）: {e}")

        saved_count  = 0
        failed_count = 0
        errors       = []

        for chunk in chunks:
            try:
                chunk_text = chunk.get('chunk_text', '').replace('\u0000', '')
                if not chunk_text.strip():
                    logger.warning(f"[Stage K] 空チャンクをスキップ: type={chunk.get('chunk_type')}")
                    continue

                embedding = self.llm_client.generate_embedding(chunk_text)

                chunk_data = {
                    'doc_id':       doc_id,
                    'person':       person,
                    'source':       source,
                    'category':     category,
                    'chunk_index':  chunk.get('chunk_index', 0),
                    'chunk_text':   chunk_text,
                    'chunk_type':   chunk.get('chunk_type'),
                    'chunk_weight': chunk.get('search_weight', 1.0),
                    'embedding':    embedding,
                }

                self.db.client.table('10_ix_search_index').insert(chunk_data).execute()
                saved_count += 1

            except Exception as e:
                err_msg = f"type={chunk.get('chunk_type')}: {e}"
                logger.error(f"[Stage K] チャンク保存失敗: {err_msg}")
                errors.append(err_msg)
                failed_count += 1

        logger.info(f"[Stage K完了] {saved_count}/{len(chunks)}チャンクを保存 (失敗: {failed_count})")

        return {
            'success':      saved_count > 0 and failed_count == 0,
            'saved_count':  saved_count,
            'failed_count': failed_count,
            'errors':       errors,
        }

    def process(self, chunks: List[Dict[str, Any]], doc_id: str) -> None:
        """process() エイリアス"""
        self.embed_and_save(doc_id, chunks)
