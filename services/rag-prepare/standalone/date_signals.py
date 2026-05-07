"""09 行単位の日付シグナル生成（パイプライン非依存）。"""
from __future__ import annotations

import json
import re
import unicodedata
import calendar
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


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


def _coerce_day(v: Any) -> Optional[date]:
    """PostgREST / Python から来る日付を date に正規化。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, str) and len(v) >= 10:
        return _safe_date(v[:10])
    return None


def _base_date(row: Dict[str, Any]) -> date:
    for key in ("post_at", "start_at", "end_at", "due_date"):
        d = _coerce_day(row.get(key))
        if d:
            return d
    return datetime.now().date()


def _coerce_meta(meta: Any) -> Dict[str, Any]:
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str) and meta.strip():
        try:
            loaded = json.loads(meta)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}
    return {}


def build_date_signals(
    row: Dict[str, Any],
    *,
    extra_text: str = "",
    merge_meta_date_signals: bool = True,
) -> Dict[str, Any]:
    """09 行の構造化情報＋本文から date_signals 形の dict を組み立てる。

    ``extra_text`` は全文に相当する本文の追補のみ（例: 09.body 未反映の取り込み用）。
    ``merge_meta_date_signals`` が False のとき、meta 内の旧 date_signals は取り込まない（ix_date_signals 用）。
    """
    base = _base_date(row)
    meta = _coerce_meta(row.get("meta"))
    body = str(row.get("body") or "")
    title = str(row.get("title") or "")
    ui_data = row.get("ui_data")

    text_parts = [title, body, (extra_text or "").strip()]
    if isinstance(ui_data, dict):
        for tl in ui_data.get("timeline") or []:
            if isinstance(tl, dict):
                text_parts.append(str(tl.get("date") or ""))
                text_parts.append(str(tl.get("event") or ""))
    text = "\n".join(text_parts)
    text = unicodedata.normalize("NFKC", text)

    normalized_dates: List[str] = []
    normalized_ranges: List[Dict[str, str]] = []
    partial_dates: List[Dict[str, Any]] = []

    if merge_meta_date_signals:
        existing = meta.get("date_signals") if isinstance(meta, dict) else None
        if isinstance(existing, dict):
            normalized_dates.extend([str(x) for x in (existing.get("normalized_dates") or [])])
            normalized_ranges.extend(
                [x for x in (existing.get("normalized_ranges") or []) if isinstance(x, dict)]
            )
            partial_dates.extend([x for x in (existing.get("partial_dates") or []) if isinstance(x, dict)])

    # 既存 all_dates 互換
    normalized_dates.extend([str(x) for x in (meta.get("all_dates") or [])])

    # 構造化列
    for key in ("post_at", "start_at", "end_at", "due_date"):
        cd = _coerce_day(row.get(key))
        if cd:
            normalized_dates.append(cd.isoformat())
    s_d = _coerce_day(row.get("start_at"))
    e_d = _coerce_day(row.get("end_at"))
    if s_d and e_d:
        normalized_ranges.append(
            {"start": s_d.isoformat(), "end": e_d.isoformat(), "source_text": "start_at/end_at"}
        )

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


def build_ix_search_date_list(
    row: Dict[str, Any],
    signals: Dict[str, Any],
    *,
    max_range_span: int = 400,
) -> List[str]:
    """
    検索加点用 date[] に載せる全日（重複除去・昇順 ISO 文字列）。
    09 の日付列・signals の単日・レンジ（日単位展開）・月粒度（その月の全日）を集約する。
    """
    acc: set[date] = set()

    for key in ("post_at", "start_at", "end_at", "due_date"):
        d = _coerce_day(row.get(key))
        if d:
            acc.add(d)

    for s in signals.get("normalized_dates") or []:
        d = _safe_date(str(s)[:10])
        if d:
            acc.add(d)

    for r in signals.get("normalized_ranges") or []:
        if not isinstance(r, dict):
            continue
        sa = _safe_date(str(r.get("start") or "")[:10])
        ea = _safe_date(str(r.get("end") or "")[:10])
        if not sa or not ea or ea < sa:
            continue
        n = 0
        cur = sa
        while cur <= ea and n < max_range_span:
            acc.add(cur)
            cur = cur + timedelta(days=1)
            n += 1

    for p in signals.get("partial_dates") or []:
        if not isinstance(p, dict):
            continue
        try:
            y = int(p.get("year"))
            m = int(p.get("month"))
        except Exception:
            continue
        if m < 1 or m > 12:
            continue
        _, last = calendar.monthrange(y, m)
        for day in range(1, last + 1):
            try:
                acc.add(date(y, m, day))
            except Exception:
                pass

    return sorted({d.isoformat() for d in acc})

