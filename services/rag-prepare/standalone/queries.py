"""
検索データ準備一覧: 09_unified_documents・09_unified_documents_meta（ステータスのみ）・raw を突き合わせる。

正本の本文は raw（01–05 系）、生成テキストは 09・10。登録済み判定は meta.ix_vectorized_at のみ。
pipeline_meta は参照しない。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from standalone.error_utils import is_gemini_503_error
from standalone.ud_meta import UD_META_TABLE

DRIVE_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")

# Classroom raw のみ course_name 列がある（他テーブルでは SELECT に含めない）。
_CLASSROOM_RAW_WITH_COURSE_NAME = frozenset(
    {
        "03_ema_classroom_01_raw",
        "04_ikuya_classroom_01_raw",
        "05_ikuya_waseaca_01_raw",
    }
)


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
    """09_unified_documents.ui_data に構造化用 JSON があるか。"""
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


def _display_filename(extras: Optional[Dict[str, Any]], _raw_id: str) -> str:
    if not extras:
        return ""
    return (extras.get("file_name") or "").strip()


def _resolved_drive_id(file_url: Optional[str]) -> Optional[str]:
    return drive_id_from_file_url(file_url)


def _raw_select_columns(raw_table: str) -> str:
    """08_file_only は created_at / due_date が無い。Classroom は course_name のみ（検索データ準備一覧用）。"""
    common = "id, file_url, file_name, title, source, pdf_md_content, pdf_md_updated_at"
    if raw_table in _CLASSROOM_RAW_WITH_COURSE_NAME:
        common = (
            "id, file_url, file_name, title, source, course_name, "
            "pdf_md_content, pdf_md_updated_at"
        )
    if raw_table == "08_file_only_01_raw":
        return common
    return f"{common}, created_at, due_date"


def _display_post_at_str(ud: Dict[str, Any], extras: Optional[Dict[str, Any]], raw_table: str) -> str:
    """一覧の日付列用。
    - 09 がある行: 09 の post_at のみ（無ければ空）。
    - 09 が無い行: Classroom 系 raw(03/04/05) の created_at のみ。08 は空。
    """
    has_09 = bool(ud.get("id"))
    if has_09:
        v = ud.get("post_at")
        return str(v) if v else ""
    if extras and raw_table in _CLASSROOM_RAW_WITH_COURSE_NAME:
        v = extras.get("created_at")
        return str(v) if v else ""
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
    meta_limit: Optional[int] = None,
    include_vectorized: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """meta 行を一覧表示する。include_vectorized=True で登録済みも含む。"""
    tables = list(raw_tables)
    if not tables:
        return [], None

    page_size = 1000
    offset = 0
    pending_meta: List[Dict[str, Any]] = []

    try:
        while True:
            q = (
                db_client.table(UD_META_TABLE)
                .select(
                    "raw_table, raw_id, doc_id, ix_vectorized_at, ix_skip_pdf, updated_at, "
                    "ix_vectorize_error, ix_vectorize_error_at, ix_pipeline_error, ix_pipeline_error_at"
                )
                .in_("raw_table", tables)
                .order("updated_at", desc=True)
                .range(offset, offset + page_size - 1)
            )
            if not include_vectorized:
                q = q.is_("ix_vectorized_at", "null")
            meta_res = q.execute()
            rows = meta_res.data or []
            pending_meta.extend(rows)
            if meta_limit is not None and len(pending_meta) >= meta_limit:
                pending_meta = pending_meta[:meta_limit]
                break
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as e:
        return [], f"{UD_META_TABLE} の取得に失敗しました: {e}"

    if not pending_meta:
        return [], None

    by_table: Dict[str, Set[str]] = defaultdict(set)
    for r in pending_meta:
        rt = r.get("raw_table")
        rid = r.get("raw_id")
        if rt and rid:
            by_table[str(rt)].add(str(rid))

    doc_ids_meta: List[str] = []
    for r in pending_meta:
        did = r.get("doc_id")
        if did:
            doc_ids_meta.append(str(did))

    ud_by_id: Dict[str, Dict[str, Any]] = {}
    chunk_size = 80
    for i in range(0, len(doc_ids_meta), chunk_size):
        chunk = doc_ids_meta[i : i + chunk_size]
        if not chunk:
            continue
        try:
            ud_by_doc = (
                db_client.table("09_unified_documents")
                .select(
                    "id, raw_id, raw_table, classification1, person, title, file_url, "
                    "post_at, start_at, end_at, due_date, ui_data, body, classification2, classification3"
                )
                .in_("id", chunk)
                .execute()
            )
        except Exception as e:
            return [], f"09_unified_documents の取得に失敗しました: {e}"

        for row in ud_by_doc.data or []:
            uid = row.get("id")
            if uid is not None:
                ud_by_id[str(uid)] = row

    raw_extras_by_pair: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rt, id_set in by_table.items():
        ids = list(id_set)
        chunk_size = 80
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            try:
                raw_res = (
                    db_client.table(rt)
                    .select(_raw_select_columns(rt))
                    .in_("id", chunk)
                    .execute()
                )
            except Exception as e:
                return [], f"raw テーブル ({rt}) の取得に失敗しました: {e}"

            for row in raw_res.data or []:
                if row.get("id") is not None:
                    entry: Dict[str, Any] = {
                        "file_url": row.get("file_url"),
                        "file_name": row.get("file_name"),
                        "title": row.get("title"),
                        "source": row.get("source"),
                        "category": row.get("category"),
                        "pdf_md_content": row.get("pdf_md_content"),
                        "pdf_md_updated_at": row.get("pdf_md_updated_at"),
                    }
                    if rt in _CLASSROOM_RAW_WITH_COURSE_NAME:
                        entry["course_name"] = row.get("course_name")
                    if rt != "08_file_only_01_raw":
                        entry["created_at"] = row.get("created_at")
                        entry["due_date"] = row.get("due_date")
                    raw_extras_by_pair[(rt, str(row["id"]))] = entry

    uds_by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for rt, id_set in by_table.items():
        ids = list(id_set)
        chunk_size = 80
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            try:
                ud_res = (
                    db_client.table("09_unified_documents")
                    .select(
                        "id, raw_id, raw_table, classification1, person, classification2, classification3, "
                        "title, file_url, post_at, start_at, end_at, due_date, ui_data, body"
                    )
                    .eq("raw_table", rt)
                    .in_("raw_id", chunk)
                    .execute()
                )
            except Exception as e:
                return [], f"09_unified_documents (raw突き合わせ: {rt}) の取得に失敗しました: {e}"

            for row in ud_res.data or []:
                rrid = row.get("raw_id")
                rrt = row.get("raw_table")
                if rrid is not None and rrt:
                    pair_key = (str(rrt), str(rrid))
                    uds_by_pair.setdefault(pair_key, []).append(row)

    out: List[Dict[str, Any]] = []
    for m in pending_meta:
        rt = m.get("raw_table")
        rid = m.get("raw_id")
        if not rt or not rid:
            continue
        rid_s = str(rid)
        rt_s = str(rt)
        extras = raw_extras_by_pair.get((rt_s, rid_s))
        row_error: Optional[str] = None

        if extras is None:
            row_error = f"raw テーブル ({rt_s}) に対応するレコード (id={rid_s}) が存在しません"

        fu = extras.get("file_url") if extras else None
        did_meta = m.get("doc_id")
        ud: Dict[str, Any] = {}

        if did_meta:
            did_str = str(did_meta).strip()
            found = ud_by_id.get(did_str)
            if found:
                ud = found
            else:
                err = f"meta の doc_id ({did_str}) に対応する 09 が存在しません（不整合）"
                row_error = f"{row_error}; {err}" if row_error else err
        else:
            matches = uds_by_pair.get((rt_s, rid_s), [])
            if len(matches) == 1:
                ud = matches[0]
            elif len(matches) > 1:
                dup_ids = ", ".join(str(r.get("id")) for r in matches)
                err = f"同じ (raw_table, raw_id) に対応する 09 レコードが複数存在します（重複: {dup_ids}）"
                row_error = f"{row_error}; {err}" if row_error else err
            else:
                ud = {}

        pdf_md_content = extras.get("pdf_md_content") if extras else None
        has_pdf_md = pdf_md_in_raw(pdf_md_content)
        ix_skip_pdf = bool(m.get("ix_skip_pdf"))
        has_physical_file = (not ix_skip_pdf) and (raw_row_has_file_backing(fu) or bool(drive_id_from_file_url(fu)))

        has_09 = bool(ud.get("id"))
        if has_09:
            display_source = str(ud.get("classification1") or "").strip()
            display_course = str(ud.get("classification2") or "").strip()
            c1 = str(ud.get("classification1") or "").strip()
            c2 = str(ud.get("classification2") or "").strip()
            c3 = str(ud.get("classification3") or "").strip()
        else:
            display_source = str(extras.get("source") or "").strip() if extras else ""
            display_course = (
                str(extras.get("course_name") or "").strip()
                if extras and rt_s in _CLASSROOM_RAW_WITH_COURSE_NAME
                else ""
            )
            c1 = display_source
            c2 = display_course
            c3 = str(extras.get("category") or "").strip() if extras else ""

        if not row_error and _gmail_without_attachment(display_source, fu, pdf_md_content):
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

        display_filename = _display_filename(extras, rid_s)
        drive_id = _resolved_drive_id(fu)

        display_post_at = _display_post_at_str(ud, extras, rt_s)
        unified_doc_id = ud.get("id")
        dom_row_id = f"{rt_s}_{rid_s}"
        row_id = dom_row_id

        ix_vectorized_at = m.get("ix_vectorized_at")
        ix_vectorize_error = m.get("ix_vectorize_error")
        ix_vectorize_error_at = m.get("ix_vectorize_error_at")
        ix_pipeline_error = m.get("ix_pipeline_error")
        ix_pipeline_error_at = m.get("ix_pipeline_error_at")

        if has_09:
            raw_title = ud.get("title")
            doc_title = str(raw_title).strip() if raw_title is not None and str(raw_title).strip() else None
        else:
            raw_title = extras.get("title") if extras else None
            doc_title = str(raw_title).strip() if raw_title is not None and str(raw_title).strip() else None

        enriched = {
            "id": dom_row_id,
            "row_id": dom_row_id,
            "dom_row_id": dom_row_id,
            "unified_doc_id": str(unified_doc_id) if unified_doc_id else None,
            "raw_id": rid_s,
            "raw_table": rt_s,
            "title": doc_title,
            "display_post_at": display_post_at,
            "display_segment": segment,
            "display_segment_label": segment_label,
            "display_source": display_source,
            "display_course_name": display_course,
            "display_filename": display_filename,
            "resolved_drive_id": drive_id,
            "has_09_structured": has_structured_09,
            "has_09_doc": bool(unified_doc_id),
            "is_vectorized": bool(ix_vectorized_at),
            "ix_vectorize_error": ix_vectorize_error,
            "ix_vectorize_error_at": ix_vectorize_error_at,
            "ix_vectorize_error_is_temp": is_gemini_503_error(ix_vectorize_error),
            "ix_pipeline_error": ix_pipeline_error,
            "ix_pipeline_error_at": ix_pipeline_error_at,
            "ix_pipeline_error_is_temp": is_gemini_503_error(ix_pipeline_error),
            "classification1": c1,
            "classification2": c2,
            "classification3": c3,
            "row_error": row_error,
        }
        out.append(enriched)

    out.sort(key=lambda x: (x.get("display_post_at") or ""), reverse=True)
    return out, None


def compute_search_data_prep_counts(docs: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """画面上部の件数表示用内訳を集計する。
    - 全体 (total)
    - 未処理(読み取り待ち) (pending_raw)
    - 構造化済(ベクトル化待ち) (pending_vectorize)
    - 登録済み (vectorized)
    - 失敗あり (has_error)
    """
    total = len(docs)
    pending_raw = 0
    pending_vectorize = 0
    vectorized = 0
    has_error = 0

    for d in docs:
        is_vec = bool(d.get("is_vectorized"))
        seg = d.get("display_segment")
        err = bool(
            d.get("row_error")
            or d.get("ix_vectorize_error")
            or d.get("ix_pipeline_error")
        )

        if err:
            has_error += 1

        if is_vec:
            vectorized += 1
        else:
            if seg == "pending_md":
                pending_raw += 1
            else:
                pending_vectorize += 1

    return {
        "total": total,
        "pending_raw": pending_raw,
        "pending_vectorize": pending_vectorize,
        "vectorized": vectorized,
        "has_error": has_error,
    }

