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
from shared.common.database.client import DatabaseClient

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
        document_id=None,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite"
    ):
        """
        Text AI Processor 初期化

        Args:
            document_id: ドキュメントID（Supabase保存用）
            api_key: Google AI API Key
            model_name: 使用するモデル名（gemini-2.5-flash-lite）
        """
        self.document_id = document_id
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
        articles: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        log_file=None
    ) -> Dict[str, Any]:
        """
        articles からイベント、タスク、注意事項を抽出

        Args:
            articles: G-21 で生成された articles
                [{'title': str, 'body': str}, ...]
            year_context: 年度コンテキスト（日付推定に使用）
            log_file: ログファイルパス（オプション）

        Returns:
            {
                'success': bool,
                'calendar_events': list,
                'tasks': list,
                'notices': list,
                'tokens_used': int
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G-22]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(articles, year_context)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(
        self,
        articles: List[Dict[str, Any]],
        year_context: Optional[int] = None
    ) -> Dict[str, Any]:
        """process() の実装本体"""
        logger.info("[G-22] ========================================")
        logger.info("[G-22] AI処理開始")
        logger.info("[G-22] ========================================")

        if not GENAI_AVAILABLE or not self.model:
            logger.error("[G-22] Gemini API が利用できません")
            return self._error_result("Gemini API not available")

        # 入力データのサマリーをログ出力
        logger.info("[G-22]")
        logger.info("[G-22] ========== 入力データ詳細（G-21 の全 articles） ==========")
        logger.info(f"[G-22] articles: {len(articles)}件")

        if not articles:
            logger.info("[G-22] (処理する articles がありません)")
            return {
                'success': True,
                'summary': '',
                'tags': [],
                'people': [],
                'topic_sections': [],
                'calendar_events': [],
                'tasks': [],
                'notices': [],
                'tokens_used': 0
            }

        # G-21 の全 articles の詳細をログ出力
        for i, article in enumerate(articles, 1):
            title = article.get('title', '(タイトルなし)')
            body = article.get('body', '')
            logger.info(f"[G-22] Article {i}: title={title} body={len(body)}文字")
        logger.info("[G-22] " + "=" * 60)

        try:
            # G-21 の全 articles をそのまま統合してAIに渡す
            logger.info("[G-22]")
            logger.info("[G-22] G-21 の全 articles を統合中...")
            full_text = self._combine_articles(articles)
            logger.info(f"[G-22] 統合完了: {len(full_text)}文字")

            # プロンプトを構築
            logger.info("[G-22]")
            logger.info("[G-22] プロンプトを構築中...")
            prompt = self._build_prompt(full_text, year_context)
            logger.info(f"[G-22] プロンプト構築完了: {len(prompt)}文字")
            logger.info(f"[G-22] 年度コンテキスト: {year_context if year_context else 'なし（AIが推定）'}")

            # プロンプトの全文をログ出力
            logger.info("[G-22]")
            logger.info("[G-22] ========== AI に渡すプロンプト（全文） ==========")
            logger.info(f"[G-22] {prompt}")
            logger.info("[G-22] " + "=" * 60)

            # モデル情報をログ出力
            logger.info("[G-22]")
            logger.info("[G-22] ========== AI モデル情報 ==========")
            logger.info(f"[G-22] モデル名: {self.model_name}")
            logger.info(f"[G-22] 入力テキスト長: {len(full_text)}文字")
            logger.info(f"[G-22] プロンプト長: {len(prompt)}文字")
            logger.info("[G-22] " + "=" * 60)

            # Gemini に送信
            logger.info("[G-22]")
            logger.info("[G-22] Gemini API にリクエスト送信中...")
            response = self.model.generate_content(prompt)
            raw_text = response.text
            logger.info(f"[G-22] API レスポンス受信: {len(raw_text)}文字")

            # AI レスポンスの全文をログ出力
            logger.info("[G-22]")
            logger.info("[G-22] ========== AI レスポンス（全文） ==========")
            logger.info(f"[G-22] {raw_text}")
            logger.info("[G-22] " + "=" * 60)

            # トークン使用量をログ出力
            tokens_input = len(prompt) // 4
            tokens_output = len(raw_text) // 4
            tokens_used = tokens_input + tokens_output

            logger.info("[G-22]")
            logger.info("[G-22] ========== トークン使用量 ==========")
            logger.info(f"[G-22] 入力トークン（概算）: {tokens_input}")
            logger.info(f"[G-22] 出力トークン（概算）: {tokens_output}")
            logger.info(f"[G-22] 合計トークン（概算）: {tokens_used}")
            logger.info("[G-22] " + "=" * 60)

            # JSONをパース
            logger.info("[G-22]")
            logger.info("[G-22] AI レスポンスをパース中...")
            result = self._parse_response(raw_text)
            logger.info("[G-22] パース完了")

            # 各 article の処理結果を詳細にログ出力
            logger.info("[G-22]")
            logger.info("[G-22] ========== 抽出結果詳細 ==========")

            summary = result.get('summary', '')
            tags = result.get('tags', [])
            people = result.get('people', [])
            topic_sections = result.get('topic_sections', [])
            calendar_events = result.get('calendar_events', [])
            tasks = result.get('tasks', [])
            notices = result.get('notices', [])

            logger.info(f"[G-22] summary: {summary[:50]}..." if len(summary) > 50 else f"[G-22] summary: {summary}")
            logger.info(f"[G-22] tags: {tags}")
            logger.info(f"[G-22] people: {people}")
            logger.info(f"[G-22] topic_sections: {len(topic_sections)}件")
            for i, sec in enumerate(topic_sections, 1):
                logger.info(f"[G-22]   Section {i}: title={sec.get('title')} body={len(sec.get('body',''))}文字")

            logger.info(f"[G-22] calendar_events: {len(calendar_events)}件")
            if calendar_events:
                for i, event in enumerate(calendar_events, 1):
                    logger.info(f"[G-22]   Event {i}:")
                    for key, value in event.items():
                        logger.info(f"[G-22]     {key}: {value}")
            else:
                logger.info("[G-22]   (なし)")

            logger.info("[G-22]")
            logger.info(f"[G-22] tasks: {len(tasks)}件")
            if tasks:
                for i, task in enumerate(tasks, 1):
                    logger.info(f"[G-22]   Task {i}:")
                    for key, value in task.items():
                        logger.info(f"[G-22]     {key}: {value}")
            else:
                logger.info("[G-22]   (なし)")

            logger.info("[G-22]")
            logger.info(f"[G-22] notices: {len(notices)}件")
            if notices:
                for i, notice in enumerate(notices, 1):
                    logger.info(f"[G-22]   Notice {i}:")
                    for key, value in notice.items():
                        logger.info(f"[G-22]     {key}: {value}")
            else:
                logger.info("[G-22]   (なし)")

            logger.info("[G-22] " + "=" * 60)

            # 最終結果のサマリー
            logger.info("[G-22]")
            logger.info("[G-22] ========== 最終結果サマリー ==========")
            logger.info(f"[G-22]   ├─ 総イベント数: {len(calendar_events)}件")
            logger.info(f"[G-22]   ├─ 総タスク数: {len(tasks)}件")
            logger.info(f"[G-22]   ├─ 総注意事項数: {len(notices)}件")
            logger.info(f"[G-22]   └─ トークン使用量: {tokens_used}")
            logger.info("[G-22] " + "=" * 60)

            logger.info("[G-22]")
            logger.info("[G-22] ========================================")
            logger.info("[G-22] AI処理完了")
            logger.info("[G-22] ========================================")

            result = {
                'success': True,
                'summary': summary,
                'tags': tags,
                'people': people,
                'topic_sections': topic_sections,
                'calendar_events': calendar_events,
                'tasks': tasks,
                'notices': notices,
                'tokens_used': tokens_used
            }

            # Supabaseに保存
            if self.document_id:
                try:
                    db = DatabaseClient(use_service_role=True)
                    update_data = {
                        'g22_ai_extracted': {
                            'summary': summary,
                            'tags': tags,
                            'people': people,
                            'calendar_events': calendar_events,
                            'tasks': tasks,
                            'notices': notices
                        }
                    }
                    # topic_sections が得られた場合、g21_articles を AI 出力で上書き
                    if topic_sections:
                        topic_articles = [{'title': s.get('title', ''), 'body': s.get('body', '')} for s in topic_sections]
                        update_data['g21_articles'] = topic_articles
                        logger.info(f"[G-22] ✓ g21_articles を AI トピック版で上書き: {len(topic_articles)}件")

                    db.client.table('Rawdata_FILE_AND_MAIL').update(update_data).eq('id', self.document_id).execute()
                    logger.info(f"[G-22] ✓ Supabase 保存完了: イベント{len(calendar_events)}件, タスク{len(tasks)}件, 注意事項{len(notices)}件")
                except Exception as e:
                    logger.error(f"[G-22] Supabase保存エラー: {e}")

            return result

        except Exception as e:
            logger.error("[G-22] ========================================")
            logger.error(f"[G-22] AI処理エラー: {e}")
            logger.error("[G-22] ========================================")
            logger.error("", exc_info=True)
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

    def _build_prompt(self, text: str, year_context: Optional[int] = None) -> str:
        """
        プロンプトを構築

        Args:
            text: 入力テキスト
            year_context: 年度コンテキスト

        Returns:
            プロンプト文字列
        """
        # 年度コンテキスト情報を追加
        from datetime import datetime
        context_info = ""
        if year_context:
            context_info = f"\n**年度ヒント**: {year_context}年の文書です。日付の年を推定する際に参考にしてください。\n"
        else:
            current_year = datetime.now().year
            context_info = f"\n**年度情報**: テキストから年度を推定してください。不明な場合は{current_year}年を使用してください。\n"

        return f"""
あなたは学校通信の情報を整理する専門家です。
{context_info}
以下は G-21 が生成した全 articles です。各 article には「# ラベル」が付いており、
ラベルの意味は以下の通りです：
  - 送信者 / メール / 送信日時 / 件名 / 本文 → メール由来のフィールド
  - 段落N → PDF添付ファイルの地の文（段落単位）

この全データを使って、以下の JSON を返してください：

1. **summary**: 文書全体の内容を1〜2文で日本語要約
   - 何についての文書か、主要なトピックを端的に記述

2. **tags**: 文書のキーワードタグ（配列）
   - 例：["運動会", "持ち物", "保護者向け"]
   - 5〜10個程度

3. **people**: 文書に登場する人物・役職（配列）
   - 例：["担任 田中先生", "PTA会長", "校長"]
   - 固有名詞・役職名のみ。不明な場合は空配列

4. **topic_sections**: 全データを内容のまとまりごとにグループ化してください。
   - 各セクションに適切な日本語タイトルをつける（例：「メール情報」「運動会について」「持ち物」）
   - ラベルの異なるデータ（メール情報 vs PDF地の文）を適切に分類すること
   - 全 articles のデータをカバーすること（省略しない）

5. **calendar_events**: 日付（YYYY-MM-DD形式）、時間、イベント名、場所
   - 「1/15」のような表記は年度ヒントを参考に完全な日付に変換すること

6. **tasks**: 提出物、準備物、持ち物

7. **notices**: 重要な連絡、変更点

[全 articles]
{text}

出力形式:
```json
{{
  "summary": "今年の運動会についての案内です。...",
  "tags": ["運動会", "持ち物", "保護者向け"],
  "people": ["担任 田中先生", "校長"],
  "topic_sections": [
    {{
      "title": "運動会について",
      "body": "今年の運動会は..."
    }}
  ],
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
- 抽出できない場合は空の配列（または空文字列）を返すこと
- 日付は可能な限り YYYY-MM-DD 形式に変換すること
- topic_sections は必ず全文をカバーすること（省略・要約しない）
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
                'topic_sections': [],
                'calendar_events': [],
                'tasks': [],
                'notices': []
            }

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'summary': '',
            'tags': [],
            'people': [],
            'topic_sections': [],
            'calendar_events': [],
            'tasks': [],
            'notices': [],
            'tokens_used': 0
        }
