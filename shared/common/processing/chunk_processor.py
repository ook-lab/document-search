"""
チャンク処理モジュール

ドキュメントを小チャンクに分割し、embeddingを生成してデータベースに保存する
"""

from typing import List, Dict, Optional
import asyncio
from loguru import logger

from shared.common.utils.chunking import TextChunker
from shared.ai.llm_client.llm_client import LLMClient
from shared.common.database.client import DatabaseClient


class ChunkProcessor:
    """
    ドキュメントをチャンク分割してembedding生成するクラス
    """

    def __init__(
        self,
        chunk_size: int = 300,
        overlap: int = 50,
        max_concurrent: int = 3
    ):
        """
        Args:
            chunk_size: 1チャンクの文字数（デフォルト300文字）
            overlap: チャンク間のオーバーラップ文字数（デフォルト50文字）
            max_concurrent: 同時実行可能なembedding生成数（レート制限対策）
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.llm_client = LLMClient()
        self.db = DatabaseClient()
        self.max_concurrent = max_concurrent

    async def process_document(
        self,
        document_id: str,
        full_text: str,
        force_reprocess: bool = False,
        owner_id: str = None
    ) -> Dict[str, any]:
        """
        ドキュメントをチャンク分割してembedding生成

        1. テキストを小チャンクに分割
        2. 各チャンクのembeddingを生成
        3. データベースに保存

        Args:
            document_id: ドキュメントID（UUID）
            full_text: ドキュメント全文
            force_reprocess: Trueの場合、既存のチャンクを削除して再処理
            owner_id: オーナーID（省略時は親ドキュメントから継承）

        Returns:
            {
                "success": True/False,
                "document_id": "...",
                "chunks_created": 10,
                "chunks_failed": 0,
                "error": "..." (失敗時のみ)
            }
        """
        try:
            logger.info(f"[ChunkProcessor] Processing document: {document_id}")

            # Phase 3: owner_id を取得（指定がない場合は親ドキュメントから継承）
            if owner_id is None:
                try:
                    parent_doc = self.db.client.table('Rawdata_FILE_AND_MAIL')\
                        .select('owner_id')\
                        .eq('id', document_id)\
                        .execute()
                    if parent_doc.data:
                        owner_id = parent_doc.data[0].get('owner_id')
                        logger.debug(f"[ChunkProcessor] 親ドキュメントから owner_id 継承: {owner_id}")
                except Exception as e:
                    logger.warning(f"[ChunkProcessor] 親ドキュメントの owner_id 取得エラー: {e}")

            if owner_id is None:
                logger.error(f"[ChunkProcessor] owner_id が取得できません: document_id={document_id}")
                return {
                    "success": False,
                    "document_id": document_id,
                    "chunks_created": 0,
                    "chunks_failed": 0,
                    "error": "owner_id is required but not available"
                }

            # 既存のチャンクを確認
            if not force_reprocess:
                existing_chunks = self.db.client.table('10_ix_search_index').select('id').eq('document_id', document_id).execute()
                if existing_chunks.data:
                    logger.info(f"[ChunkProcessor] Document {document_id} already has {len(existing_chunks.data)} chunks. Skipping.")
                    return {
                        "success": True,
                        "document_id": document_id,
                        "chunks_created": 0,
                        "chunks_failed": 0,
                        "message": "Already processed"
                    }

            # 再処理の場合は既存チャンクを削除
            if force_reprocess:
                logger.info(f"[ChunkProcessor] Deleting existing chunks for document {document_id}")
                self.db.client.table('10_ix_search_index').delete().eq('document_id', document_id).execute()

            # テキストを小チャンクに分割
            logger.info(f"[ChunkProcessor] Splitting text into chunks (size={self.chunk_size}, overlap={self.overlap})")
            chunker = TextChunker(chunk_size=self.chunk_size, chunk_overlap=self.overlap)
            chunks = chunker.split_text(full_text)

            if not chunks:
                logger.warning(f"[ChunkProcessor] No chunks created for document {document_id}")
                return {
                    "success": False,
                    "document_id": document_id,
                    "chunks_created": 0,
                    "chunks_failed": 0,
                    "error": "No chunks created (empty document?)"
                }

            logger.info(f"[ChunkProcessor] Created {len(chunks)} chunks")

            # 各チャンクのembeddingを生成（並列実行）
            chunks_with_embeddings = await self._generate_embeddings_for_chunks(chunks)

            # データベースに保存
            chunks_created = 0
            chunks_failed = 0

            for chunk_data in chunks_with_embeddings:
                if chunk_data.get("embedding"):
                    try:
                        chunk_text = chunk_data.get("chunk_text", chunk_data.get("content", ""))
                        self.db.client.table('10_ix_search_index').insert({
                            "document_id": document_id,
                            "owner_id": owner_id,  # Phase 3: 親ドキュメントから継承
                            "chunk_index": chunk_data["chunk_index"],
                            "chunk_content": chunk_text,
                            "chunk_size": len(chunk_text),
                            "chunk_type": "content_small",
                            "search_weight": 1.0,
                            "embedding": chunk_data["embedding"]
                        }).execute()
                        chunks_created += 1
                    except Exception as e:
                        logger.error(f"[ChunkProcessor] Failed to save chunk {chunk_data['chunk_index']}: {e}")
                        chunks_failed += 1
                else:
                    logger.warning(f"[ChunkProcessor] Chunk {chunk_data['chunk_index']} has no embedding, skipping")
                    chunks_failed += 1

            logger.info(f"[ChunkProcessor] Document {document_id} processed: {chunks_created} chunks created, {chunks_failed} failed")

            return {
                "success": True,
                "document_id": document_id,
                "chunks_created": chunks_created,
                "chunks_failed": chunks_failed
            }

        except Exception as e:
            logger.error(f"[ChunkProcessor] Error processing document {document_id}: {e}")
            return {
                "success": False,
                "document_id": document_id,
                "chunks_created": 0,
                "chunks_failed": 0,
                "error": str(e)
            }

    async def _generate_embeddings_for_chunks(
        self,
        chunks: List[Dict[str, any]]
    ) -> List[Dict[str, any]]:
        """
        チャンクリストに対してembeddingを生成（並列処理）

        Args:
            chunks: チャンクリスト

        Returns:
            embeddingが追加されたチャンクリスト
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def generate_single_embedding(chunk: Dict[str, any]) -> Dict[str, any]:
            async with semaphore:
                try:
                    # チャンクのテキストを取得（既存の形式に対応）
                    chunk_text = chunk.get("chunk_text", chunk.get("content", ""))
                    if not chunk_text:
                        raise ValueError("Chunk has no text content")

                    # LLMClient.generate_embeddingは同期関数なのでasyncio.to_threadで実行
                    embedding = await asyncio.to_thread(
                        self.llm_client.generate_embedding,
                        chunk_text
                    )
                    chunk["embedding"] = embedding
                    logger.debug(f"[ChunkProcessor] Generated embedding for chunk {chunk['chunk_index']}")
                except Exception as e:
                    logger.error(f"[ChunkProcessor] Failed to generate embedding for chunk {chunk['chunk_index']}: {e}")
                    chunk["embedding"] = None

                return chunk

        # 並列でembedding生成
        tasks = [generate_single_embedding(chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 例外が発生したタスクをフィルタリング
        chunks_with_embeddings = [
            result for result in results
            if not isinstance(result, Exception) or logger.error(f"[ChunkProcessor] Exception during embedding generation: {result}")
        ]

        return chunks_with_embeddings

    def delete_document_chunks(self, document_id: str) -> bool:
        """
        指定されたドキュメントの全チャンクを削除

        Args:
            document_id: ドキュメントID

        Returns:
            成功/失敗
        """
        try:
            self.db.client.table('10_ix_search_index').delete().eq('document_id', document_id).execute()
            logger.info(f"[ChunkProcessor] Deleted all chunks for document {document_id}")
            return True
        except Exception as e:
            logger.error(f"[ChunkProcessor] Failed to delete chunks for document {document_id}: {e}")
            return False

    def get_document_chunks(self, document_id: str) -> List[Dict[str, any]]:
        """
        指定されたドキュメントの全チャンクを取得

        Args:
            document_id: ドキュメントID

        Returns:
            チャンクリスト
        """
        try:
            result = self.db.client.table('10_ix_search_index').select('*').eq('document_id', document_id).order('chunk_index').execute()
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"[ChunkProcessor] Failed to get chunks for document {document_id}: {e}")
            return []
