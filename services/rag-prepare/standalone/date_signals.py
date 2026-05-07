"""09 行単位の日付シグナル生成（パイプライン非依存）。"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple


def _safe_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _dedupe_sorted(dates: List[str]) -> List[str]:
    uniq = sorted({d for d in dates if _safe_date(d)})
    return uniq


def _normalize_year(base: date, month: int, day: int) -> Optional[date]:
    """基準日に近い年を補完（年跨ぎを許容）。"""
    candidates = []
    for y in (base.year - 1, base.year, base.year + 1):
        try:
            candidates.append(date(y, month, day))
        except Exception:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - base).days))


def _extract_iso_dates(text: str) -> List[str]:
    out: List[str] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        d = _safe_date(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
        if d:
            out.append(d.isoformat())
    return out


def _extract_month_day_dates(text: str, base: date) -> List[str]:
    out: List[str] = []
    # 5/8, 5月8日, 5.8
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*(?:/|\.|月)\s*(\d{1,2})\s*日?", text):
        mm = int(m.group(1))
        dd = int(m.group(2))
        d = _normalize_year(base, mm, dd)
        if d:
            out.append(d.isoformat())
    return out


def _extract_ranges(text: str, base: date) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    # 5/7〜5/13, 5月7日-5月13日
    pat = re.compile(
        r"(?<!\d)(\d{1,2})\s*(?:/|\.|月)\s*(\d{1,2})\s*日?\s*[〜~\-ー－]\s*(\d{1,2})\s*(?:/|\.|月)\s*(\d{1,2})\s*日?"
    )
    for m in pat.finditer(text):
        sm, sd = int(m.group(1)), int(m.group(2))
        em, ed = int(m.group(3)), int(m.group(4))
        s = _normalize_year(base, sm, sd)
        e = _normalize_year(base, em, ed)
        if not s or not e:
            continue
        if e < s:
            # 年跨ぎ補正（例: 12/28〜1/5）
            try:
                e = date(s.year + 1, em, ed)
            except Exception:
                pass
        out.append({"start": s.isoformat(), "end": e.isoformat(), "source_text": m.group(0)})
    return out


def _extract_partial_dates(text: str, base: date) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in re.finditer(r"(?<!\d)(\d{1,2})月号\b", text):
        month = int(m.group(1))
        out.append(
            {
                "year": base.year,
                "month": month,
                "day": None,
                "text": m.group(0),
                "granularity": "month",
            }
        )
    return out


def _base_date(row: Dict[str, Any]) -> date:
    for key in ("post_at", "start_at", "end_at", "due_date"):
        v = row.get(key)
        if isinstance(v, str) and len(v) >= 10:
            d = _safe_date(v[:10])
            if d:
                return d
    return datetime.now().date()


def build_date_signals(row: Dict[str, Any]) -> Dict[str, Any]:
    """09 row -> date_signals dict."""
    base = _base_date(row)
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    body = str(row.get("body") or "")
    title = str(row.get("title") or "")
    ui_data = row.get("ui_data")

    text_parts = [title, body]
    if isinstance(ui_data, dict):
        for tl in ui_data.get("timeline") or []:
            if isinstance(tl, dict):
                text_parts.append(str(tl.get("date") or ""))
                text_parts.append(str(tl.get("event") or ""))
    text = "\n".join(text_parts)

    normalized_dates: List[str] = []
    normalized_ranges: List[Dict[str, str]] = []
    partial_dates: List[Dict[str, Any]] = []

    existing = meta.get("date_signals") if isinstance(meta, dict) else None
    if isinstance(existing, dict):
        normalized_dates.extend([str(x) for x in (existing.get("normalized_dates") or [])])
        normalized_ranges.extend([x for x in (existing.get("normalized_ranges") or []) if isinstance(x, dict)])
        partial_dates.extend([x for x in (existing.get("partial_dates") or []) if isinstance(x, dict)])

    # 既存 all_dates 互換
    normalized_dates.extend([str(x) for x in (meta.get("all_dates") or [])])

    # 構造化列
    for key in ("post_at", "start_at", "end_at", "due_date"):
        v = row.get(key)
        if isinstance(v, str) and len(v) >= 10 and _safe_date(v[:10]):
            normalized_dates.append(v[:10])
    if isinstance(row.get("start_at"), str) and isinstance(row.get("end_at"), str):
        s = row.get("start_at", "")[:10]
        e = row.get("end_at", "")[:10]
        if _safe_date(s) and _safe_date(e):
            normalized_ranges.append({"start": s, "end": e, "source_text": "start_at/end_at"})

    # 本文抽出
    normalized_dates.extend(_extract_iso_dates(text))
    normalized_dates.extend(_extract_month_day_dates(text, base))
    normalized_ranges.extend(_extract_ranges(text, base))
    partial_dates.extend(_extract_partial_dates(text, base))

    # normalize outputs
    normalized_dates = _dedupe_sorted(normalized_dates)
    seen = set()
    nr: List[Dict[str, str]] = []
    for r in normalized_ranges:
        s = str(r.get("start") or "")
        e = str(r.get("end") or "")
        if not (_safe_date(s) and _safe_date(e)):
            continue
        k = (s, e, str(r.get("source_text") or ""))
        if k in seen:
            continue
        seen.add(k)
        nr.append({"start": s, "end": e, "source_text": k[2]})
    normalized_ranges = nr

    pp: List[Dict[str, Any]] = []
    seen_p = set()
    for p in partial_dates:
        try:
            y = int(p.get("year"))
            m = int(p.get("month"))
            d = p.get("day")
            txt = str(p.get("text") or "")
            g = str(p.get("granularity") or "month")
        except Exception:
            continue
        key = (y, m, d, txt, g)
        if key in seen_p:
            continue
        seen_p.add(key)
        pp.append({"year": y, "month": m, "day": d, "text": txt, "granularity": g})
    partial_dates = pp

    return {
        "normalized_dates": normalized_dates,
        "normalized_ranges": normalized_ranges,
        "partial_dates": partial_dates,
    }

