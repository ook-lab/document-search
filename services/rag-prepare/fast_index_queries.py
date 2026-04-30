"""
rag-prepare 一覧用: pipeline_meta・raw・09_unified_documents を突き合わせ、
区分・表示用ソース/ファイル名を付与する。

構造化（doc-processor G 相当）は 09 の ui_data を正とする。
完了判定は pipeline_meta.processing_status のみ
（K 完了・ベクトル化完了とも completed）。
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


def md_layer_in_pipeline_meta(md_content: Optional[str]) -> bool:
    """pipeline_meta.md_content に MD 層（本文）が入っているか。"""
    return bool((md_content or "").strip())


def body_layer_in_09(ud_row: Dict[str, Any]) -> bool:
    """09_unified_documents.body にインデックス用の本文があるか（FastIndexer が参照する経路）。"""
    b = ud_row.get("body")
    return bool((b or "").strip())


def structured_in_09(ui_data: Any) -> bool:
    """09_unified_documents.ui_data に G 由来の構造化 JSON があるか。"""
    if ui_data is None:
        return False
    if isinstance(ui_data, dict):
        return len(ui_data) > 0
    if isinstance(ui_data, list):
        return len(ui_data) > 0
    if isinstance(ui_data, str):
        t = ui_data.strip()
        if not t or t in ("{}", "null", "[]"):
            return False
        return True
    return True


def _gmail_without_attachment(r: Dict[str, Any], file_url: Optional[str]) -> bool:
    src = (r.get("source") or "").strip().lower()
    if src != "gmail":
        return False
    has_pm_drive = bool((r.get("drive_file_id") or "").strip())
    has_md = md_layer_in_pipeline_meta(r.get("md_content"))
    return not has_pm_drive and not has_md and not raw_row_has_file_backing(file_url)


def _display_filename(extras: Dict[str, Any], raw_id: str) -> str:
    fn = (extras.get("file_name") or "").strip()
    title = (extras.get("title") or "").strip()
    if fn:
        return fn
    if title:
        return title
    rid = str(raw_id)
    return f"(無題・{rid[:8]}…)" if len(rid) > 8 else f"(無題・{rid})"


def _resolved_drive_id(pm: Dict[str, Any], file_url: Optional[str]) -> Optional[str]:
    did = (pm.get("drive_file_id") or "").strip()
    if did:
        return did
    return drive_id_from_file_url(file_url)


def fetch_pending_fast_index_docs(
    db_client: Any,
    raw_tables: Sequence[str],
    *,
    meta_limit: int = 500,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    processing_status != completed の pipeline_meta を対象 raw のみ返す。
    """
    tables = list(raw_tables)
    if not tables:
        return [], None

    try:
        res = (
            db_client.table("pipeline_meta")
            .select(
                "id, raw_id, raw_table, source, person, created_at, processing_status, "
                "drive_file_id, md_content"
            )
            .in_("raw_table", tables)
            .neq("processing_status", "completed")
            .order("created_at", desc=True)
            .limit(meta_limit)
            .execute()
        )
    except Exception as e:
        return [], f"pipeline_meta の取得に失敗しました: {e}"

    rows: List[Dict[str, Any]] = list(res.data or [])
    if not rows:
        return [], None

    by_table: Dict[str, Set[str]] = defaultdict(set)
    for r in rows:
        rt = r.get("raw_table")
        rid = r.get("raw_id")
        if rt and rid:
            by_table[str(rt)].add(str(rid))

    raw_extras_by_pair: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rt, id_set in by_table.items():
        ids = list(id_set)
        chunk_size = 80
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            fetched: Dict[str, Dict[str, Any]] = {}
            try:
                raw_res = (
                    db_client.table(rt)
                    .select("id, file_url, file_name, title, source")
                    .in_("id", chunk)
                    .execute()
                )
                for row in raw_res.data or []:
                    if row.get("id") is not None:
                        fetched[str(row["id"])] = {
                            "file_url": row.get("file_url"),
                            "file_name": row.get("file_name"),
                            "title": row.get("title"),
                            "source": row.get("source"),
                        }
            except Exception:
                fetched = {}
            for rid in chunk:
                raw_extras_by_pair[(rt, rid)] = fetched.get(rid, {})

    ud_by_pair: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rt, id_set in by_table.items():
        ids = list(id_set)
        chunk_size = 80
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            try:
                ud_res = (
                    db_client.table("09_unified_documents")
                    .select("raw_id, raw_table, ui_data, body")
                    .eq("raw_table", rt)
                    .in_("raw_id", chunk)
                    .execute()
                )
                for row in ud_res.data or []:
                    rrid = row.get("raw_id")
                    rrt = row.get("raw_table")
                    if rrid is not None and rrt:
                        ud_by_pair[(str(rrt), str(rrid))] = {
                            "ui_data": row.get("ui_data"),
                            "body": row.get("body"),
                        }
            except Exception:
                pass

    out: List[Dict[str, Any]] = []
    for r in rows:
        rt = r.get("raw_table")
        rid = r.get("raw_id")
        if not rt or not rid:
            continue
        rid_s = str(rid)
        rt_s = str(rt)
        extras = raw_extras_by_pair.get((rt_s, rid_s), {})
        fu = extras.get("file_url")

        has_pm_drive = bool((r.get("drive_file_id") or "").strip())
        has_md_col = md_layer_in_pipeline_meta(r.get("md_content"))
        has_physical_file = has_pm_drive or raw_row_has_file_backing(fu) or bool(
            drive_id_from_file_url(fu)
        )

        if _gmail_without_attachment(r, fu):
            continue

        ud_row = ud_by_pair.get((rt_s, rid_s), {})
        has_structured_09 = structured_in_09(ud_row.get("ui_data"))

        if has_structured_09:
            segment = "structured"
            segment_label = "構造化済"
        elif has_md_col or body_layer_in_09(ud_row):
            segment = "md_done"
            segment_label = "MD済" if has_md_col else "09本文あり"
        elif has_physical_file:
            segment = "pending_md"
            segment_label = "未処理"
        else:
            segment = "text_only"
            segment_label = "テキストのみ"

        src_pm = (r.get("source") or "").strip()
        src_raw = (extras.get("source") or "").strip()
        display_source = src_pm or src_raw or "—"
        display_filename = _display_filename(extras, rid_s)
        drive_id = _resolved_drive_id(r, fu)

        enriched = {
            **r,
            "display_segment": segment,
            "display_segment_label": segment_label,
            "display_source": display_source,
            "display_filename": display_filename,
            "resolved_drive_id": drive_id,
            "has_09_structured": has_structured_09,
        }
        out.append(enriched)

    out.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
    return out, None
