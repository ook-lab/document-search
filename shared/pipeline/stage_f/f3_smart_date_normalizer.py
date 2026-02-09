"""
F-3: Smart Date/Time Normalizer（日付・時刻の正規化）

Gemini 2.5 Flash-lite を使用して、曖昧な日付表現を
ISO 8601 形式に変換する。

目的:
1. 「1/12」「来週月曜」「15:00〜」等の正規化
2. 年度補完（Stage A のコンテキスト情報を活用）
3. マイクロAIタスクとしての実装
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import json
from datetime import datetime

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[F-3] google-generativeai がインストールされていません")


class F3SmartDateNormalizer:
    """F-3: Smart Date/Time Normalizer（日付正規化）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite"
    ):
        """
        Smart Date Normalizer 初期化

        Args:
            api_key: Google AI API Key
            model_name: 使用するモデル名
        """
        self.model_name = model_name
        self.api_key = api_key

        if not GENAI_AVAILABLE:
            logger.error("[F-3] google-generativeai が必要です")
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
        else:
            logger.warning("[F-3] API key が設定されていません")
            self.model = None

    def normalize_dates(
        self,
        events: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        reference_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        イベントリストの日付を正規化

        Args:
            events: イベントリスト
            year_context: 年度コンテキスト（例: 2025）
            reference_date: 基準日（デフォルトは今日）

        Returns:
            {
                'success': bool,
                'normalized_events': list,  # 正規化済みイベント
                'tokens_used': int,
                'normalization_count': int
            }
        """
        if not GENAI_AVAILABLE or not self.model:
            logger.error("[F-3] Gemini API が利用できません")
            return self._error_result("Gemini API not available")

        if not events:
            logger.info("[F-3] 正規化するイベントがありません")
            return {
                'success': True,
                'normalized_events': [],
                'tokens_used': 0,
                'normalization_count': 0
            }

        logger.info(f"[F-3] 日付正規化開始: {len(events)}件")

        try:
            # 年度コンテキストを決定
            if year_context is None:
                year_context = datetime.now().year

            # 基準日を決定
            if reference_date is None:
                reference_date = datetime.now().strftime('%Y-%m-%d')

            # プロンプトを構築
            prompt = self._build_prompt(events, year_context, reference_date)

            logger.info(f"[F-3] モデル: {self.model_name}")
            logger.info(f"[F-3] 年度: {year_context}")
            logger.info(f"[F-3] 基準日: {reference_date}")

            # Gemini に送信
            response = self.model.generate_content(prompt)
            raw_text = response.text

            # レスポンスをパース
            normalized_events = self._parse_response(raw_text, events)

            # トークン数を概算
            tokens_used = (len(prompt) + len(raw_text)) // 4

            normalization_count = sum(
                1 for e in normalized_events if e.get('normalized_date')
            )

            logger.info(f"[F-3] 正規化完了:")
            logger.info(f"  ├─ 正規化成功: {normalization_count}件")
            logger.info(f"  └─ トークン: 約{tokens_used}")

            return {
                'success': True,
                'normalized_events': normalized_events,
                'tokens_used': tokens_used,
                'normalization_count': normalization_count
            }

        except Exception as e:
            logger.error(f"[F-3] 正規化エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _build_prompt(
        self,
        events: List[Dict[str, Any]],
        year_context: int,
        reference_date: str
    ) -> str:
        """
        プロンプトを構築

        Args:
            events: イベントリスト
            year_context: 年度
            reference_date: 基準日

        Returns:
            プロンプト文字列
        """
        prompt_parts = []

        # ベースプロンプト
        prompt_parts.append(f"""
あなたは曖昧な日付表現を ISO 8601 形式に変換する専門家です。

**コンテキスト:**
- 年度: {year_context}年
- 基準日: {reference_date}

**タスク:**
以下のイベントリストに含まれる日付表現を、ISO 8601 形式（YYYY-MM-DD）に変換してください。

**重要な変換ルール:**
1. 「1/12」 → 「{year_context}-01-12」（年度を補完）
2. 「来週月曜」 → 基準日から計算した具体的な日付
3. 「11月9日」 → 「{year_context}-11-09」
4. 時刻は「HH:MM」形式（24時間表記）

**入力イベント:**
```json
{json.dumps(events, ensure_ascii=False, indent=2)}
```

**出力形式:**
各イベントに `normalized_date` と `normalized_time` フィールドを追加してください。
日付が不明な場合は `normalized_date: null` としてください。

```json
[
  {{
    "original_text": "...",
    "normalized_date": "YYYY-MM-DD",
    "normalized_time": "HH:MM",
    ...
  }}
]
```
""")

        return "".join(prompt_parts)

    def _parse_response(
        self,
        raw_text: str,
        original_events: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        レスポンスをパースして正規化イベントを抽出

        Args:
            raw_text: Geminiの生レスポンス
            original_events: 元のイベントリスト

        Returns:
            正規化されたイベントリスト
        """
        try:
            # ```json ... ``` で囲まれている場合
            if '```json' in raw_text:
                start = raw_text.find('```json') + 7
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
                normalized_events = json.loads(json_str)
            # ``` ... ``` で囲まれている場合
            elif '```' in raw_text:
                start = raw_text.find('```') + 3
                end = raw_text.find('```', start)
                json_str = raw_text[start:end].strip()
                normalized_events = json.loads(json_str)
            # JSON部分のみの場合
            else:
                normalized_events = json.loads(raw_text)

            # 元のイベントとマージ
            for i, event in enumerate(normalized_events):
                if i < len(original_events):
                    # 元のイベントのフィールドを保持
                    for key, value in original_events[i].items():
                        if key not in event:
                            event[key] = value

            return normalized_events

        except Exception as e:
            logger.warning(f"[F-3] JSONパースエラー: {e}")
            # パース失敗時は元のイベントを返す
            return original_events

    def normalize_single_date(
        self,
        date_text: str,
        year_context: Optional[int] = None
    ) -> Optional[str]:
        """
        単一の日付テキストを正規化（簡易版）

        Args:
            date_text: 日付テキスト
            year_context: 年度

        Returns:
            ISO 8601 形式の日付 or None
        """
        if year_context is None:
            year_context = datetime.now().year

        # 簡易的なパターンマッチング
        import re

        # パターン1: MM/DD
        pattern1 = r'(\d{1,2})/(\d{1,2})'
        match = re.search(pattern1, date_text)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            return f"{year_context}-{month:02d}-{day:02d}"

        # パターン2: MM月DD日
        pattern2 = r'(\d{1,2})月(\d{1,2})日'
        match = re.search(pattern2, date_text)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            return f"{year_context}-{month:02d}-{day:02d}"

        return None

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'normalized_events': [],
            'tokens_used': 0,
            'normalization_count': 0
        }
