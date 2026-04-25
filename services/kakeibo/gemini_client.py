"""
Kakeibo 用 Gemini クライアント
レシート OCR（画像→JSON）とテキスト生成のみを実装（shared.ai 不要）
"""
import json
import os
from typing import Dict, Optional

import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig


_log_db = None

def _get_log_db():
    global _log_db
    if _log_db is None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
        _log_db = create_client(url, key)
    return _log_db

def _log(app: str, stage: str, model: str, response) -> None:
    """レスポンスのトークン使用量を ai_usage_logs に記録（失敗しても無視）"""
    try:
        u = getattr(response, 'usage_metadata', None)
        if not u:
            return
        _get_log_db().table('ai_usage_logs').insert({
            'app': app,
            'stage': stage,
            'model': model,
            'prompt_token_count': getattr(u, 'prompt_token_count', 0) or 0,
            'candidates_token_count': getattr(u, 'candidates_token_count', 0) or 0,
            'thoughts_token_count': getattr(u, 'thoughts_token_count', 0) or 0,
            'total_token_count': getattr(u, 'total_token_count', 0) or 0,
        }).execute()
    except Exception:
        pass


class GeminiClient:
    """Gemini API クライアント（Kakeibo専用）"""

    def __init__(self):
                        vertexai.init(location="asia-northeast1")

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

        gen_model = GenerativeModel(model)
        response = gen_model.generate_content(
            [
                {"mime_type": "image/jpeg", "data": image_bytes},
                prompt,
            ],
            generation_config=GenerationConfig(
                temperature=temperature,
                max_output_tokens=32768,
            ),
        )
        _log('kakeibo', 'receipt-ocr', model, response)
        text = response.text
        if not text:
            # 安全フィルターやブロックで candidates が空の場合
            finish = None
            try:
                finish = response.candidates[0].finish_reason if response.candidates else "NO_CANDIDATES"
            except Exception:
                pass
            print(f"[GeminiClient] response.text が空 model={model} finish_reason={finish}")
            return ""
        return text

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
        gen_model = GenerativeModel(model_name)
        response = gen_model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                max_output_tokens=max_output_tokens,
                temperature=0.1,
            ),
        )
        _log('kakeibo', 'text-gen', model_name, response)
        return {"success": True, "content": response.text}
