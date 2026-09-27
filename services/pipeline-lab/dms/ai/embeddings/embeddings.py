"""
Embedding Client (gemini-embedding-2, 1536次元)
"""
import os
from typing import List, Optional
from google import genai
from google.genai import types


class EmbeddingClient:
    """
    Gemini gemini-embedding-2 を使用したEmbedding生成クライアント (1536次元)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("GOOGLE_AI_PAID_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError("GOOGLE_AI_PAID_API_KEY is not set")

        self.client = genai.Client(api_key=self.api_key)
        self.model_name = "gemini-embedding-2"
        self.dimensions = 1536

    def generate_embedding(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        """
        Embeddingを生成 (1536次元)
        """
        if not text or not str(text).strip():
            raise ValueError("空のテキストはembedding化できません")

        clean_text = str(text).strip()
        if task_type == "RETRIEVAL_DOCUMENT":
            payload = f"title: none | text: {clean_text}"
        elif task_type == "RETRIEVAL_QUERY":
            payload = f"task: search result | query: {clean_text}"
        else:
            raise ValueError(f"Invalid task_type: {task_type}. Must be 'RETRIEVAL_DOCUMENT' or 'RETRIEVAL_QUERY'")

        response = self.client.models.embed_content(
            model=self.model_name,
            contents=payload,
            config=types.EmbedContentConfig(output_dimensionality=self.dimensions),
        )

        if not response.embeddings or len(response.embeddings) != 1:
            cnt = len(response.embeddings) if response.embeddings else 0
            raise ValueError(f"Invalid embedding response: expected 1 embedding, got {cnt}")

        emb = response.embeddings[0]
        values = getattr(emb, "values", None)
        if values is None or len(values) != self.dimensions:
            dim = len(values) if values else 0
            raise ValueError(f"Invalid embedding dimensionality: expected {self.dimensions}, got {dim}")

        return list(values)

    def generate_embeddings_batch(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        """バッチでEmbeddingを生成 (1536次元、1リクエスト1テキストで処理、空テキストは例外)"""
        if not texts:
            return []

        embeddings = []
        for text in texts:
            if not text or not str(text).strip():
                raise ValueError("空のテキストはembedding化できません（フォールバック・ゼロベクトル禁止）")
            embedding = self.generate_embedding(text, task_type)
            embeddings.append(embedding)

        return embeddings

    def generate_query_embedding(self, query: str) -> List[float]:
        """クエリ用のEmbeddingを生成 (1536次元)"""
        return self.generate_embedding(query, task_type="RETRIEVAL_QUERY")
