"""
G-22: Text AI Processor（地の文のAI処理）

G-21で生成された最高レベルのテキスト（articles）から、
Gemini 2.5 Flash-lite でイベント、タスク、注意事項を抽出する。

目的:
- G-21のテキストからAI解析
- イベント、タスク、注意事項の抽出
- 全文ログ出力（AI品質評価用）
"""

from typing import Dict, Any, List, Optional
from loguru import logger
import json

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[G-22] google-generativeai がインストールされていません")


class G22TextAIProcessor:
    """G-22: Text AI Processor（地の文のAI処理）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite"
    ):
        """
        Text AI Processor 初期化

        Args:
            api_key: Google AI API Key
            model_name: 使用するモデル名（gemini-2.5-flash-lite）
        """
        self.model_name = model_name
        self.api_key = api_key

        if not GENAI_AVAILABLE:
            logger.error("[G-22] google-generativeai が必要です")
            self.model = None
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
            logger.info(f"[G-22] モデル初期化: {model_name}")
        else:
            logger.warning("[G-22] API key が設定されていません")
            self.model = None

    def process(
        self,
        articles: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        articles からイベント、タスク、注意事項を抽出

        Args:
            articles: G-21 で生成された articles
                [{'title': str, 'body': str}, ...]

        Returns:
            {
                'success': bool,
                'calendar_events': list,
                'tasks': list,
                'notices': list,
                'tokens_used': int
            }
        """
        logger.info("[G-22] AI処理開始")

        if not GENAI_AVAILABLE or not self.model:
            logger.error("[G-22] Gemini API が利用できません")
            return self._error_result("Gemini API not available")

        if not articles:
            logger.info("[G-22] 処理する articles がありません")
            return {
                'success': True,
                'calendar_events': [],
                'tasks': [],
                'notices': [],
                'tokens_used': 0
            }

        try:
            # articles を1つのテキストに統合
            full_text = self._combine_articles(articles)

            # プロンプトを構築
            prompt = self._build_prompt(full_text)

            logger.info(f"[G-22] モデル: {self.model_name}")
            logger.info(f"[G-22] 入力テキスト長: {len(full_text)}文字")
            logger.info("")
            logger.info("[G-22] ========== 入力テキスト（G-21の出力） ==========")
            logger.info(full_text)
            logger.info("=" * 60)

            # Gemini に送信
            response = self.model.generate_content(prompt)
            raw_text = response.text

            logger.info("")
            logger.info("[G-22] ========== AI応答（生） ==========")
            logger.info(raw_text)
            logger.info("=" * 60)

            # JSONをパース
            result = self._parse_response(raw_text)

            # トークン数を概算
            tokens_used = (len(prompt) + len(raw_text)) // 4

            logger.info("")
            logger.info("[G-22] ========== 抽出結果 ==========")
            logger.info(f"calendar_events: {len(result.get('calendar_events', []))}件")
            for i, event in enumerate(result.get('calendar_events', []), 1):
                logger.info(f"  Event {i}: {event}")
            logger.info(f"tasks: {len(result.get('tasks', []))}件")
            for i, task in enumerate(result.get('tasks', []), 1):
                logger.info(f"  Task {i}: {task}")
            logger.info(f"notices: {len(result.get('notices', []))}件")
            for i, notice in enumerate(result.get('notices', []), 1):
                logger.info(f"  Notice {i}: {notice}")
            logger.info("=" * 60)

            logger.info(f"[G-22] AI処理完了: トークン約{tokens_used}")

            return {
                'success': True,
                'calendar_events': result.get('calendar_events', []),
                'tasks': result.get('tasks', []),
                'notices': result.get('notices', []),
                'tokens_used': tokens_used
            }

        except Exception as e:
            logger.error(f"[G-22] AI処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _combine_articles(self, articles: List[Dict[str, Any]]) -> str:
        """
        articles を1つのテキストに統合

        Args:
            articles: [{'title': str, 'body': str}, ...]

        Returns:
            統合されたテキスト
        """
        parts = []
        for article in articles:
            title = article.get('title', '')
            body = article.get('body', '')
            if title:
                parts.append(f"# {title}")
            if body:
                parts.append(body)
            parts.append("")  # 空行で区切り

        return "\n".join(parts)

    def _build_prompt(self, text: str) -> str:
        """
        プロンプトを構築

        Args:
            text: 入力テキスト

        Returns:
            プロンプト文字列
        """
        return f"""
あなたは学校通信から重要な情報を抽出する専門家です。

以下のテキストから、以下の情報を抽出し、JSON形式で返してください：

1. **予定・スケジュール**: 日付、時間、イベント名、場所
2. **タスク**: 提出物、準備物、持ち物
3. **注意事項**: 重要な連絡、変更点

[テキスト]
{text}

出力形式:
```json
{{
  "calendar_events": [
    {{
      "date": "2024-01-15",
      "time": "9:00-12:00",
      "event": "授業参観",
      "location": "各教室"
    }}
  ],
  "tasks": [
    {{
      "deadline": "2024-01-10",
      "item": "健康調査票",
      "description": "保護者記入欄を記入して提出"
    }}
  ],
  "notices": [
    {{
      "category": "持ち物",
      "content": "上履き、体操着を忘れずに"
    }}
  ]
}}
```

**重要な指示:**
- テキストに記載されていない情報は絶対に作らないこと
- 抽出できない場合は空の配列を返すこと
- 日付は可能な限り YYYY-MM-DD 形式に変換すること
"""

    def _parse_response(self, raw_text: str) -> Dict[str, Any]:
        """
        レスポンスをパースしてJSONを抽出

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
            logger.warning(f"[G-22] JSONパースエラー: {e}")
            # パースできない場合は空の結果を返す
            return {
                'calendar_events': [],
                'tasks': [],
                'notices': []
            }

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'calendar_events': [],
            'tasks': [],
            'notices': [],
            'tokens_used': 0
        }
