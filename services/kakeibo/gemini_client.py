"""
Kakeibo 用 Gemini クライアント
レシート OCR（画像→JSON）とテキスト生成のみを実装（shared.ai 不要）
"""
import json
import os
from typing import Dict, Optional

import google.generativeai as genai


class GeminiClient:
    """Gemini API クライアント（Kakeibo専用）"""

    def __init__(self):
        api_key = os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY が設定されていません")
        genai.configure(api_key=api_key)

    # ── レシート OCR（画像 → テキスト）────────────────────────

    def generate_with_images(
        self,
        prompt: str,
        image_data: str,          # base64エンコード済み
        model: str = "gemini-2.5-flash-lite",
        temperature: float = 0.1,
    ) -> str:
        """
        レシート画像を読み取ってテキストを返す。
        image_data は base64文字列（data:image/...;base64,... 形式も可）。
        """
        # "data:image/jpeg;base64," プレフィックスを除去
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]

        import base64
        image_bytes = base64.b64decode(image_data)

        gen_model = genai.GenerativeModel(model)
        response = gen_model.generate_content(
            [
                {"mime_type": "image/jpeg", "data": image_bytes},
                prompt,
            ],
            generation_config=genai.GenerationConfig(temperature=temperature),
        )
        return response.text

    # ── テキスト生成（商品名一般化など）──────────────────────

    def call_model(
        self,
        prompt: str,
        model_name: str = "gemini-2.5-flash",
        max_output_tokens: int = 8192,
        **_kwargs,                # tier など余分なキーワードを無視
    ) -> Dict:
        """
        テキストプロンプトを送信して結果を返す。
        戻り値: {"success": bool, "content": str}
        """
        gen_model = genai.GenerativeModel(model_name)
        response = gen_model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=max_output_tokens,
                temperature=0.1,
            ),
        )
        return {"success": True, "content": response.text}
