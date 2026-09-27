"""Gemini embeddings for rag-prepare search index registration (no monorepo ``dms/``)."""
from __future__ import annotations

import os
from typing import List

from google import genai
from google.genai import types


class EmbeddingGen:
    """gemini-embedding-2, 1536 dimensions."""

    def __init__(self) -> None:
        self.api_key = (os.environ.get("GOOGLE_AI_PAID_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError("GOOGLE_AI_PAID_API_KEY is not set")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = "gemini-embedding-2"
        self.dimensions = 1536

    def generate_embedding(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        _ = task_type
        if not text or not str(text).strip():
            raise ValueError("空のテキストはembedding化できません")
        content_payload = f"title: none | text: {str(text).strip()}"
        response = self.client.models.embed_content(
            model=self.model_name,
            contents=content_payload,
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
