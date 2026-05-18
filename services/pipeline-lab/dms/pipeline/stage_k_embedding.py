"""
チャンクをベクトル化して `10_ix_search_index` に保存（09_unified_documents に行があることのみ許可）。

互換のためクラス名は `StageKEmbedding` のまま。パイプライン表記の「Stage K」は廃止。

- モデル: OpenAI text-embedding-3-small (1536次元)
- 書き込み先: 10_ix_search_index (doc_id = 09_unified_documents.id、FK と require_unified_document_before_ix_write で担保)
"""
from typing import Dict, Any, List, Optional
from loguru import logger

from dms.ai.llm_client.llm_client import LLMClient
from dms.common.database.client import DatabaseClient


class StageKEmbedding:
    """チャンクを埋め込みベクトル化し 10_ix に保存する。"""

    def __init__(self, llm_client: LLMClient, db_client: DatabaseClient):
        self.llm_client = llm_client
        self.db = db_client

    def embed_and_save(
        self,
        doc_id: str,
        chunks: List[Dict[str, Any]],
        person: Optional[str] = None,
        classification1: Optional[str] = None,
        classification2: Optional[str] = None,
        classification3: Optional[str] = None,
        delete_existing: bool = True,
    ) -> Dict[str, Any]:
        """
        チャンクをベクトル化して 10_ix_search_index に保存

        Args:
            doc_id:          09_unified_documents.id
            chunks:          チャンクリスト
            person:          09_unified_documents.person（非正規化）
            classification1..3: 09 と同じ非正規化（旧 source / course / category に相当）
            delete_existing: 既存チャンクを先に削除するか

        Returns:
            {'success': bool, 'saved_count': int, 'failed_count': int}
        """
        logger.info(f"[Embedding] ベクトル化開始: doc_id={doc_id}, chunks={len(chunks)}")

        self.db.require_unified_document_before_ix_write(doc_id)

        if person is None or classification1 is None or classification3 is None:
            try:
                row = (
                    self.db.client
                    .table('09_unified_documents')
                    .select('person, classification1, classification2, classification3')
                    .eq('id', doc_id)
                    .execute()
                )
                if row.data:
                    r = row.data[0]
                    person = person or r.get('person')
                    classification1 = classification1 or r.get('classification1')
                    classification3 = classification3 or r.get('classification3')
                    if classification2 is None:
                        classification2 = r.get('classification2')
            except Exception as e:
                logger.warning(f"[Embedding] 09 からの取得失敗（継続）: {e}")

        # 既存チャンクを削除
        if delete_existing:
            try:
                self.db.client.table('10_ix_search_index').delete().eq('doc_id', doc_id).execute()
                logger.info(f"[Embedding] 既存チャンク削除: doc_id={doc_id}")
            except Exception as e:
                logger.warning(f"[Embedding] 既存チャンク削除エラー（継続）: {e}")

        saved_count  = 0
        failed_count = 0
        errors       = []

        for chunk in chunks:
            try:
                chunk_text = chunk.get('chunk_text', '').replace('\u0000', '')
                if not chunk_text.strip():
                    logger.warning(f"[Embedding] 空チャンクをスキップ: type={chunk.get('chunk_type')}")
                    continue

                embedding = self.llm_client.generate_embedding(chunk_text)

                chunk_data = {
                    'doc_id':       doc_id,
                    'person':       person,
                    'classification1': classification1,
                    'classification2': classification2,
                    'classification3': classification3,
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
                logger.error(f"[Embedding] チャンク保存失敗: {err_msg}")
                errors.append(err_msg)
                failed_count += 1

        logger.info(f"[Embedding完了] {saved_count}/{len(chunks)}チャンクを保存 (失敗: {failed_count})")

        return {
            'success':      saved_count > 0 and failed_count == 0,
            'saved_count':  saved_count,
            'failed_count': failed_count,
            'errors':       errors,
        }

    def process(self, chunks: List[Dict[str, Any]], doc_id: str) -> None:
        """process() エイリアス"""
        self.embed_and_save(doc_id, chunks)
