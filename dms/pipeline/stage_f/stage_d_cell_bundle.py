"""Stage D 格子・罫線・セルを Stage G まで欠損なく渡す（プロンプト打ち切りとは別経路）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dms.pipeline.stage_d.d9_cell_identifier import D9CellIdentifier


def _cells_in_bbox(cells: List[Dict[str, Any]], bbox: List[float], margin: float = 0.004) -> List[Dict[str, Any]]:
    if not bbox or len(bbox) < 4:
        return list(cells)
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin
    out: List[Dict[str, Any]] = []
    for c in cells:
        bb = c.get("bbox")
        if not bb or len(bb) < 4:
            continue
        cx0, cy0, cx1, cy1 = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
        if cx1 < x0 or cx0 > x1 or cy1 < y0 or cy0 > y1:
            continue
        out.append(dict(c))
    return out


def build_stage_d_cell_bundle(stage_d_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    G36 B+D 行再構成用。digest の打ち切りとは独立に grid / lines / cells を載せる。
    """
    if not stage_d_result:
        return {"available": False}

    dbg = stage_d_result.get("debug") or {}
    grid_result = dbg.get("grid_result") if isinstance(dbg.get("grid_result"), dict) else {}
    cell_result = dbg.get("cell_result") if isinstance(dbg.get("cell_result"), dict) else {}
    unified = grid_result.get("unified_lines") or {}
    if not isinstance(unified, dict):
        unified = {}

    raw_cells = list(cell_result.get("cells") or [])
    h_lines = list(unified.get("horizontal") or [])
    v_lines = list(unified.get("vertical") or [])
    if raw_cells and h_lines:
        raw_cells = D9CellIdentifier()._detect_merged_cells(raw_cells, h_lines, v_lines)

    grid_info = dict(cell_result.get("grid_info") or {})
    page_idx = stage_d_result.get("page_index")

    vec = dbg.get("vector_lines") if isinstance(dbg.get("vector_lines"), dict) else {}
    page_size_pt: Optional[List[float]] = None
    if isinstance(vec, dict) and vec.get("page_size"):
        ps = vec["page_size"]
        if isinstance(ps, (list, tuple)) and len(ps) >= 2:
            page_size_pt = [float(ps[0]), float(ps[1])]

    tables_out: List[Dict[str, Any]] = []
    for t in stage_d_result.get("tables") or []:
        if not isinstance(t, dict):
            continue
        bbox = t.get("bbox")
        bb = list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else None
        tables_out.append(
            {
                "table_id": str(t.get("table_id") or ""),
                "origin_uid": str(t.get("origin_uid") or ""),
                "bbox": bb,
                "cells": _cells_in_bbox(raw_cells, bb) if bb else [],
            }
        )

    return {
        "available": bool(raw_cells or h_lines or v_lines or tables_out),
        "page_index": page_idx,
        "page_size_pt": page_size_pt,
        "grid_info": grid_info,
        "cells": raw_cells,
        "unified_lines": {
            "horizontal": h_lines,
            "vertical": v_lines,
        },
        "tables": tables_out,
    }
