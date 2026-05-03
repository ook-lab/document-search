"""Text-only Gemini for kakeibo product generalization (no shared.ai)."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import google.generativeai as genai


class KakeiboGeminiTextClient:
    """Minimal LLM surface matching TransactionProcessor expectations."""

    def __init__(self) -> None:
        api_key = os.environ.get("GOOGLE_AI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY is not set")
        genai.configure(api_key=api_key)

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
            gen_model = genai.GenerativeModel(model_name)
            response = gen_model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_output_tokens,
                    temperature=0.1,
                ),
            )
            text = (response.text or "").strip()
            return {"success": True, "content": text}
        except Exception as e:
            return {"success": False, "error": str(e), "model": model_name}