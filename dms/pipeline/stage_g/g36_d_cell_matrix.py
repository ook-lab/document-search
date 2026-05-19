"""
D 実線セルで行列を確定 → 文字を bbox 割当 → 結合マス内のみ論理行分割。

語彙・表種キーワードは使わない。G36-BD の「extract 行 union」は使わない。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from dms.pipeline.stage_g.g36_cell_interior_ai import (
    G36_CELL_INTERIOR_AI_CONTRACT,
    G36CellInteriorAIError,
    judge_cell_interiors_ai,
)

G36_D_CELL_MATRIX_CONTRACT = "g36_d_cell_matrix_v1"


def _is_norm_coord(v: float) -> bool:
    return -0.5 <= v <= 1.6


def bbox_to_pt(
    bbox: Sequence[float],
    page_w: float,
    page_h: float,
) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if _is_norm_coord(max(abs(x0), abs(y0), abs(x1), abs(y1))):
        return (x0 * page_w, y0 * page_h, x1 * page_w, y1 * page_h)
    return (x0, y0, x1, y1)


def _word_center_norm(w: Dict[str, Any], page_w: float, page_h: float) -> Tuple[float, float]:
    cx = (float(w["x0"]) + float(w["x1"])) / 2.0
    cy = (float(w["top"]) + float(w["bottom"])) / 2.0
    if page_w > 0 and page_h > 0 and not _is_norm_coord(max(cx, cy)):
        return (cx / page_w, cy / page_h)
    return (cx, cy)


def _filter_cells_x_norm(
    cells: List[Dict[str, Any]],
    x_norm_range: Optional[Tuple[float, float]],
) -> List[Dict[str, Any]]:
    if not x_norm_range or len(x_norm_range) < 2:
        return list(cells)
    x0, x1 = float(x_norm_range[0]), float(x_norm_range[1])
    if x0 > x1:
        x0, x1 = x1, x0
    margin = 0.002
    x0 -= margin
    x1 += margin
    out: List[Dict[str, Any]] = []
    for c in cells:
        bb = c.get("bbox")
        if not bb or len(bb) < 4:
            continue
        cx = (float(bb[0]) + float(bb[2])) / 2.0
        if x0 <= cx <= x1:
            out.append(c)
    return out


def _cells_for_table(
    cell_bundle: Dict[str, Any],
    table_id: str,
    table_bbox_norm: Optional[List[float]],
    *,
    x_norm_range: Optional[Tuple[float, float]] = None,
) -> List[Dict[str, Any]]:
    for t in cell_bundle.get("tables") or []:
        if str(t.get("table_id") or "") == str(table_id or ""):
            return list(t.get("cells") or [])
    if table_bbox_norm:
        margin = 0.004
        x0, y0, x1, y1 = table_bbox_norm
        x0 -= margin
        y0 -= margin
        x1 += margin
        y1 += margin
        out = []
        for c in cell_bundle.get("cells") or []:
            bb = c.get("bbox")
            if not bb or len(bb) < 4:
                continue
            if float(bb[2]) < x0 or float(bb[0]) > x1:
                continue
            if float(bb[3]) < y0 or float(bb[1]) > y1:
                continue
            out.append(dict(c))
        return _filter_cells_x_norm(out, x_norm_range)
    return _filter_cells_x_norm(list(cell_bundle.get("cells") or []), x_norm_range)


def _anchor_cells(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged_ids = {
        str(c.get("merged_into"))
        for c in cells
        if c.get("merged_into")
    }
    anchors: List[Dict[str, Any]] = []
    for c in cells:
        cid = str(c.get("cell_id") or "")
        if cid and cid in merged_ids:
            continue
        if c.get("merged_into"):
            continue
        anchors.append(c)
    return anchors


def _occupancy(
    anchors: List[Dict[str, Any]],
) -> Tuple[int, int, Dict[Tuple[int, int], Dict[str, Any]]]:
    max_r = 0
    max_c = 0
    cover: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for cell in anchors:
        r0 = int(cell["row"])
        c0 = int(cell["col"])
        rs = max(1, int(cell.get("rowspan") or 1))
        cs = max(1, int(cell.get("colspan") or 1))
        max_r = max(max_r, r0 + rs - 1)
        max_c = max(max_c, c0 + cs - 1)
        for dr in range(rs):
            for dc in range(cs):
                cover[(r0 + dr, c0 + dc)] = cell
    return max_r, max_c, cover


def _assign_words_to_anchors(
    page: Any,
    table_bbox_pt: Tuple[float, float, float, float],
    anchors: List[Dict[str, Any]],
    page_w: float,
    page_h: float,
) -> Dict[str, List[Dict[str, Any]]]:
    tx0, ty0, tx1, ty1 = table_bbox_pt
    cropped = page.crop((tx0, ty0, tx1, ty1))
    words = cropped.extract_words(x_tolerance=3, y_tolerance=3) or []
    by_id: Dict[str, List[Dict[str, Any]]] = {str(c["cell_id"]): [] for c in anchors}

    for w in words:
        nx, ny = _word_center_norm(w, page_w, page_h)
        best_id: Optional[str] = None
        best_area = -1.0
        for cell in anchors:
            bb = cell.get("bbox") or []
            if len(bb) < 4:
                continue
            x0, y0, x1, y1 = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
            if nx < x0 or nx > x1 or ny < y0 or ny > y1:
                continue
            area = (x1 - x0) * (y1 - y0)
            if area > best_area:
                best_area = area
                best_id = str(cell["cell_id"])
        if best_id:
            by_id.setdefault(best_id, []).append(w)
    return by_id


def _join_words(words: List[Dict[str, Any]]) -> str:
    return " ".join(
        str(w.get("text") or "").strip()
        for w in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
        if str(w.get("text") or "").strip()
    )


def _geometry_interior_lines(words: List[Dict[str, Any]], *, y_tol: float = 4.0) -> List[str]:
    if not words:
        return [""]
    clusters: List[List[Dict[str, Any]]] = []
    for w in sorted(words, key=lambda x: float(x["top"])):
        cy = (float(w["top"]) + float(w["bottom"])) / 2.0
        if not clusters:
            clusters.append([w])
            continue
        prev_cy = sum(
            (float(x["top"]) + float(x["bottom"])) / 2.0 for x in clusters[-1]
        ) / len(clusters[-1])
        if abs(cy - prev_cy) <= y_tol:
            clusters[-1].append(w)
        else:
            clusters.append([w])
    lines = [_join_words(cl) for cl in clusters]
    return [ln for ln in lines if ln] or [""]


def _cell_needs_interior_review(cell: Dict[str, Any], geo_lines: List[str]) -> bool:
    rs = int(cell.get("rowspan") or 1)
    cs = int(cell.get("colspan") or 1)
    if rs > 1 or cs > 1:
        return True
    return len(geo_lines) > 1


def _emit_grid_rows(
    max_r: int,
    max_c: int,
    cover: Dict[Tuple[int, int], Dict[str, Any]],
    cell_lines: Dict[str, List[str]],
) -> List[List[str]]:
    out: List[List[str]] = []
    for r in range(1, max_r + 1):
        k_extra = 1
        for col in range(1, max_c + 1):
            cell = cover.get((r, col))
            if not cell or int(cell["row"]) != r:
                continue
            k_extra = max(k_extra, len(cell_lines.get(str(cell["cell_id"]), [""])))
        for sub in range(k_extra):
            row: List[str] = []
            for col in range(1, max_c + 1):
                cell = cover.get((r, col))
                if not cell:
                    row.append("")
                    continue
                cid = str(cell["cell_id"])
                lines = cell_lines.get(cid) or [""]
                if int(cell["row"]) == r:
                    txt = lines[sub] if sub < len(lines) else ""
                else:
                    idx = r - int(cell["row"])
                    txt = lines[idx] if idx < len(lines) else ""
                row.append(txt)
            out.append(row)
    return out


def rebuild_table_from_d_cell_matrix(
    *,
    page: Any,
    table_bbox: Sequence[float],
    cell_bundle: Dict[str, Any],
    table_id: str = "",
    header_rows: int = 1,
    document_id: Optional[str] = None,
    use_interior_ai: bool = True,
    x_norm_range: Optional[Tuple[float, float]] = None,
) -> Optional[Tuple[List[List[Any]], Dict[str, Any]]]:
    """
    D セル行列を正本に表 grid を構築。失敗時 None。
    """
    if not cell_bundle.get("available"):
        return None

    ps = cell_bundle.get("page_size_pt")
    if isinstance(ps, (list, tuple)) and len(ps) >= 2:
        page_w, page_h = float(ps[0]), float(ps[1])
    else:
        page_w, page_h = float(page.width), float(page.height)

    bb_norm = list(table_bbox) if table_bbox and len(table_bbox) >= 4 else None
    if bb_norm is None:
        return None

    cells = _cells_for_table(cell_bundle, table_id, bb_norm, x_norm_range=x_norm_range)
    anchors = _anchor_cells(cells)
    if not anchors:
        return None

    max_r, max_c, cover = _occupancy(anchors)
    if max_r < 1 or max_c < 1:
        return None

    table_bbox_pt = bbox_to_pt(bb_norm, page_w, page_h)
    words_by_id = _assign_words_to_anchors(page, table_bbox_pt, anchors, page_w, page_h)

    cell_lines: Dict[str, List[str]] = {}
    ai_batch: List[Dict[str, Any]] = []

    for cell in anchors:
        cid = str(cell["cell_id"])
        words = words_by_id.get(cid) or []
        geo = _geometry_interior_lines(words)
        assigned = _join_words(words) or " ".join(geo)
        cell_lines[cid] = geo
        if _cell_needs_interior_review(cell, geo):
            ai_batch.append(
                {
                    "cell_id": cid,
                    "row": cell.get("row"),
                    "col": cell.get("col"),
                    "rowspan": cell.get("rowspan", 1),
                    "colspan": cell.get("colspan", 1),
                    "assigned_text": assigned,
                    "geometry_lines": geo,
                }
            )

    interior_judge = G36_CELL_INTERIOR_AI_CONTRACT
    if ai_batch and use_interior_ai:
        try:
            ai_out = judge_cell_interiors_ai(ai_batch, document_id=document_id)
            for cid, lines in ai_out.items():
                if lines:
                    cell_lines[cid] = lines
        except G36CellInteriorAIError as exc:
            logger.warning(f"[G36-D-MATRIX] interior AI skip: {exc}")
            interior_judge = "g36_cell_interior_geometry_fallback"

    body = _emit_grid_rows(max_r, max_c, cover, cell_lines)
    if not body:
        return None

    dsr = max(0, min(int(header_rows), len(body)))
    head = body[:dsr] if dsr else []
    data_rows = body[dsr:] if dsr else body
    grid = head + data_rows if head else data_rows

    h_merges: List[Dict[str, Any]] = []
    for ri, row in enumerate(grid):
        i = 0
        while i < len(row):
            if not str(row[i] or "").strip():
                i += 1
                continue
            j = i + 1
            while j < len(row) and not str(row[j] or "").strip():
                j += 1
            if j - i > 1:
                h_merges.append({"row_index": ri, "start": i, "colspan": j - i})
            i = j

    meta: Dict[str, Any] = {
        "d_cell_matrix": True,
        "lr_rebuilt": True,
        "vertical_merge_judge": G36_D_CELL_MATRIX_CONTRACT,
        "cell_interior_judge": interior_judge,
        "d_grid_rows": max_r,
        "d_grid_cols": max_c,
        "source_rows": max_r,
        "output_rows": len(grid),
    }
    if h_merges:
        meta["horizontal_merges"] = h_merges

    logger.info(
        f"[G36-D-MATRIX] table_id={table_id} grid={max_r}x{max_c} "
        f"out_rows={len(grid)} interior_ai_cells={len(ai_batch)}"
    )
    return grid, meta


def try_d_cell_matrix_table_rec(
    table_rec: Dict[str, Any],
    page: Any,
    cell_bundle: Dict[str, Any],
    *,
    document_id: Optional[str] = None,
    header_rows: int = 1,
    x_norm_range: Optional[Tuple[float, float]] = None,
) -> bool:
    bbox = table_rec.get("bbox")
    if not bbox and isinstance(table_rec.get("metadata"), dict):
        bbox = (table_rec.get("metadata") or {}).get("bbox")
    if not bbox or len(bbox) < 4:
        return False

    ps = cell_bundle.get("page_size_pt")
    if isinstance(ps, (list, tuple)) and len(ps) >= 2:
        pw, ph = float(ps[0]), float(ps[1])
    else:
        pw, ph = float(page.width), float(page.height)
    if not _is_norm_coord(max(abs(float(x)) for x in bbox[:4])):
        bbox_norm = [
            float(bbox[0]) / pw,
            float(bbox[1]) / ph,
            float(bbox[2]) / pw,
            float(bbox[3]) / ph,
        ]
    else:
        bbox_norm = [float(x) for x in bbox[:4]]

    original_data = table_rec.get("data") or []
    orig_nrows = len(original_data)
    orig_ncols = max((len(r) for r in original_data), default=0)

    res = rebuild_table_from_d_cell_matrix(
        page=page,
        table_bbox=bbox_norm,
        cell_bundle=cell_bundle,
        table_id=str(table_rec.get("table_id") or ""),
        header_rows=header_rows,
        document_id=document_id,
        x_norm_range=x_norm_range,
    )
    if res is None:
        return False
    grid, meta = res
    new_nrows = len(grid)
    new_ncols = max((len(r) for r in grid), default=0)
    if orig_nrows > 0 and orig_ncols > 0:
        row_ratio = new_nrows / orig_nrows
        col_ratio = new_ncols / orig_ncols
        if row_ratio > 1.3 or row_ratio < 0.7 or col_ratio > 1.3 or col_ratio < 0.7:
            logger.warning(
                f"[G36-D-MATRIX] 次元ミスマッチ → スキップ "
                f"table_id={table_rec.get('table_id')} "
                f"rows {orig_nrows}→{new_nrows} cols {orig_ncols}→{new_ncols}"
            )
            return False
    table_rec["data"] = [list(r) for r in grid]
    m = dict(table_rec.get("metadata") or {})
    m.update(meta)
    table_rec["metadata"] = m
    return True


def _parent_bbox_norm_from_structured(
    structured_tables: List[Dict[str, Any]],
    table_id: str,
) -> Optional[List[float]]:
    base = str(table_id or "").split("_S")[0]
    for st in structured_tables:
        tid = str(st.get("table_id") or "")
        if tid in (table_id, base):
            bb = (st.get("metadata") or {}).get("bbox")
            if isinstance(bb, (list, tuple)) and len(bb) >= 4:
                return [float(x) for x in bb[:4]]
    return None


def _x_norm_range_for_col_block(
    parent_bbox_norm: List[float],
    parent_ncols: int,
    col_start: int,
    col_end: int,
    common_left: List[int],
) -> Tuple[float, float]:
    x0, _, x1, _ = parent_bbox_norm
    w = max(x1 - x0, 1e-6)
    ncol = max(int(parent_ncols), 1)
    left = min(common_left) if common_left else 0
    c0 = left + int(col_start)
    c1 = left + int(col_end)
    return (x0 + w * c0 / ncol, x0 + w * (c1 + 1) / ncol)


def apply_d_cell_matrix_to_e14(
    e14_reconstructed: List[Dict[str, Any]],
    pdf_path: Any,
    cell_bundle: Dict[str, Any],
    *,
    structured_tables: Optional[List[Dict[str, Any]]] = None,
    document_id: Optional[str] = None,
) -> int:
    """G44 後の各 sub_table に D セル行列を適用。成功数を返す。"""
    import pdfplumber

    from dms.pipeline.stage_g.g36_lr_vertical_orchestrator import resolve_geometry_pdf_path

    geometry_pdf = resolve_geometry_pdf_path(pdf_path)
    if not geometry_pdf.is_file():
        return 0

    structured_tables = structured_tables or []
    rebuilt = 0
    with pdfplumber.open(geometry_pdf) as doc:
        for entry in e14_reconstructed:
            parent_id = str(entry.get("table_id") or "")
            parent_bb = _parent_bbox_norm_from_structured(structured_tables, parent_id)
            for sub in entry.get("sub_tables") or []:
                data = sub.get("data") or []
                if len(data) < 2:
                    continue
                meta = dict(sub.get("metadata") or {}) if isinstance(sub.get("metadata"), dict) else {}
                page_i = meta.get("page")
                if page_i is None:
                    for st in structured_tables:
                        if str(st.get("table_id") or "") == parent_id:
                            page_i = (st.get("metadata") or {}).get("page")
                            break
                try:
                    page_i = int(page_i or 0)
                except (TypeError, ValueError):
                    page_i = 0
                if page_i < 0 or page_i >= len(doc.pages):
                    continue
                page = doc.pages[page_i]
                bbox_norm = parent_bb
                if not bbox_norm:
                    continue
                x_range: Optional[Tuple[float, float]] = None
                if sub.get("split_axis") == "col" and meta.get("extract_col_start") is not None:
                    try:
                        x_range = _x_norm_range_for_col_block(
                            bbox_norm,
                            int(meta.get("parent_table_col_count") or 0),
                            int(meta["extract_col_start"]),
                            int(meta["extract_col_end"]),
                            list(meta.get("extract_col_common_left") or []),
                        )
                    except (TypeError, ValueError):
                        x_range = None
                hr = meta.get("header_rows")
                if isinstance(hr, list) and hr:
                    header_rows = len(hr)
                else:
                    header_rows = int(meta.get("data_start_row") or 1)
                table_rec = {
                    "table_id": sub.get("sub_table_id") or parent_id,
                    "page": page_i,
                    "bbox": bbox_norm,
                    "data": [list(r) for r in data],
                    "metadata": meta,
                }
                if try_d_cell_matrix_table_rec(
                    table_rec,
                    page,
                    cell_bundle,
                    document_id=document_id,
                    header_rows=header_rows,
                    x_norm_range=x_range,
                ):
                    sub["data"] = table_rec["data"]
                    sub["metadata"] = table_rec.get("metadata") or meta
                    rebuilt += 1
    logger.info(f"[G36-D-MATRIX] e14 sub_tables rebuilt={rebuilt}")
    return rebuilt
