"""
検索データ準備一覧: 09_unified_documents・09_unified_documents_meta（ステータスのみ）・raw を突き合わせる。

正本の本文は raw（01–05 系）、生成テキストは 09・10。登録済み判定は meta.ix_vectorized_at のみ。
pipeline_meta は参照しない。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from standalone.ud_meta import UD_META_TABLE

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


def pdf_md_in_raw(md_content: Optional[str]) -> bool:
    """raw 行にPDF由来MDが入っているか。"""
    return bool((md_content or "").strip())


def body_layer_in_09(ud_row: Dict[str, Any]) -> bool:
    """09_unified_documents.body にインデックス用の本文があるか。"""
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


def _gmail_without_attachment(
    source: Optional[str],
    file_url: Optional[str],
    pdf_md: Optional[str],
) -> bool:
    if (source or "").strip().lower() != "gmail":
        return False
    has_md = pdf_md_in_raw(pdf_md)
    if has_md:
        return False
    if raw_row_has_file_backing(file_url):
        return False
    if drive_id_from_file_url(file_url):
        return False
    return True


def _display_filename(extras: Dict[str, Any], raw_id: str) -> str:
    fn = (extras.get("file_name") or "").strip()
    title = (extras.get("title") or "").strip()
    if fn:
        return fn
    if title:
        return title
    rid = str(raw_id)
    return f"(無題・{rid[:8]}…)" if len(rid) > 8 else f"(無題・{rid})"


def _resolved_drive_id(file_url: Optional[str]) -> Optional[str]:
    return drive_id_from_file_url(file_url)


def _raw_select_columns(raw_table: str) -> str:
    """08_file_only は created_at / due_date が無い。"""
    common = "id, file_url, file_name, title, source, pdf_md_content, pdf_md_updated_at"
    if raw_table == "08_file_only_01_raw":
        return common
    return f"{common}, created_at, due_date"


def _display_post_at_str(ud: Dict[str, Any], extras: Dict[str, Any]) -> str:
    """一覧の日付列用。投稿・送信に近い時刻のみ（09 indexed_at や meta 更新日は使わない）。"""
    for key in ("post_at", "start_at", "end_at"):
        v = ud.get(key)
        if v:
            return str(v)
    v = extras.get("created_at")
    if v:
        return str(v)
    v = extras.get("due_date")
    if v:
        return str(v)
    v = extras.get("pdf_md_updated_at")
    if v:
        return str(v)
    return ""


def _fetch_meta_ix_map(db_client: Any, doc_ids: Sequence[str]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    chunk_size = 100
    ids = [str(x) for x in doc_ids if x]
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        if not chunk:
            continue
        try:
            mres = (
                db_client.table(UD_META_TABLE)
                .select("doc_id, ix_vectorized_at")
                .in_("doc_id", chunk)
                .execute()
            )
            for row in mres.data or []:
                did = row.get("doc_id")
                if did:
                    out[str(did)] = row.get("ix_vectorized_at")
        except Exception:
            continue
    return out


def fetch_pending_search_data_prep_docs(
    db_client: Any,
    raw_tables: Sequence[str],
    *,
    meta_limit: int = 500,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """ix_vectorized_at が未設定の meta 行（raw 基準）を一覧表示する。"""
    tables = list(raw_tables)
    if not tables:
        return [], None

    try:
        meta_res = (
            db_client.table(UD_META_TABLE)
            .select("raw_table, raw_id, doc_id, ix_vectorized_at, updated_at")
            .in_("raw_table", tables)
            .is_("ix_vectorized_at", "null")
            .order("updated_at", desc=True)
            .limit(meta_limit)
            .execute()
        )
    except Exception as e:
        return [], f"{UD_META_TABLE} の取得に失敗しました: {e}"

    pending_meta = list(meta_res.data or [])
    if not pending_meta:
        return [], None

    by_table: Dict[str, Set[str]] = defaultdict(set)
    for r in pending_meta:
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
                    .select(_raw_select_columns(rt))
                    .in_("id", chunk)
                    .execute()
                )
                for row in raw_res.data or []:
                    if row.get("id") is not None:
                        entry: Dict[str, Any] = {
                            "file_url": row.get("file_url"),
                            "file_name": row.get("file_name"),
                            "title": row.get("title"),
                            "source": row.get("source"),
                            "pdf_md_content": row.get("pdf_md_content"),
                            "pdf_md_updated_at": row.get("pdf_md_updated_at"),
                        }
                        if rt != "08_file_only_01_raw":
                            entry["created_at"] = row.get("created_at")
                            entry["due_date"] = row.get("due_date")
                        fetched[str(row["id"])] = entry
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
                    .select(
                        "id, raw_id, raw_table, source, person, category, title, file_url, "
                        "post_at, start_at, end_at, due_date, ui_data, body"
                    )
                    .eq("raw_table", rt)
                    .in_("raw_id", chunk)
                    .execute()
                )
                for row in ud_res.data or []:
                    rrid = row.get("raw_id")
                    rrt = row.get("raw_table")
                    if rrid is not None and rrt:
                        ud_by_pair[(str(rrt), str(rrid))] = row
            except Exception:
                pass

    out: List[Dict[str, Any]] = []
    for m in pending_meta:
        rt = m.get("raw_table")
        rid = m.get("raw_id")
        if not rt or not rid:
            continue
        rid_s = str(rid)
        rt_s = str(rt)
        extras = raw_extras_by_pair.get((rt_s, rid_s), {})
        fu = extras.get("file_url")
        ud = ud_by_pair.get((rt_s, rid_s), {})

        has_pdf_md = pdf_md_in_raw(extras.get("pdf_md_content"))
        has_physical_file = raw_row_has_file_backing(fu) or bool(drive_id_from_file_url(fu))

        src_ud = (ud.get("source") or "").strip()
        src_raw = (extras.get("source") or "").strip()
        merged_source = src_ud or src_raw

        if _gmail_without_attachment(merged_source, fu, extras.get("pdf_md_content")):
            continue

        has_structured_09 = structured_in_09(ud.get("ui_data"))

        if has_structured_09:
            segment = "structured"
            segment_label = "構造化済"
        elif has_pdf_md or body_layer_in_09(ud):
            segment = "structured"
            segment_label = "構造化済" if has_pdf_md else "09本文あり"
        elif has_physical_file:
            segment = "pending_md"
            segment_label = "未処理"
        else:
            segment = "text_only"
            segment_label = "テキストのみ"

        display_source = merged_source or "—"
        display_filename = _display_filename(extras, rid_s)
        drive_id = _resolved_drive_id(fu)

        display_post_at = _display_post_at_str(ud, extras)
        unified_doc_id = ud.get("id")
        row_id = str(unified_doc_id) if unified_doc_id else f"{rt_s}:{rid_s}"

        enriched = {
            "id": row_id,
            "row_id": row_id,
            "unified_doc_id": str(unified_doc_id) if unified_doc_id else None,
            "raw_id": rid_s,
            "raw_table": rt_s,
            "display_post_at": display_post_at,
            "display_segment": segment,
            "display_segment_label": segment_label,
            "display_source": display_source,
            "display_filename": display_filename,
            "resolved_drive_id": drive_id,
            "has_09_structured": has_structured_09,
            "has_09_doc": bool(unified_doc_id),
        }
        out.append(enriched)

    out.sort(key=lambda x: (x.get("display_post_at") or ""), reverse=True)
    return out, None
