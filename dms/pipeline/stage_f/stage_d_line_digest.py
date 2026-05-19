"""Stage D 由来の罫線・格子情報を Stage G（G26 ページ理解）へ渡す。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_TABLE_PAGE_RE = re.compile(r"^P(\d+)_")
_MAX_LINES_PER_ORIENTATION = 200


def _r4(v: float) -> float:
    return round(float(v), 4)


def _serialize_line(line: Dict[str, Any], index: int, orientation: str) -> Dict[str, Any]:
    return {
        "line_id": f"{'h' if orientation == 'horizontal' else 'v'}{index}",
        "orientation": orientation,
        "x0": _r4(line["x0"]),
        "y0": _r4(line["y0"]),
        "x1": _r4(line["x1"]),
        "y1": _r4(line["y1"]),
        "length_norm": _r4(
            max(abs(float(line["x1"]) - float(line["x0"])), abs(float(line["y1"]) - float(line["y0"])))
        ),
        "source": str(line.get("source") or "unified"),
    }


def _line_midpoint(line: Dict[str, Any]) -> Tuple[float, float]:
    return (
        (float(line["x0"]) + float(line["x1"])) / 2.0,
        (float(line["y0"]) + float(line["y1"])) / 2.0,
    )


def _line_intersects_bbox(line: Dict[str, Any], bbox: List[float], margin: float = 0.008) -> bool:
    if not bbox or len(bbox) < 4:
        return False
    x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin
    mx, my = _line_midpoint(line)
    return x0 <= mx <= x1 and y0 <= my <= y1


def build_stage_d_line_digest(stage_d_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    F50 が線の意味付与に使う D サマリ。

    unified_lines の座標（正規化 0–1）を載せる。本数上限超過時は truncated=true。
    """
    if not stage_d_result:
        return {"available": False, "tables": [], "document_grid": None, "lines": []}

    tables_out: List[Dict[str, Any]] = []
    page_idx = stage_d_result.get("page_index")
    for t in stage_d_result.get("tables") or []:
        if not isinstance(t, dict):
            continue
        cell_map = t.get("cell_map") or []
        cell_count = len(cell_map) if isinstance(cell_map, list) else 0
        tid = str(t.get("table_id") or "")
        m = _TABLE_PAGE_RE.match(tid)
        row_page = int(m.group(1)) if m else page_idx
        bbox = t.get("bbox")
        tables_out.append(
            {
                "table_id": tid,
                "origin_uid": str(t.get("origin_uid") or ""),
                "page_index": row_page,
                "source": t.get("source"),
                "bbox": list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else None,
                "cell_count": cell_count,
            }
        )

    dbg = stage_d_result.get("debug") or {}
    grid = dbg.get("grid_result") if isinstance(dbg, dict) else {}
    if not isinstance(grid, dict):
        grid = {}
    unified = grid.get("unified_lines") or {}
    if not isinstance(unified, dict):
        unified = {}

    raw_h = list(unified.get("horizontal") or [])
    raw_v = list(unified.get("vertical") or [])
    truncated = len(raw_h) > _MAX_LINES_PER_ORIENTATION or len(raw_v) > _MAX_LINES_PER_ORIENTATION
    h_src = raw_h[:_MAX_LINES_PER_ORIENTATION]
    v_src = raw_v[:_MAX_LINES_PER_ORIENTATION]

    lines_out: List[Dict[str, Any]] = []
    for i, ln in enumerate(h_src):
        if isinstance(ln, dict) and "x0" in ln:
            lines_out.append(_serialize_line(ln, i, "horizontal"))
    for i, ln in enumerate(v_src):
        if isinstance(ln, dict) and "x0" in ln:
            lines_out.append(_serialize_line(ln, i, "vertical"))

    for tbl in tables_out:
        bb = tbl.get("bbox")
        if bb:
            tbl["line_ids_near"] = [
                ln["line_id"] for ln in lines_out if _line_intersects_bbox(ln, bb)
            ]

    vec = dbg.get("vector_lines") if isinstance(dbg, dict) else {}
    page_size = None
    if isinstance(vec, dict) and vec.get("page_size"):
        ps = vec["page_size"]
        if isinstance(ps, (list, tuple)) and len(ps) >= 2:
            page_size = [float(ps[0]), float(ps[1])]

    inter = len(grid.get("intersections") or [])
    doc_grid: Optional[Dict[str, Any]] = None
    if lines_out or inter:
        doc_grid = {
            "unified_horizontal": len(raw_h),
            "unified_vertical": len(raw_v),
            "intersections": inter,
            "lines_in_digest": len(lines_out),
            "truncated": truncated,
        }

    return {
        "available": bool(tables_out or lines_out),
        "page_index": page_idx,
        "page_size_pt": page_size,
        "tables": tables_out,
        "document_grid": doc_grid,
        "lines": lines_out,
        "lines_truncated": truncated,
    }
