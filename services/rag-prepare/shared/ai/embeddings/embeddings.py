"""
Embedding Client (DEPRECATED - OpenAI text-embedding-3-small を使用してください)
このクラスは後方互換性のために残されていますが、使用は推奨されません。
代わりに LLMClient.generate_embedding() を使用してください。
"""
from typing import List, Optional
from openai import OpenAI
from shared.common.config.settings import settings


class EmbeddingClient:
    """
    OpenAI text-embedding-3-small を使用したEmbedding生成クライアント (1536次元)

    注意: このクラスは非推奨です。LLMClient.generate_embedding() を使用してください。
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("OpenAI API Key が設定されていません")

        self.client = OpenAI(api_key=self.api_key)
        self.model_name = "text-embedding-3-small"
        self.dimensions = 1536

    def generate_embedding(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        """
        Embeddingを生成 (1536次元)

        注意: task_type パラメータは互換性のために残されていますが、使用されません
        """
        if not text or not text.strip():
            raise ValueError("空のテキストはembedding化できません")

        response = self.client.embeddings.create(
            model=self.model_name,
            input=text,
            dimensions=self.dimensions
        )

        return response.data[0].embedding

    def generate_embeddings_batch(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        """バッチでEmbeddingを生成 (1536次元)"""
        if not texts:
            return []

        embeddings = []
        for text in texts:
            if text and text.strip():
                embedding = self.generate_embedding(text, task_type)
                embeddings.append(embedding)
            else:
                # OpenAI text-embedding-3-smallは1536次元
                embeddings.append([0.0] * 1536)

        return embeddings

    def generate_query_embedding(self, query: str) -> List[float]:
        """クエリ用のEmbeddingを生成 (1536次元)"""
        return self.generate_embedding(query, task_type="RETRIEVAL_QUERY")
