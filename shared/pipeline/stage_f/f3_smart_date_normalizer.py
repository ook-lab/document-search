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
        model_name: str = "gemini-2.5-flash-lite",
        next_stage=None
    ):
        """
        Smart Date Normalizer 初期化

        Args:
            api_key: Google AI API Key
            model_name: 使用するモデル名
            next_stage: 次のステージ（F-5）のインスタンス
        """
        self.model_name = model_name
        self.api_key = api_key
        self.next_stage = next_stage

        if not GENAI_AVAILABLE:
            logger.error("[F-3] google-generativeai が必要です")
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
        else:
            logger.warning("[F-3] API key が設定されていません")
            self.model = None

    def normalize(
        self,
        events: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        merge_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        チェーンパターン用: イベントを正規化して次のステージへ

        Args:
            events: イベントリスト
            year_context: 年度コンテキスト
            merge_result: F-1の統合結果

        Returns:
            F-5の結果（チェーン経由）またはF-3の結果
        """
        # イベントの正規化（表データとテキストも渡す）
        display_sent_at = merge_result.get('display_sent_at') if merge_result else None
        norm_result = self.normalize_dates(
            events=events,
            year_context=year_context,
            merge_result=merge_result,
            display_sent_at=display_sent_at,
        )

        if not norm_result.get('success'):
            return norm_result

        # merge_resultを更新
        if merge_result:
            merge_result['events'] = norm_result['normalized_events']

        # ★チェーン: 次のステージ（F-5）を呼び出す
        if self.next_stage and merge_result:
            logger.info("[F-3] → 次のステージ（F-5）を呼び出します")
            return self.next_stage.join(merge_result=merge_result)

        # チェーンがない場合は正規化結果を返す
        if merge_result:
            merge_result['normalized_events'] = norm_result['normalized_events']
            return merge_result

        return norm_result

    def normalize_dates(
        self,
        events: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        reference_date: Optional[str] = None,
        merge_result: Optional[Dict[str, Any]] = None,
        display_sent_at=None,
    ) -> Dict[str, Any]:
        """
        イベントリストの日付を正規化

        Args:
            events: イベントリスト
            year_context: 年度コンテキスト（例: 2025）
            reference_date: 基準日（デフォルトは今日）
            merge_result: F-1の統合結果（表データとテキストを含む）

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
            # 基準日を決定（display_sent_at があればそちらを優先）
            if reference_date is None:
                if display_sent_at:
                    # タイムゾーン付き文字列の場合は日付部分だけ取り出す
                    reference_date = str(display_sent_at)[:10]
                else:
                    reference_date = datetime.now().strftime('%Y-%m-%d')

            # ★表データとテキストを取得（年度推定に使用）
            tables = merge_result.get('tables', []) if merge_result else []
            raw_text = merge_result.get('raw_integrated_text', '') if merge_result else ''

            # プロンプトを構築
            prompt = self._build_prompt(events, year_context, reference_date, tables, raw_text, display_sent_at)

            logger.info(f"[F-3] モデル: {self.model_name}")
            logger.info(f"[F-3] 年度ヒント: {year_context if year_context else 'なし（AIが推定）'}")
            logger.info(f"[F-3] 基準日（送信日ベース）: {reference_date}")
            logger.info(f"[F-3] display_sent_at: {display_sent_at or '未設定'}")
            logger.info(f"[F-3] 年度推定用データ: 表{len(tables)}個, テキスト{len(raw_text)}文字")

            # プロンプト全文をログ出力
            logger.info("=" * 80)
            logger.info("[F-3] AI プロンプト全文:")
            logger.info("=" * 80)
            logger.info(prompt)
            logger.info("=" * 80)

            # Gemini に送信
            response = self.model.generate_content(prompt)
            raw_text = response.text

            # レスポンス全文をログ出力
            logger.info("=" * 80)
            logger.info("[F-3] AI レスポンス全文:")
            logger.info("=" * 80)
            logger.info(raw_text)
            logger.info("=" * 80)

            # レスポンスをパース
            normalized_events = self._parse_response(raw_text, events)

            # トークン数を概算
            tokens_used = (len(prompt) + len(raw_text)) // 4

            normalization_count = sum(
                1 for e in normalized_events if e.get('normalized_date')
            )

            # トークン使用量の詳細をログ出力
            prompt_tokens = len(prompt) // 4
            response_tokens = len(raw_text) // 4
            logger.info(f"[F-3] トークン使用量詳細:")
            logger.info(f"  ├─ プロンプト: 約{prompt_tokens} tokens")
            logger.info(f"  ├─ レスポンス: 約{response_tokens} tokens")
            logger.info(f"  └─ 合計: 約{tokens_used} tokens")

            # 正規化されたイベントの詳細（変換前→変換後）をログ出力
            logger.info("=" * 80)
            logger.info("[F-3] 正規化イベント詳細（変換前→変換後）:")
            logger.info("=" * 80)
            for idx, event in enumerate(normalized_events, 1):
                original = event.get('original_text', event.get('date_text', ''))
                normalized_date = event.get('normalized_date')
                normalized_time = event.get('normalized_time')

                if normalized_date or normalized_time:
                    logger.info(f"[F-3] Event #{idx}:")
                    logger.info(f"  ├─ 元の表現: 「{original}」")
                    if normalized_date:
                        logger.info(f"  ├─ 正規化日付: {normalized_date}")
                    if normalized_time:
                        logger.info(f"  ├─ 正規化時刻: {normalized_time}")
                    if year_context:
                        logger.info(f"  └─ 年度ヒント: {year_context}年を使用")
                    else:
                        logger.info(f"  └─ 年度: AIがテキストから推定")
            logger.info("=" * 80)

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
        year_context: Optional[int],
        reference_date: str,
        tables: Optional[List[Dict[str, Any]]] = None,
        raw_text: Optional[str] = None,
        display_sent_at=None,
    ) -> str:
        """
        プロンプトを構築

        Args:
            events: イベントリスト
            year_context: 年度（Noneの場合はAIが推定）
            reference_date: 基準日
            tables: 表データ（年度推定に使用）
            raw_text: 統合テキスト（年度推定に使用）

        Returns:
            プロンプト文字列
        """
        prompt_parts = []

        # year_contextの有無でプロンプトを変更
        sent_at_line = f"- 送信日時（Supabase）: {display_sent_at}" if display_sent_at else ""
        if year_context:
            # 年度ヒントがある場合
            context_info = f"""
**コンテキスト:**
- 年度ヒント: {year_context}年
- 基準日: {reference_date}
{sent_at_line}
"""
        else:
            # 年度ヒントがない場合
            context_info = f"""
**コンテキスト:**
- 基準日: {reference_date}
{sent_at_line}
- 年度情報: 不明（以下のルールと情報から推定してください）
"""

        # ★年度推定のための追加情報
        year_hints_section = ""
        if not year_context:
            year_hints = []

            # テキストから年度ヒント抽出（先頭500文字）
            if raw_text:
                text_sample = raw_text[:500] if len(raw_text) > 500 else raw_text
                year_hints.append(f"**文書テキスト（抜粋）:**\n```\n{text_sample}\n```")

            # 表データから年度ヒント抽出（最初の表のみ）
            if tables and len(tables) > 0:
                table = tables[0]
                headers = table.get('headers', [])
                rows = table.get('rows', [])[:5]  # 最初の5行のみ
                table_sample = f"ヘッダー: {headers}\n"
                for i, row in enumerate(rows, 1):
                    table_sample += f"行{i}: {row}\n"
                year_hints.append(f"**表データ（抜粋）:**\n```\n{table_sample}```")

            if year_hints:
                year_hints_section = "\n\n**年度推定のための参考情報:**\n" + "\n\n".join(year_hints)

        # 最近傍年ルール（display_sent_at がある場合に追加）
        closest_year_rule = ""
        if display_sent_at and not year_context:
            sent_date_str = str(display_sent_at)[:10]
            closest_year_rule = f"""
**★最重要：年が不明な場合の年度決定ルール（送信日最近傍）**
- 年が書かれていない日付（例: "1/4"、"3月5日"）は、送信日 {sent_date_str} に最も近い日付になる年を選んでください
- 送信日の前後それぞれの年（送信年-1、送信年、送信年+1）で候補日を作り、送信日との日数差が最小になる年を採用します
- 例: 送信日=2025-12-25、日付="1/4"
    → 2025-01-04: |2025-12-25 - 2025-01-04| = 355日
    → 2026-01-04: |2025-12-25 - 2026-01-04| = 10日  ← 最も近い → 2026年を採用
    → 2024-01-04: |2025-12-25 - 2024-01-04| = 721日
- 曜日の整合性も確認し、合わない場合はさらに近い年を探してください
"""

        # ベースプロンプト
        prompt_parts.append(f"""
あなたは曖昧な日付表現を ISO 8601 形式に変換する専門家です。

{context_info}{year_hints_section}
{closest_year_rule}
**タスク:**
1. **年度の推定**: テキストや表から年度情報を推定してください
   - 例: "2025年度"、"令和7年"、"2025/1/16 発行"、表中の日付パターン
   - ★表データとテキストの両方を確認し、年情報を見つけてください
   - ★年が書かれていない場合は上記「送信日最近傍ルール」を使ってください
2. **曜日の検証**: 日付に曜日が付いている場合（例: "1/15（月）"）、変換後の日付が実際にその曜日か確認してください
   - 曜日が合わない場合は、正しい年を再推定してください
   - 例: "1/15（月）" → 2025-01-15が月曜日でない場合、2024-01-15や2026-01-15など、月曜日になる年を探してください
3. **日付の変換**: 以下のイベントリストに含まれる日付表現を、ISO 8601 形式（YYYY-MM-DD）に変換してください

**重要な変換ルール:**
1. 「1/12」 → 推定した年度を使って「YYYY-01-12」
2. 「1/15（月）」 → 月曜日になる年を探して「YYYY-01-15」（曜日の整合性を優先）
3. 「来週月曜」 → 基準日から計算した具体的な日付
4. 「11月9日」 → 推定した年度を使って「YYYY-11-09」
5. 時刻は「HH:MM」形式（24時間表記）

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
