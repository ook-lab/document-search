"""検索クエリ拡張（doc-search 専用）。"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from loguru import logger

from docsearch.llm import DocSearchLLM


class QueryExpander:
    def __init__(self, llm_client: Optional[DocSearchLLM] = None) -> None:
        self.llm_client = llm_client or DocSearchLLM()

    def expand_query(self, query: str, max_keywords: int = 10) -> Dict[str, Any]:
        if not query or len(query.strip()) < 2:
            return {
                "original_query": query,
                "expanded_query": query,
                "keywords": [],
                "expansion_applied": False,
            }
        if not self._should_expand(query):
            return {
                "original_query": query,
                "expanded_query": query,
                "keywords": [query],
                "expansion_applied": False,
            }
        try:
            prompt = self._build_expansion_prompt(query, max_keywords)
            response = self.llm_client.call_model(
                tier="utility",
                prompt=prompt,
                model_name="gemini-2.5-flash-lite",
            )
            if not response.get("success"):
                return self._fallback_expansion(query)
            expanded_text = (response.get("content") or "").strip()
            if not expanded_text:
                return self._fallback_expansion(query)
            keywords = [kw.strip() for kw in expanded_text.split() if kw.strip()]
            keywords = list(dict.fromkeys(keywords))[:max_keywords]
            expanded_query = " ".join(keywords)
            return {
                "original_query": query,
                "expanded_query": expanded_query,
                "keywords": keywords,
                "expansion_applied": True,
            }
        except Exception as e:
            logger.error("expand_query: {}", e, exc_info=True)
            return self._fallback_expansion(query)

    def _should_expand(self, query: str) -> bool:
        if len(query) > 100:
            return False
        if "（" in query or "(" in query:
            return False
        date_pattern = r"^\d{1,2}月\d{1,2}日$|^\d{1,2}/\d{1,2}$"
        if re.match(date_pattern, query):
            return False
        return True

    def _build_expansion_prompt(self, query: str, max_keywords: int) -> str:
        return f"""あなたは検索エンジンの専門家です。ユーザーの検索クエリを、検索システムが拾いやすいように関連語を含めて拡張してください。

【重要なルール】
1. 元のクエリの意図を保ちながら、同義語・関連語を追加する
2. 回答は拡張したキーワードのみをスペース区切りで返す（説明文は不要）
3. 最大{max_keywords}個のキーワードに収める
4. 日本語の検索なので、日本語でよく使われる表現を優先する

【ユーザー入力】
{query}

【拡張キーワード（スペース区切り）】
"""

    def _fallback_expansion(self, query: str) -> Dict[str, Any]:
        expansion_rules = {
            "予定": ["予定", "スケジュール", "行事", "イベント", "日程", "カレンダー"],
            "議事録": ["議事録", "会議", "決定事項", "議題", "協議"],
            "時間割": ["時間割", "授業", "科目", "スケジュール", "クラス"],
            "連絡": ["連絡", "お知らせ", "通知", "案内"],
        }
        keywords = [query]
        for key, related_words in expansion_rules.items():
            if key in query:
                keywords.extend([w for w in related_words if w not in keywords])
        expanded_query = " ".join(keywords)
        return {
            "original_query": query,
            "expanded_query": expanded_query,
            "keywords": keywords,
            "expansion_applied": True,
        }
