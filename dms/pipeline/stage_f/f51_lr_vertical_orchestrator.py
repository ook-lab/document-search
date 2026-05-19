"""
F51: 表の内容を AI で読み、縦・横の結合セルを論理行に再構成する。

geometry による分岐・フォールバックなし。pdfplumber は文字抽出の参照のみ。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from dms.pipeline.stage_f.f51_lr_vertical_ai_judge import (
    F51_LR_VERTICAL_AI_CONTRACT,
    F51LRVerticalAIError,
    judge_lr_vertical_layout_ai,
    rebuild_grid_from_ai_correspondence,
)
from dms.pipeline.stage_f.lr_merged_vertical_grid import F51_AI_JUDGE_CONTRACT
from dms.pipeline.stage_f.merged_cell_grid import expand_multiline_cells_in_grid


class F51LRMergedVerticalError(F51LRVerticalAIError):
    pass


_DATA_ROW_START_RE = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩]")


def _preview_lacks_header_row(preview: List[List[Any]]) -> bool:
    """先頭 extract 行が列ラベルではなくデータ項目から始まる表。"""
    if not preview:
        return False
    row = preview[0]
    if not isinstance(row, (list, tuple)) or not row:
        return False
    c0 = "" if row[0] is None else str(row[0]).strip()
    return bool(c0 and _DATA_ROW_START_RE.match(c0))


def _apply_extract_header_row_from_preview(
    preview: List[List[Any]], ai: Dict[str, Any]
) -> Dict[str, Any]:
    """
    先頭 extract 行が丸数字項目から始まる表はヘッダー行が無い（pdfplumber 1行化）。
    AI が extract_header_row=0 を返しても上書きしない（創作見出しは付けない）。
    """
    if not _preview_lacks_header_row(preview):
        return ai
    out = dict(ai)
    out["extract_header_row"] = None
    out["header_cells"] = []
    return out


def _resolve_plumber_table(page: Any, table_rec: Dict[str, Any]) -> Any:
    idx = table_rec.get("b_plumber_index")
    if idx is None:
        idx = (table_rec.get("metadata") or {}).get("b_plumber_index")
    bbox = table_rec.get("bbox")
    tables = page.find_tables() or []
    if idx is not None and 0 <= int(idx) < len(tables):
        return tables[int(idx)]
    if bbox and len(bbox) >= 4:
        tx0, ty0, tx1, ty1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        for t in tables:
            bb = getattr(t, "bbox", None)
            if not bb:
                continue
            ox0, oy0, ox1, oy1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
            if abs(ox0 - tx0) < 2 and abs(oy0 - ty0) < 2 and abs(ox1 - tx1) < 2 and abs(oy1 - ty1) < 2:
                return t
    raise F51LRMergedVerticalError(
        f"f51_plumber_table_not_found: table_id={table_rec.get('table_id')}"
    )


def _apply_f51_ai(
    table_rec: Dict[str, Any],
    raw: List[List[Any]],
    *,
    document_id: Optional[str],
) -> bool:
    preview = expand_multiline_cells_in_grid(raw)
    contract_error: Optional[str] = None
    ai: Optional[Dict[str, Any]] = None
    for attempt in range(3):
        try:
            ai = judge_lr_vertical_layout_ai(
                table_preview=preview,
                geometry_evidence={},
                geometry_hint=None,
                document_id=document_id,
                contract_error=contract_error,
            )
            ai = _apply_extract_header_row_from_preview(preview, ai)
            break
        except F51LRVerticalAIError as exc:
            msg = str(exc)
            if attempt < 2 and ("slash_join" in msg or "slash_joined" in msg):
                contract_error = msg
                ai = None
                continue
            raise
    if ai is None:
        raise F51LRVerticalAIError("f51_ai_missing_result")
    if ai.get("layout_ai_contract") != F51_LR_VERTICAL_AI_CONTRACT:
        raise F51LRVerticalAIError("f51_ai_contract_missing")

    layout_kind = ai["layout_kind"]
    meta = dict(table_rec.get("metadata") or {})
    meta.update(
        {
            "vertical_merge_judge": F51_AI_JUDGE_CONTRACT,
            "vertical_merge_confidence": ai["confidence"],
            "vertical_merge_rationale": ai["rationale"],
            "correspondence_summary": ai.get("correspondence_summary"),
            "vertical_merges_ai": ai.get("vertical_merges"),
        }
    )

    if layout_kind in ("no_vertical_merge", "no_merge"):
        meta["vertical_merge_mode"] = "no_merge"
        meta["lr_rebuilt"] = False
        table_rec["metadata"] = meta
        return False

    grid, h_merges, layout_meta = rebuild_grid_from_ai_correspondence(preview, ai, header_rows=1)
    meta.update(
        {
            "vertical_merge_mode": layout_kind,
            "lr_merged_vertical_contract": "lr_merged_vertical_v2",
            "lr_rebuilt": True,
            "logical_row_count": len(ai.get("logical_rows") or []),
            "source_rows": len(preview),
            "output_rows": len(grid),
            **layout_meta,
        }
    )
    if h_merges:
        meta["horizontal_merges"] = h_merges

    table_rec["data"] = [list(r) for r in grid]
    table_rec["metadata"] = meta
    logger.info(
        f"[F51-AI] rebuilt table_id={table_rec.get('table_id')} mode={layout_kind} "
        f"rows={len(grid)}"
    )
    return True


def run_f51_lr_vertical_on_tables(
    tables: List[Dict[str, Any]],
    pdf_path: str | Path | None = None,
    *,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """structured data 上で F51 AI を実行（pdf_path は互換のため受け取るのみ）。"""
    _ = pdf_path
    rebuilt = 0
    for table_rec in tables:
        meta = dict(table_rec.get("metadata") or {})
        if meta.get("lr_rebuilt"):
            continue

        data = table_rec.get("data") or []
        expanded = expand_multiline_cells_in_grid(data)
        if len(expanded) < 2:
            continue

        if _apply_f51_ai(table_rec, expanded, document_id=document_id):
            rebuilt += 1

    logger.info(f"[F51] 完了: rebuilt={rebuilt} / {len(tables)}")
    return tables


def run_f51_on_structured_tables(
    structured_tables: List[Dict[str, Any]],
    pdf_path: str | Path,
    *,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    work: List[Dict[str, Any]] = []
    for st in structured_tables:
        meta = dict(st.get("metadata") or {})
        headers = st.get("headers") or []
        rows = st.get("rows") or []
        full: List[List[Any]] = []
        if headers:
            full.append(list(headers))
        full.extend([list(r) for r in rows])
        page = meta.get("page")
        if page is None and st.get("source_page") is not None:
            try:
                page = int(st.get("source_page")) - 1
            except (TypeError, ValueError):
                page = st.get("source_page")
        work.append(
            {
                "table_id": st.get("table_id"),
                "page": page,
                "b_plumber_index": meta.get("b_plumber_index", meta.get("index")),
                "bbox": meta.get("bbox"),
                "data": full,
                "metadata": meta,
            }
        )
    run_f51_lr_vertical_on_tables(work, pdf_path, document_id=document_id)
    for st, rec in zip(structured_tables, work):
        meta_out = dict(rec.get("metadata") or {})
        st["metadata"] = meta_out
        if not meta_out.get("lr_rebuilt"):
            continue
        data = rec.get("data") or []
        if data:
            st["headers"] = list(data[0])
            st["rows"] = [list(r) for r in data[1:]]
    return structured_tables
