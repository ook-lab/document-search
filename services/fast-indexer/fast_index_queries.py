"""
fast-indexer 一覧用: pipeline_meta を raw の file_url と突き合わせ、
「ファイル（Drive 等）あり」と「テキストオンリー」を判定する。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

DRIVE_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")


def drive_id_from_file_url(file_url: Optional[str]) -> Optional[str]:
    if not file_url:
        return None
    m = DRIVE_URL_RE.search(str(file_url))
    return m.group(1) if m else None


def raw_row_has_file_backing(file_url: Optional[str]) -> bool:
    """HTTP(S) の添付・Drive URL など、ファイル経路がありそうなとき True。"""
    if not file_url or not str(file_url).strip():
        return False
    s = str(file_url).strip().lower()
    return s.startswith("http") or s.startswith("//")


def _gmail_without_attachment(r: Dict[str, Any], file_url: Optional[str]) -> bool:
    src = (r.get("source") or "").strip().lower()
    if src != "gmail":
        return False
    has_pm_drive = bool((r.get("drive_file_id") or "").strip())
    has_md = bool((r.get("md_content") or "").strip())
    return not has_pm_drive and not has_md and not raw_row_has_file_backing(file_url)


def fetch_pending_fast_index_docs(
    db_client: Any,
    raw_tables: Sequence[str],
    *,
    meta_limit: int = 500,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    processing_status != completed の pipeline_meta を対象 raw のみ返す。
    戻り値: (docs 各行に fast_kind を付与, 取得失敗時のエラーメッセージ)
    """
    tables = list(raw_tables)
    if not tables:
        return [], None

    try:
        res = (
            db_client.table("pipeline_meta")
            .select(
                "id, raw_id, raw_table, source, person, created_at, processing_status, "
                "drive_file_id, md_content, text_embedded"
            )
            .in_("raw_table", tables)
            .neq("processing_status", "completed")
            .or_("text_embedded.is.null,text_embedded.eq.false")
            .order("created_at", desc=True)
            .limit(meta_limit)
            .execute()
        )
    except Exception as e:
        return [], f"pipeline_meta の取得に失敗しました: {e}"

    rows: List[Dict[str, Any]] = list(res.data or [])
    if not rows:
        return [], None

    # raw_id を raw_table ごとにバッチ取得
    by_table: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        rt = r.get("raw_table")
        rid = r.get("raw_id")
        if not rt or not rid:
            continue
        has_pm_drive = bool((r.get("drive_file_id") or "").strip())
        has_md = bool((r.get("md_content") or "").strip())
        if has_pm_drive or has_md:
            continue
        by_table[str(rt)].append(r)

    file_url_by_pair: Dict[Tuple[str, str], Optional[str]] = {}
    for rt, group in by_table.items():
        ids: List[str] = []
        seen: Set[str] = set()
        for item in group:
            rid = str(item["raw_id"])
            if rid not in seen:
                seen.add(rid)
                ids.append(rid)
        chunk_size = 80
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            fetched: Dict[str, Optional[str]] = {}
            try:
                raw_res = (
                    db_client.table(rt).select("id, file_url").in_("id", chunk).execute()
                )
                for row in raw_res.data or []:
                    if row.get("id") is not None:
                        fetched[str(row["id"])] = row.get("file_url")
            except Exception:
                fetched = {}
            for rid in chunk:
                file_url_by_pair[(rt, rid)] = fetched.get(rid)

    out: List[Dict[str, Any]] = []
    for r in rows:
        rt = r.get("raw_table")
        rid = r.get("raw_id")
        if not rt or not rid:
            continue
        has_pm_drive = bool((r.get("drive_file_id") or "").strip())
        has_md = bool((r.get("md_content") or "").strip())
        fu: Optional[str] = None
        if not has_pm_drive and not has_md:
            fu = file_url_by_pair.get((str(rt), str(rid)))
        has_file = has_pm_drive or has_md or raw_row_has_file_backing(fu) or bool(
            drive_id_from_file_url(fu)
        )

        if _gmail_without_attachment(r, fu):
            continue

        if has_file:
            r = {**r, "fast_kind": "file_or_md"}
        else:
            src = (r.get("source") or "").strip().lower()
            if src == "gmail":
                continue
            r = {**r, "fast_kind": "text_only"}
        out.append(r)

    out.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
    return out, None
