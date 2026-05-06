"""doc-search 専用 LLM（埋め込み・回答生成）。コスト記録は行わない。"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional

import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from loguru import logger
from openai import OpenAI

from docsearch.config import settings
from docsearch.models import AIProvider, get_model_config


class DocSearchLLM:
    def __init__(self) -> None:
        self.openai_api_key = settings.OPENAI_API_KEY
        genai.configure(api_key=settings.GOOGLE_AI_API_KEY)
        self.gemini_api_key = bool(settings.GOOGLE_AI_API_KEY)
        self.openai_client = OpenAI(api_key=self.openai_api_key) if self.openai_api_key else None

    def call_model(
        self,
        tier: str,
        prompt: str,
        file_path: Optional[Path] = None,
        log_context: Optional[Dict] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        _ = log_context
        config = get_model_config(tier)
        provider = config["provider"]
        model_name = kwargs.pop("model_name", None) or config["model"]
        if model_name:
            if "gemini" in model_name.lower():
                provider = AIProvider.GEMINI
            elif "gpt" in model_name.lower() or "text-embedding" in model_name.lower():
                provider = AIProvider.OPENAI

        if provider == AIProvider.GEMINI:
            if not self.gemini_api_key:
                return {"success": False, "error": "Gemini API key is missing", "model": model_name}
            return self._call_gemini(model_name, prompt, file_path, config, **kwargs)
        if provider == AIProvider.OPENAI:
            if not self.openai_client:
                return {"success": False, "error": "OpenAI API key is missing", "model": model_name}
            return self._call_openai(model_name, prompt, config, **kwargs)
        return {"success": False, "error": f"unsupported provider: {provider}", "model": model_name}

    def _call_gemini(
        self,
        model_name: str,
        prompt: str,
        file_path: Optional[Path],
        config: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        uploaded_file = None
        try:
            model = genai.GenerativeModel(model_name)
            content_parts: List[Any] = [prompt]
            if file_path and file_path.exists():
                mime_type, _ = mimetypes.guess_type(str(file_path))
                if not mime_type:
                    mime_type = "application/pdf"
                with open(str(file_path), "rb") as f:
                    file_data = f.read()
                uploaded_file = {"mime_type": mime_type, "data": file_data}
                content_parts.append(uploaded_file)

            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            max_out = int(kwargs.pop("max_tokens", config.get("max_tokens", 65536)))
            temp = float(config.get("temperature", 0.1))
            generation_config = GenerationConfig(max_output_tokens=max_out, temperature=temp)
            response_format = kwargs.get("response_format")
            if response_format in ("json", "json_object"):
                generation_config = GenerationConfig(
                    max_output_tokens=max_out,
                    temperature=temp,
                    response_mime_type="application/json",
                )

            response = model.generate_content(
                content_parts,
                generation_config=generation_config,
                safety_settings=safety_settings,
            )
            if not response.candidates:
                return {"success": False, "error": "Gemini returned no candidates", "model": model_name, "provider": "gemini"}
            candidate = response.candidates[0]
            if candidate.finish_reason != 1:
                return {
                    "success": False,
                    "error": f"Gemini finish_reason: {candidate.finish_reason}",
                    "model": model_name,
                    "provider": "gemini",
                }
            text_content = candidate.content.parts[0].text if candidate.content.parts else ""
            return {"success": True, "content": text_content, "model": model_name, "provider": "gemini"}
        except Exception as e:
            logger.error("Gemini error: {}", e)
            return {"success": False, "error": str(e), "model": model_name, "provider": "gemini"}
        finally:
            _ = uploaded_file

    def _call_openai(
        self,
        model_name: str,
        prompt: str,
        config: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        _ = kwargs
        try:
            max_completion_tokens = config.get("max_completion_tokens")
            max_tokens = config.get("max_tokens", 16384)
            api_params: Dict[str, Any] = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
            }
            if "temperature" in config:
                api_params["temperature"] = config["temperature"]
            if max_completion_tokens:
                api_params["max_completion_tokens"] = max_completion_tokens
            else:
                api_params["max_tokens"] = max_tokens
            assert self.openai_client is not None
            response = self.openai_client.chat.completions.create(**api_params)
            return {
                "success": True,
                "content": response.choices[0].message.content,
                "model": model_name,
                "provider": "openai",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "model": model_name, "provider": "openai"}

    def generate_embedding(self, text: str, log_context: Optional[Dict] = None) -> List[float]:
        _ = log_context
        cfg = get_model_config("embeddings")
        if not self.openai_client:
            raise ConnectionError("OpenAI client not initialized for embedding generation.")
        response = self.openai_client.embeddings.create(
            model=cfg["model"],
            input=text,
            dimensions=cfg.get("dimensions", 1536),
        )
        return response.data[0].embedding
