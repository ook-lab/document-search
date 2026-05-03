"""Web 検索（DuckDuckGo）の結果からリンク URL を上から順に取得."""

from __future__ import annotations

from typing import List, Optional

from ddgs import DDGS

from fetch_urls import normalize_date_ytdlp

_VALID_TL = frozenset({"d", "w", "m", "y"})


def _to_after_before_iso(date_after: Optional[str], date_before: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """YYYY-MM-DD。不正なら normalize が例外。"""
    da = (date_after or "").strip() or None
    db = (date_before or "").strip() or None
    if not da and not db:
        return None, None
    raw_a = normalize_date_ytdlp(da) if da else None
    raw_b = normalize_date_ytdlp(db) if db else None
    def fmt(x: Optional[str]) -> Optional[str]:
        if not x:
            return None
        return f"{x[:4]}-{x[4:6]}-{x[6:8]}"

    a_iso = fmt(raw_a)
    b_iso = fmt(raw_b)
    if a_iso and b_iso and a_iso > b_iso:
        raise RuntimeError("開始日が終了日より後になっています。")
    return a_iso, b_iso


def fetch_web_search_urls(
    query: str,
    max_results: int = 20,
    *,
    timelimit: Optional[str] = None,
    date_after: Optional[str] = None,
    date_before: Optional[str] = None,
) -> List[str]:
    q = (query or "").strip()
    if not q:
        return []
    n = max(1, min(int(max_results), 200))
    tl = (timelimit or "").strip().lower() or None
    if tl and tl not in _VALID_TL:
        tl = None

    a_iso, b_iso = _to_after_before_iso(date_after, date_before)
    if a_iso or b_iso:
        parts = [q]
        if a_iso:
            parts.append(f"after:{a_iso}")
        if b_iso:
            parts.append(f"before:{b_iso}")
        full_q = " ".join(parts)
        tl = None
    else:
        full_q = q

    urls: List[str] = []
    seen: set[str] = set()
    try:
        with DDGS() as ddgs:
            kwargs: dict = {"max_results": n}
            if tl:
                kwargs["timelimit"] = tl
            for row in ddgs.text(full_q, **kwargs):
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
