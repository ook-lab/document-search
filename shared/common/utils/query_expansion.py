"""
クエリ拡張（Query Expansion）ユーティリティ

ユーザーの検索クエリをLLMで拡張し、同義語や関連語を含めることで
検索精度を向上させます。
"""
import re
from typing import Dict, Any, Optional
from loguru import logger
from shared.ai.llm_client.llm_client import LLMClient


class QueryExpander:
    """
    検索クエリを拡張するクラス

    目的：
    - ユーザーが「予定」と検索したときに、「行事」「イベント」「スケジュール」も検索対象に含める
    - ベクトル検索と全文検索の両方で効果を発揮
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Args:
            llm_client: LLMクライアント（指定しない場合は新規作成）
        """
        self.llm_client = llm_client or LLMClient()

    def expand_query(self, query: str, max_keywords: int = 10) -> Dict[str, Any]:
        """
        検索クエリを拡張

        Args:
            query: 元のクエリ
            max_keywords: 拡張キーワードの最大数

        Returns:
            {
                "original_query": str,      # 元のクエリ
                "expanded_query": str,      # 拡張されたクエリ（スペース区切り）
                "keywords": List[str],      # 抽出されたキーワードリスト
                "expansion_applied": bool   # 拡張が適用されたかどうか
            }
        """
        if not query or len(query.strip()) < 2:
            logger.warning("[クエリ拡張] クエリが空または短すぎます")
            return {
                "original_query": query,
                "expanded_query": query,
                "keywords": [],
                "expansion_applied": False
            }

        # 拡張が必要かどうかを判定（短いクエリやキーワード検索には効果的）
        if not self._should_expand(query):
            logger.info(f"[クエリ拡張] スキップ: '{query}'")
            return {
                "original_query": query,
                "expanded_query": query,
                "keywords": [query],
                "expansion_applied": False
            }

        try:
            # LLMでクエリを拡張
            prompt = self._build_expansion_prompt(query, max_keywords)
            response = self.llm_client.call_model(
                tier="utility",  # 軽量なタスク
                prompt=prompt,
                model_name="gemini-2.5-flash"  # 高速モデル
            )

            if not response.get('success'):
                logger.warning(f"[クエリ拡張] LLM呼び出し失敗: {response.get('error')}")
                return self._fallback_expansion(query)

            expanded_text = response.get('content', '').strip()

            if not expanded_text:
                logger.warning("[クエリ拡張] 空の応答")
                return self._fallback_expansion(query)

            # キーワードを抽出（スペース区切り）
            keywords = [kw.strip() for kw in expanded_text.split() if kw.strip()]

            # 重複削除
            keywords = list(dict.fromkeys(keywords))

            # 最大数に制限
            if len(keywords) > max_keywords:
                keywords = keywords[:max_keywords]

            expanded_query = " ".join(keywords)

            logger.info(f"[クエリ拡張] 成功: '{query}' → '{expanded_query}'")

            return {
                "original_query": query,
                "expanded_query": expanded_query,
                "keywords": keywords,
                "expansion_applied": True
            }

        except Exception as e:
            logger.error(f"[クエリ拡張] エラー: {e}", exc_info=True)
            return self._fallback_expansion(query)

    def _should_expand(self, query: str) -> bool:
        """
        クエリ拡張が必要かどうか判定

        Args:
            query: 検索クエリ

        Returns:
            True: 拡張すべき
            False: 拡張不要
        """
        # 長すぎるクエリは拡張しない（すでに十分詳細）
        if len(query) > 100:
            return False

        # 固有名詞のみのクエリは拡張しない（例: 「学年通信（29）」）
        # カッコ付き固有名詞を含む場合は拡張しない
        if '（' in query or '(' in query:
            return False

        # 日付のみのクエリは拡張しない（例: 「12月4日」）
        date_pattern = r'^\d{1,2}月\d{1,2}日$|^\d{1,2}/\d{1,2}$'
        if re.match(date_pattern, query):
            return False

        # その他のケースは拡張する
        return True

    def _build_expansion_prompt(self, query: str, max_keywords: int) -> str:
        """
        クエリ拡張用のプロンプトを構築

        Args:
            query: 元のクエリ
            max_keywords: 最大キーワード数

        Returns:
            プロンプト文字列
        """
        prompt = f"""あなたは検索エンジンの専門家です。ユーザーの検索クエリを、検索システムが拾いやすいように関連語を含めて拡張してください。

【重要なルール】
1. 元のクエリの意図を保ちながら、同義語・関連語を追加する
2. 回答は拡張したキーワードのみをスペース区切りで返す（説明文は不要）
3. 最大{max_keywords}個のキーワードに収める
4. 日本語の検索なので、日本語でよく使われる表現を優先する

【拡張例】
入力: 12月の予定
出力: 12月 予定 行事 イベント スケジュール カレンダー 実施計画 日程

入力: 委員会の議事録
出力: 委員会 議事録 会議 決定事項 議題 協議 打ち合わせ

入力: 時間割
出力: 時間割 授業 科目 スケジュール クラス 予定表 カリキュラム

【ユーザー入力】
{query}

【拡張キーワード（スペース区切り）】
"""
        return prompt

    def _fallback_expansion(self, query: str) -> Dict[str, Any]:
        """
        LLMが使えない場合のフォールバック（ルールベース拡張）

        Args:
            query: 元のクエリ

        Returns:
            拡張結果
        """
        # ルールベースの簡易拡張
        expansion_rules = {
            "予定": ["予定", "スケジュール", "行事", "イベント", "日程", "カレンダー"],
            "議事録": ["議事録", "会議", "決定事項", "議題", "協議"],
            "時間割": ["時間割", "授業", "科目", "スケジュール", "クラス"],
            "連絡": ["連絡", "お知らせ", "通知", "案内"],
        }

        keywords = [query]  # 元のクエリを最初に含める

        # クエリに含まれるキーワードに対応する拡張語を追加
        for key, related_words in expansion_rules.items():
            if key in query:
                keywords.extend([w for w in related_words if w not in keywords])

        expanded_query = " ".join(keywords)

        logger.info(f"[クエリ拡張] フォールバック適用: '{query}' → '{expanded_query}'")

        return {
            "original_query": query,
            "expanded_query": expanded_query,
            "keywords": keywords,
            "expansion_applied": True
        }
