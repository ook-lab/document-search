"""OpenAI embeddings for rag-prepare fast-index only (no monorepo `shared/`)."""
from __future__ import annotations

import os
from typing import List, Optional

from openai import OpenAI


class EmbeddingGen:
    """text-embedding-3-small, 1536 dimensions."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        self.client = OpenAI(api_key=self.api_key)
        self.model_name = "text-embedding-3-small"
        self.dimensions = 1536

    def generate_embedding(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        _ = task_type
        if not text or not str(text).strip():
            raise ValueError("空のテキストはembedding化できません")
        response = self.client.embeddings.create(
            model=self.model_name,
            input=text,
            dimensions=self.dimensions,
        )
        return response.data[0].embedding
