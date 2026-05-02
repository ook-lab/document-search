"""
LLMクライアント（v3.0: マルチプロバイダ対応）
Gemini / Anthropic / OpenAI を統一インターフェースで利用
"""

import os
import base64
from typing import Dict, List, Any, Optional, Union
from pathlib import Path
import mimetypes

import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger

from shared.common.config.model_tiers import AIProvider, get_model_config
from shared.common.config.settings import settings
from .exceptions import MaxTokensExceededError

class LLMClient:
    """統合LLMクライアント"""
    
    def __init__(self):
        """設定からAPIキーを取得し、各プロバイダーを初期化"""

        # Settings経由でAPIキーを取得（環境変数管理の統一）
        self.openai_api_key = settings.OPENAI_API_KEY

        # Gemini設定 (Google AI API Key + 東京リージョン)
        vertexai.init(
            api_key=settings.GOOGLE_AI_API_KEY,
            location="asia-northeast1",
        )
        self.gemini_api_key = bool(settings.GOOGLE_AI_API_KEY)

        # OpenAI設定
        if self.openai_api_key:
            self.openai_client = OpenAI(api_key=self.openai_api_key)
        else:
            self.openai_client = None
    
    def generate_with_images(
        self,
        prompt: str,
        image_data: Union[str, List[str]],
        model: str = "gemini-2.5-flash-lite",
        temperature: float = 0.0,
        max_tokens: int = 8192
    ) -> str:
        """
        画像データを使ってGemini Vision APIを呼び出し

        Args:
            prompt: プロンプト
            image_data: Base64エンコードされた画像データ（単一または複数）
            model: モデル名
            temperature: 温度パラメータ
            max_tokens: 最大トークン数

        Returns:
            生成されたテキスト
        """
        if not self.gemini_api_key:
            raise ValueError("Gemini API key is missing")

        try:
            model_obj = GenerativeModel(model)

            # 画像データをリスト化
            if isinstance(image_data, str):
                image_data_list = [image_data]
            else:
                image_data_list = image_data

            # コンテンツパーツを構築
            content_parts = [prompt]

            # 画像を追加
            for img_base64 in image_data_list:
                # Base64をバイトにデコード
                img_bytes = base64.b64decode(img_base64)

                # Geminiの画像形式に変換
                image_part = Part.from_data(
                    mime_type='image/png',
                    data=img_bytes
                )
                content_parts.append(image_part)

            # 安全フィルター設定
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            # APIを呼び出し
            response = model_obj.generate_content(
                content_parts,
                generation_config=GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature
                ),
                safety_settings=safety_settings
            )

            # レスポンスの検証
            if not response.candidates:
                raise ValueError("Gemini returned no candidates")

            candidate = response.candidates[0]

            # finish_reason をチェック
            if candidate.finish_reason != 1:  # 1 = STOP (正常終了)
                finish_reason_name = candidate.finish_reason.name if hasattr(candidate.finish_reason, 'name') else str(candidate.finish_reason)
                error_details = {
                    "finish_reason": candidate.finish_reason,
                    "finish_reason_name": finish_reason_name
                }
                if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                    error_details["safety_ratings"] = [
                        {
                            "category": rating.category.name if hasattr(rating.category, 'name') else str(rating.category),
                            "probability": rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                        }
                        for rating in candidate.safety_ratings
                    ]
                logger.error(f"Gemini Vision失敗: {error_details}")
                raise ValueError(f"Gemini finish_reason: {finish_reason_name} ({candidate.finish_reason})")

            # テキストを取得
            text_content = candidate.content.parts[0].text if candidate.content.parts else ""
            return text_content

        except Exception as e:
            logger.error(f"Gemini Vision API エラー: {e}")
            raise

    def call_model(
        self,
        tier: str,
        prompt: str,
        file_path: Optional[Path] = None,
        log_context: Optional[Dict] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        指定されたタスクに最適なモデルを呼び出し

        Args:
            tier: モデル階層("stagea_classification", "stageh_extraction", "ui_response")
            prompt: プロンプト
            file_path: ファイルパス (GeminiのStage A分類用)
            **kwargs: 追加パラメータ

        Returns:
            モデルレスポンス
        """
        config = get_model_config(tier)
        provider = config["provider"]
        # kwargsからmodel_nameが渡されていればそれを優先、なければconfigから取得
        model_name = kwargs.pop('model_name', None) or config["model"]

        # モデル名からプロバイダーを自動判定（明示的なmodel_name指定時）
        if model_name:
            if 'claude' in model_name.lower():
                provider = AIProvider.CLAUDE
            elif 'gemini' in model_name.lower():
                provider = AIProvider.GEMINI
            elif 'gpt' in model_name.lower() or 'text-embedding' in model_name.lower():
                provider = AIProvider.OPENAI

        if log_context:
            kwargs['log_context'] = log_context

        if provider == AIProvider.GEMINI:
            if not self.gemini_api_key:
                return {"success": False, "error": "Gemini API key is missing", "model": model_name}
            return self._call_gemini(model_name, prompt, file_path, config, **kwargs)

        elif provider == AIProvider.CLAUDE:
            return {"success": False, "error": "Anthropic is not supported in doc-search", "model": model_name}

        elif provider == AIProvider.OPENAI:
            if not self.openai_client:
                return {"success": False, "error": "OpenAI API key is missing", "model": model_name}
            return self._call_openai(model_name, prompt, config, **kwargs)

        else:
            raise ValueError(f"未対応のプロバイダー: {provider}")

    def _call_gemini(
        self,
        model_name: str,
        prompt: str,
        file_path: Optional[Path],
        config: Dict,
        **kwargs
    ) -> Dict[str, Any]:
        """Gemini API呼び出し（トップレベル関数のみ使用）"""
        uploaded_file = None
        try:
            model = GenerativeModel(model_name)

            content_parts = [prompt]

            if file_path and file_path.exists():
                # MIMEタイプを自動判定
                mime_type, _ = mimetypes.guess_type(str(file_path))
                if not mime_type:
                    mime_type = "application/pdf"  # デフォルト

                # ファイルを読み込み
                with open(str(file_path), "rb") as f:
                    file_data = f.read()
                uploaded_file = Part.from_data(data=file_data, mime_type=mime_type)
                content_parts.append(uploaded_file)

            # 安全フィルター設定（finish_reason: 2 対策）
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            # 生成設定（kwargs で max_tokens が渡された場合は優先）
            generation_config = GenerationConfig(
                max_output_tokens=kwargs.pop('max_tokens', config.get("max_tokens", 65536)),
                temperature=config.get("temperature", 0.1)
            )

            # response_format が kwargs に含まれている場合
            response_format = kwargs.get('response_format')
            if response_format in ["json", "json_object"]:
                generation_config = GenerationConfig(
                    max_output_tokens=kwargs.pop('max_tokens', config.get("max_tokens", 65536)),
                    temperature=config.get("temperature", 0.1),
                    response_mime_type="application/json"
                )

            response = model.generate_content(
                content_parts,
                generation_config=generation_config,
                safety_settings=safety_settings
            )

            # レスポンスの検証
            if not response.candidates:
                self._cleanup_uploaded_file(uploaded_file)
                return {"success": False, "error": "Gemini returned no candidates", "model": model_name, "provider": "gemini"}

            candidate = response.candidates[0]

            # finish_reason をチェック
            if candidate.finish_reason != 1:  # 1 = STOP (正常終了)
                self._cleanup_uploaded_file(uploaded_file)
                # 詳細なエラー情報を取得
                error_details = {
                    "finish_reason": candidate.finish_reason,
                    "finish_reason_name": candidate.finish_reason.name if hasattr(candidate.finish_reason, 'name') else str(candidate.finish_reason)
                }
                # safety_ratingsがあれば追加
                if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                    error_details["safety_ratings"] = [
                        {
                            "category": rating.category.name if hasattr(rating.category, 'name') else str(rating.category),
                            "probability": rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                        }
                        for rating in candidate.safety_ratings
                    ]
                logger.error(f"Gemini Vision失敗: {error_details}")
                return {
                    "success": False,
                    "error": f"Gemini finish_reason: {candidate.finish_reason}",
                    "error_details": error_details,
                    "model": model_name,
                    "provider": "gemini"
                }

            # テキストを取得
            text_content = candidate.content.parts[0].text if candidate.content.parts else ""

            # トークン使用量を取得
            usage = {}
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = {
                    "prompt_tokens":    getattr(response.usage_metadata, 'prompt_token_count', 0) or 0,
                    "completion_tokens": getattr(response.usage_metadata, 'candidates_token_count', 0) or 0,
                    "thinking_tokens":  getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0,
                    "total_tokens":     getattr(response.usage_metadata, 'total_token_count', 0) or 0,
                }
                logger.info(f"[Gemini] トークン使用量: prompt={usage['prompt_tokens']}, completion={usage['completion_tokens']}, thinking={usage['thinking_tokens']}, total={usage['total_tokens']}")

            # ログ記録
            log_context = kwargs.get('log_context')
            if log_context and usage:
                try:
                    from shared.common.ai_cost_logger import log_ai_usage
                    log_ai_usage(
                        app=log_context.get('app', 'unknown'),
                        stage=log_context.get('stage', 'unknown'),
                        model=model_name,
                        prompt_token_count=usage['prompt_tokens'],
                        candidates_token_count=usage['completion_tokens'],
                        thoughts_token_count=usage['thinking_tokens'],
                        total_token_count=usage['total_tokens'],
                        session_id=log_context.get('session_id'),
                        workspace_id=log_context.get('workspace_id'),
                    )
                except Exception as _log_err:
                    logger.warning(f"[Gemini] cost log failed: {_log_err}")

            # ファイルを削除
            self._cleanup_uploaded_file(uploaded_file)

            return {
                "success": True,
                "content": text_content,
                "model": model_name,
                "provider": "gemini",
                "usage": usage
            }

        except Exception as e:
            self._cleanup_uploaded_file(uploaded_file)
            return {"success": False, "error": str(e), "model": model_name, "provider": "gemini"}

    def _cleanup_uploaded_file(self, uploaded_file) -> None:
        """
        アップロードされたファイルを削除（トップレベル関数のみ使用）

        Args:
            uploaded_file: アップロードされたファイルオブジェクト
        """
        pass

    def _call_openai(
        self,
        model_name: str,
        prompt: str,
        config: Dict,
        **kwargs
    ) -> Dict[str, Any]:
        """OpenAI API呼び出し"""
        try:
            # ✅ GPT-5.1では max_completion_tokens を使用、旧モデルでは max_tokens（後方互換性）
            max_completion_tokens = config.get("max_completion_tokens")
            max_tokens = config.get("max_tokens", 16384)

            # パラメータを動的に構築
            api_params = {
                "model": model_name,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }

            # temperatureはGPT-5.1などの一部モデルでサポートされていないため、configに含まれている場合のみ設定
            if "temperature" in config:
                api_params["temperature"] = config["temperature"]

            # max_completion_tokens が設定されていればそれを使用、なければ max_tokens
            if max_completion_tokens:
                api_params["max_completion_tokens"] = max_completion_tokens
            else:
                api_params["max_tokens"] = max_tokens

            response = self.openai_client.chat.completions.create(**api_params)

            # トークン使用量を取得
            usage = {}
            if hasattr(response, 'usage') and response.usage:
                usage = {
                    "prompt_tokens":    getattr(response.usage, 'prompt_tokens', 0) or 0,
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0) or 0,
                    "thinking_tokens":  0,
                    "total_tokens":     getattr(response.usage, 'total_tokens', 0) or 0,
                }
                logger.info(f"[OpenAI] トークン使用量: prompt={usage['prompt_tokens']}, completion={usage['completion_tokens']}, total={usage['total_tokens']}")

            # ログ記録
            log_context = kwargs.get('log_context')
            if log_context and usage:
                try:
                    from shared.common.ai_cost_logger import log_ai_usage
                    log_ai_usage(
                        app=log_context.get('app', 'unknown'),
                        stage=log_context.get('stage', 'unknown'),
                        model=model_name,
                        prompt_token_count=usage['prompt_tokens'],
                        candidates_token_count=usage['completion_tokens'],
                        thoughts_token_count=0,
                        total_token_count=usage['total_tokens'],
                        session_id=log_context.get('session_id'),
                        workspace_id=log_context.get('workspace_id'),
                    )
                except Exception as _log_err:
                    logger.warning(f"[OpenAI] cost log failed: {_log_err}")

            return {
                "success": True,
                "content": response.choices[0].message.content,
                "model": model_name,
                "provider": "openai",
                "usage": usage
            }

        except Exception as e:
            return {"success": False, "error": str(e), "model": model_name, "provider": "openai"}

    def generate_embedding(self, text: str, log_context: Optional[Dict] = None) -> List[float]:
        """
        Embedding生成

        Args:
            text: Embeddingを生成するテキスト
            log_context: コスト記録コンテキスト（省略可）

        Returns:
            1536次元のembeddingベクトル
        """
        config = get_model_config("embeddings")

        if not self.openai_client:
            raise ConnectionError("OpenAI client not initialized for embedding generation.")

        # text-embedding-3-smallモデルで1536次元を明示的に指定
        response = self.openai_client.embeddings.create(
            model=config["model"],
            input=text,
            dimensions=config.get("dimensions", 1536)  # デフォルト1536次元
        )

        # ログ記録
        if log_context and hasattr(response, 'usage') and response.usage:
            try:
                from shared.common.ai_cost_logger import log_ai_usage
                prompt_tokens = getattr(response.usage, 'prompt_tokens', 0) or 0
                log_ai_usage(
                    app=log_context.get('app', 'unknown'),
                    stage=log_context.get('stage', 'embedding'),
                    model=config["model"],
                    prompt_token_count=prompt_tokens,
                    total_token_count=prompt_tokens,
                    session_id=log_context.get('session_id'),
                )
            except Exception as _log_err:
                logger.warning(f"[Embedding] cost log failed: {_log_err}")

        return response.data[0].embedding

    def generate_with_vision(
        self,
        prompt: str,
        image_path: str,
        model: str = "gemini-2.0-flash-exp",
        temperature: float = 0.0,
        max_tokens: int = 65536,
        response_format: Optional[str] = None,
        log_context: Optional[Dict] = None
    ) -> str:
        """
        画像ファイルを使ってGemini Vision APIを呼び出し

        Args:
            prompt: プロンプト
            image_path: 画像ファイルのパス（PNG, JPEG等）
            model: モデル名
            temperature: 温度パラメータ
            max_tokens: 最大トークン数
            response_format: レスポンスフォーマット（"json", "json_object" など）

        Returns:
            生成されたテキスト

        Raises:
            ValueError: APIキーがない、またはレスポンスが不正な場合
            Exception: その他のエラー
        """
        if not self.gemini_api_key:
            raise ValueError("Gemini API key is missing")

        try:
            model_obj = GenerativeModel(model)

            # ファイルをアップロード
            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type:
                mime_type = "image/jpeg"  # デフォルト

            with open(image_path, "rb") as f:
                file_data = f.read()
            uploaded_file = Part.from_data(data=file_data, mime_type=mime_type)

            # コンテンツパーツを構築
            content_parts = [prompt, uploaded_file]

            # 安全フィルター設定
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            # 生成設定【Ver 5.7】蛇口全開固定
            # 引数に依存せず、物理的に65536を強制
            HARDCODED_MAX_TOKENS = 65536
            
            # response_format が指定されている場合
            if response_format in ["json", "json_object"]:
                generation_config = GenerationConfig(
                    max_output_tokens=HARDCODED_MAX_TOKENS,
                    temperature=temperature,
                    response_mime_type="application/json"
                )
            else:
                generation_config = GenerationConfig(
                    max_output_tokens=HARDCODED_MAX_TOKENS,
                    temperature=temperature
                )
            logger.info(f"[Gemini Vision] max_output_tokens={HARDCODED_MAX_TOKENS} (ハードコード固定)")

            # APIを呼び出し（タイムアウト5分、1回リトライ）
            max_retries = 1
            last_error = None

            for attempt in range(max_retries + 1):  # 0回目（初回）+ 1回（リトライ）
                try:
                    logger.info(f"[Gemini Vision] API呼び出し試行 {attempt + 1}/{max_retries + 1}")
                    response = model_obj.generate_content(
                        content_parts,
                        generation_config=generation_config,
                        safety_settings=safety_settings,
                        request_options={"timeout": 300}  # 5分タイムアウト
                    )
                    break  # 成功したらループを抜ける
                except Exception as e:
                    last_error = e
                    error_str = str(e).lower()
                    # タイムアウトまたはネットワークエラーの場合
                    if any(keyword in error_str for keyword in ['timeout', 'deadline', 'network', 'connection']):
                        if attempt < max_retries:
                            logger.warning(f"[Gemini Vision] タイムアウト/ネットワークエラー。リトライします（試行 {attempt + 1}/{max_retries + 1}）: {e}")
                            continue
                        else:
                            logger.error(f"[Gemini Vision] タイムアウト/ネットワークエラー。リトライ上限に達しました: {e}")
                            raise
                    else:
                        # タイムアウト以外のエラーは即座に失敗
                        logger.error(f"[Gemini Vision] API エラー（リトライ不可）: {e}")
                        raise
            else:
                # ループが最後まで実行された（全てのリトライが失敗）
                if last_error:
                    raise last_error

            # アップロードファイルを削除
            pass

            # レスポンスの検証
            if not response.candidates:
                raise ValueError("Gemini returned no candidates")

            candidate = response.candidates[0]

            # finish_reason をチェック
            finish_reason_name = candidate.finish_reason.name if hasattr(candidate.finish_reason, 'name') else str(candidate.finish_reason)
            logger.info(f"[Gemini Vision] finish_reason: {finish_reason_name} ({candidate.finish_reason})")

            # テキストを取得（finish_reasonに関わらず取得）
            text_content = candidate.content.parts[0].text if candidate.content.parts else ""

            # finish_reason == 2 (MAX_TOKENS): トークン上限に達した場合
            # 注意: Gemini APIでは MAX_TOKENS = 2（3ではない）
            if candidate.finish_reason == 2:
                error_msg = f"MAX_TOKENS上限に達しました。出力が途中で切れています。({len(text_content)}文字)"
                logger.error(f"[Gemini Vision] {error_msg}")
                logger.error(f"[Gemini Vision] 途中で切れた出力（最後の500文字）: {text_content[-500:]}")
                raise MaxTokensExceededError(
                    message=error_msg,
                    partial_output=text_content,
                    finish_reason_name=finish_reason_name
                )

            # finish_reason != 1 (STOP以外のその他のエラー)
            if candidate.finish_reason != 1:  # 1 = STOP (正常終了)
                error_details = {
                    "finish_reason": candidate.finish_reason,
                    "finish_reason_name": finish_reason_name
                }
                if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                    error_details["safety_ratings"] = [
                        {
                            "category": rating.category.name if hasattr(rating.category, 'name') else str(rating.category),
                            "probability": rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                        }
                        for rating in candidate.safety_ratings
                    ]
                logger.error(f"Gemini Vision失敗: {error_details}")
                raise ValueError(f"Gemini finish_reason: {finish_reason_name} ({candidate.finish_reason})")

            # テキスト長とトークン使用量をログ出力
            logger.info(f"[Gemini Vision] 応答テキスト長: {len(text_content)}文字")

            # トークン使用量を取得・保存
            self.last_usage = {}
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                self.last_usage = {
                    "prompt_tokens":    getattr(response.usage_metadata, 'prompt_token_count', 0) or 0,
                    "completion_tokens": getattr(response.usage_metadata, 'candidates_token_count', 0) or 0,
                    "thinking_tokens":  getattr(response.usage_metadata, 'thoughts_token_count', 0) or 0,
                    "total_tokens":     getattr(response.usage_metadata, 'total_token_count', 0) or 0,
                    "model": model
                }
                logger.info(f"[Gemini Vision] トークン使用量: prompt={self.last_usage['prompt_tokens']}, completion={self.last_usage['completion_tokens']}, thinking={self.last_usage['thinking_tokens']}, total={self.last_usage['total_tokens']}")

            # コスト記録
            if log_context and self.last_usage:
                try:
                    from shared.common.ai_cost_logger import log_ai_usage
                    log_ai_usage(
                        app=log_context.get('app', 'unknown'),
                        stage=log_context.get('stage', 'vision'),
                        model=model,
                        prompt_token_count=self.last_usage['prompt_tokens'],
                        candidates_token_count=self.last_usage['completion_tokens'],
                        thoughts_token_count=self.last_usage['thinking_tokens'],
                        total_token_count=self.last_usage['total_tokens'],
                        session_id=log_context.get('session_id'),
                        workspace_id=log_context.get('workspace_id'),
                    )
                except Exception as _log_err:
                    logger.warning(f"[Gemini Vision] cost log failed: {_log_err}")

            return text_content

        except Exception as e:
            logger.error(f"Gemini Vision API エラー: {e}")
            raise

    def transcribe_image(
        self,
        image_path: Path,
        prompt: str = "この画像内の表組みやリストを、Markdown形式で正確に書き起こしてください。",
        model: str = "gemini-2.5-flash-lite"
    ) -> Dict[str, Any]:
        """
        画像ファイルをGemini Visionで文字起こし

        Args:
            image_path: 画像ファイルのパス（PNG, JPEG等）
            prompt: Geminiに送るプロンプト
            model: 使用するGeminiモデル（デフォルト: gemini-2.5-flash-lite）

        Returns:
            {"success": bool, "content": str, "model": str, "provider": str}
        """
        if not self.gemini_api_key:
            return {"success": False, "error": "Gemini API key is missing", "model": "gemini-2.5-flash-lite"}

        # 指定されたGeminiモデルを使用
        return self._call_gemini(
            model_name=model,
            prompt=prompt,
            file_path=image_path,
            config={
                "max_tokens": 65536,  # Gemini 2.5の最大出力トークン数（65,536）
                "temperature": 0.0
            }
        )