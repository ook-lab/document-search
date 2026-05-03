"""Web 検索（DuckDuckGo）の結果からリンク URL を上から順に取得."""

from __future__ import annotations

from typing import List

from ddgs import DDGS


def fetch_web_search_urls(query: str, max_results: int = 20) -> List[str]:
    q = (query or "").strip()
    if not q:
        return []
    n = max(1, min(int(max_results), 50))
    urls: List[str] = []
    seen: set[str] = set()
    try:
        with DDGS() as ddgs:
            for row in ddgs.text(q, max_results=n):
                href = (row.get("href") or "").strip()
                if not href or href in seen:
                    continue
                seen.add(href)
                urls.append(href)
                if len(urls) >= n:
                    break
    except Exception as e:
        raise RuntimeError(str(e) or "Web 検索に失敗しました。") from e
    return urls
