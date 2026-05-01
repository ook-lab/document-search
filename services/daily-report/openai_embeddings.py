"""OpenAI embeddings for daily-report only (no monorepo shared/)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIM = 1536


class OpenAIEmbeddings:
    """Same signature as shared LLMClient.generate_embedding for vector search."""

    def __init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for embeddings")
        self._client = OpenAI(api_key=api_key)

    def generate_embedding(
        self, text: str, log_context: Optional[Dict[str, Any]] = None
    ) -> List[float]:
        _ = log_context
        response = self._client.embeddings.create(
            model=_EMBED_MODEL,
            input=text,
            dimensions=_EMBED_DIM,
        )
        return response.data[0].embedding