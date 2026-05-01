"""Text-only Gemini for kakeibo product generalization (no shared.ai)."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig


class KakeiboGeminiTextClient:
    """Minimal LLM surface matching TransactionProcessor expectations."""

    def __init__(self) -> None:
        vertexai.init(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("VERTEX_AI_REGION", "us-central1"),
        )

    def call_model(
        self,
        tier: Optional[str] = None,
        prompt: str = "",
        model_name: str = "gemini-2.5-flash-lite",
        max_output_tokens: int = 8192,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        _ = tier
        _ = kwargs
        try:
            gen_model = GenerativeModel(model_name)
            response = gen_model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    max_output_tokens=max_output_tokens,
                    temperature=0.1,
                ),
            )
            text = (response.text or "").strip()
            return {"success": True, "content": text}
        except Exception as e:
            return {"success": False, "error": str(e), "model": model_name}