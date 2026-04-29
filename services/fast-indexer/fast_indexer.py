import logging
import os
from shared.common.database.client import DatabaseClient
from shared.ai.embeddings.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)

class FastIndexer:
    def __init__(self):
        self.db = DatabaseClient(use_service_role=True)
        self.embedder = EmbeddingClient()

    def process_document(self, pipeline_id):
        """OCRをスキップしてMDからベクトル化のみ行う"""
        try:
            # 1. メタデータ取得
            res = self.db.client.table('pipeline_meta').select('*').eq('id', pipeline_id).single().execute()
            if not res.data:
                logger.error(f"Document not found: {pipeline_id}")
                return False
            
            doc = res.data
            md_content = doc.get('md_content')
            if not md_content:
                logger.error(f"No MD content for doc: {pipeline_id}")
                return False

            # 2. チャンク分割 (簡易版)
            chunks = self._split_text(md_content)
            
            # 3. ベクトル化 & 保存
            for i, chunk in enumerate(chunks):
                vector = self.embedder.generate_embedding(chunk)
                self.db.client.table('pipeline_chunks').insert({
                    'pipeline_id': pipeline_id,
                    'chunk_index': i,
                    'content': chunk,
                    'embedding': vector,
                    'metadata': {'source': doc.get('source'), 'page': i+1}
                }).execute()

            # 4. ステータス更新
            self.db.client.table('pipeline_meta').update({
                'processing_status': 'completed',
                'text_embedded': True
            }).eq('id', pipeline_id).execute()

            logger.info(f"Successfully fast-indexed: {pipeline_id}")
            return True

        except Exception as e:
            logger.error(f"Fast index failed: {e}")
            return False

    def _split_text(self, text, chunk_size=1000):
        """簡易的なチャンク分割"""
        return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
