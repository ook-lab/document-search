"""
E-20: Context Extractor（地の文用 - Gemini 2.5 Flash-lite）

地の文画像（非表領域）から、予定・タスク・持ち物などの
文脈情報を抽出する。

目的:
1. 学年通信やお知らせからスケジュールを抽出
2. タスク・持ち物・注意事項の構造化
3. 高速・低コストでの処理（Flash-lite使用）
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import base64
import json

from .coordinate_matcher import CoordinateMatcher

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[E-20] google-generativeai がインストールされていません")


class E20ContextExtractor:
    """E-20: Context Extractor（地の文用）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite"  # Gemini 2.5 Flash-lite
    ):
        """
        Context Extractor 初期化

        Args:
            api_key: Google AI API Key
            model_name: 使用するモデル名（必ず gemini-2.5-flash-lite を使用）
        """
        self.model_name = model_name
        self.api_key = api_key
        self.matcher = CoordinateMatcher()

        if not GENAI_AVAILABLE:
            logger.error("[E-20] google-generativeai が必要です")
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
            logger.info(f"[E-20] モデル初期化: {model_name}")
        else:
            logger.warning("[E-20] API key が設定されていません")
            self.model = None

    def extract(
        self,
        image_path: Path,
        page: int = 0,
        words: Optional[List[Dict[str, Any]]] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        block_hint: Optional[str] = None,
        custom_prompt: Optional[str] = None,
        vision_text: Optional[str] = None,
        anchor_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        画像から文脈情報を抽出（座標付き）

        Args:
            image_path: 画像ファイルパス
            page: ページ番号（0-indexed）
            words: E1 の単語リスト（座標マッチング用）
            blocks: E5 のブロックリスト（座標マッチング用）
            block_hint: E-5のブロックヒント（オプション）
            custom_prompt: カスタムプロンプト（オプション）
            vision_text: E-21 の Vision OCR テキスト（あれば注入、なければ画像のみ）
            anchor_text: PDF由来テキスト（将来の拡張口、現在は null でOK）

        Returns:
            {
                'success': bool,
                'extracted_content': dict,  # 抽出された情報（page + bbox 付き）
                'raw_response': str,        # Geminiの生レスポンス
                'model_used': str,          # 使用したモデル名（gemini-2.5-flash-lite）
                'tokens_used': int,         # 使用トークン数（概算）
                'route': str,               # "E22_IMAGE_ONLY" | "E21_VISION+E22"
                'vision_text_used': bool    # Vision OCR テキストをプロンプトに注入したか
            }
        """
        if not GENAI_AVAILABLE or not self.model:
            logger.error("[E-20] Gemini API が利用できません")
            return self._error_result("Gemini API not available")

        vision_text_used = bool(vision_text and vision_text.strip())
        route = "E21_VISION+E22" if vision_text_used else "E22_IMAGE_ONLY"

        logger.info(f"[E-20] 文脈抽出開始: {image_path.name} route={route}")

        try:
            # 画像を読み込み
            with open(image_path, 'rb') as f:
                image_data = f.read()

            # プロンプトを構築
            prompt = self._build_prompt(block_hint, custom_prompt, vision_text, anchor_text)

            logger.info(f"[E-20] モデル: {self.model_name}")
            logger.info(f"[E-20] プロンプト長: {len(prompt)}文字")

            # Gemini に送信
            response = self.model.generate_content([
                prompt,
                {
                    'mime_type': 'image/png',
                    'data': image_data
                }
            ])

            # レスポンスをパース
            raw_text = response.text
            logger.info(f"[E-20] レスポンス長: {len(raw_text)}文字")

            # JSON部分を抽出（```json ... ``` で囲まれている場合）
            extracted_content = self._parse_response(raw_text)

            # 座標を付与（words と blocks が提供されている場合）
            if words:
                logger.info(f"[E-20] 座標マッチング開始（words: {len(words)}個）")
                extracted_content = self._enrich_with_coordinates(
                    extracted_content, words, blocks, page
                )

            # トークン数を概算（文字数 / 4）
            tokens_used = (len(prompt) + len(raw_text)) // 4

            logger.info(f"[E-20] 抽出完了")
            logger.info(f"  ├─ モデル: {self.model_name}")
            logger.info(f"  ├─ route: {route}")
            logger.info(f"  └─ トークン: 約{tokens_used}")

            return {
                'success': True,
                'extracted_content': extracted_content,
                'raw_response': raw_text,
                'model_used': self.model_name,
                'tokens_used': tokens_used,
                'route': route,
                'vision_text_used': vision_text_used
            }

        except Exception as e:
            logger.error(f"[E-20] 抽出エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _build_prompt(
        self,
        block_hint: Optional[str],
        custom_prompt: Optional[str],
        vision_text: Optional[str] = None,
        anchor_text: Optional[str] = None
    ) -> str:
        """
        プロンプトを構築

        Args:
            block_hint: ブロックヒント
            custom_prompt: カスタムプロンプト
            vision_text: E-21 の Vision OCR テキスト（あれば注入）
            anchor_text: PDF由来テキスト（将来の拡張口）

        Returns:
            プロンプト文字列
        """
        if custom_prompt:
            return custom_prompt

        prompt_parts = []

        # ベースプロンプト
        prompt_parts.append("""
あなたは保護者向けの学校通信から重要な情報を抽出する専門家です。

添付された画像から以下の情報を抽出し、JSON形式で返してください：

1. **予定・スケジュール**: 日付、時間、イベント名、場所
2. **タスク**: 提出物、準備物、持ち物
3. **注意事項**: 重要な連絡、変更点
4. **その他**: 上記に分類できない重要情報

出力形式:
```json
{
  "schedule": [
    {
      "date": "2024-01-15",
      "time": "9:00-12:00",
      "event": "授業参観",
      "location": "各教室"
    }
  ],
  "tasks": [
    {
      "deadline": "2024-01-10",
      "item": "健康調査票",
      "description": "保護者記入欄を記入して提出"
    }
  ],
  "notices": [
    {
      "category": "持ち物",
      "content": "上履き、体操着を忘れずに"
    }
  ],
  "other": []
}
```

**重要な指示:**
- 画像に記載されていない情報は絶対に作らないこと（捏造禁止）
- 読めない箇所は `[[判読不能]]` と明記すること
- 参考OCRは文字の補助として使い、画像の内容・順序・見出し階層を優先すること
""")

        # Vision OCR テキストを注入（あれば）
        if vision_text and vision_text.strip():
            prompt_parts.append("\n---\n")
            prompt_parts.append("**参考OCR（Vision）:**\n")
            prompt_parts.append("（以下は Vision API によるOCR結果です。誤認識を含む場合があります。画像が正本です。）\n")
            prompt_parts.append(vision_text.strip()[:3000])  # 過大なテキストを制限
            prompt_parts.append("\n")

        # PDF由来テキストを注入（あれば）
        if anchor_text and anchor_text.strip():
            prompt_parts.append("\n---\n")
            prompt_parts.append("**参考テキスト（PDF抽出）:**\n")
            prompt_parts.append(anchor_text.strip()[:2000])
            prompt_parts.append("\n")

        # ブロックヒントを追加
        if block_hint:
            prompt_parts.append("\n---\n")
            prompt_parts.append(block_hint)

        return "".join(prompt_parts)

    def _parse_response(self, raw_text: str) -> Dict[str, Any]:
        """
        レスポンスをパースしてJSON部分を抽出

        Args:
            raw_text: Geminiの生レスポンス

        Returns:
            抽出されたJSON
        """
        try:
            # ```json ... ``` で囲まれている場合
            if '```json' in raw_text:
                start = raw_text.find('```json') + 7
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
                return json.loads(json_str)
            # ``` ... ``` で囲まれている場合
            elif '```' in raw_text:
                start = raw_text.find('```') + 3
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
                return json.loads(json_str)
            # JSON部分のみの場合
            else:
                return json.loads(raw_text)
        except Exception as e:
            logger.warning(f"[E-20] JSONパースエラー: {e}")
            # パースできない場合は生テキストを返す
            return {
                'raw_text': raw_text,
                'parse_error': str(e)
            }

    def _enrich_with_coordinates(
        self,
        extracted_content: Dict[str, Any],
        words: List[Dict[str, Any]],
        blocks: Optional[List[Dict[str, Any]]],
        page: int
    ) -> Dict[str, Any]:
        """
        抽出された要素に座標を付与

        Args:
            extracted_content: Gemini が返した抽出結果
            words: E1 の単語リスト
            blocks: E5 のブロックリスト
            page: ページ番号

        Returns:
            座標が付与された extracted_content
        """
        enriched = {}

        # schedule, tasks, notices, other の各カテゴリを処理
        for key in ['schedule', 'tasks', 'notices', 'other']:
            elements = extracted_content.get(key, [])
            if not elements:
                enriched[key] = []
                continue

            # 各カテゴリに応じたテキストキーを決定
            if key == 'schedule':
                text_key = 'event'
            elif key == 'tasks':
                text_key = 'item'
            else:
                text_key = 'content'

            # 座標を付与
            enriched_elements = self.matcher.enrich_elements(
                elements, words, blocks, page, text_key
            )
            enriched[key] = enriched_elements

        # その他のキーをコピー
        for key, value in extracted_content.items():
            if key not in enriched:
                enriched[key] = value

        return enriched

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'extracted_content': {},
            'raw_response': '',
            'model_used': self.model_name,
            'tokens_used': 0,
            'route': 'E22_IMAGE_ONLY',
            'vision_text_used': False
        }
