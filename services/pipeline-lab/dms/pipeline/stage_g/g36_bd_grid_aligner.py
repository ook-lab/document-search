"""
B extract 行と Stage D 罫線を合成し、列ごとの欠損横線を反映した論理行に組み直す。

語彙・表種キーワードは使わない。pdfplumber セル bbox と D horizontal の有無のみ。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from dms.pipeline.stage_g.g36_lr_merged_vertical_grid import _column_x_bounds

G36_BD_GRID_CONTRACT = "g36_bd_d_hline_row_union_v1"


def _norm_to_pt(v: float, page_size: float) -> float:
    if page_size <= 0:
        return v
    if -0.5 <= v <= 1.6:
        return v * page_size
    return v


def _line_y_pt(line: Dict[str, Any], page_h: float) -> float:
    y0 = float(line.get("y0", 0))
    y1 = float(line.get("y1", 0))
    mid = (y0 + y1) / 2.0
    return _norm_to_pt(mid, page_h)


def _hline_blocks_column_pt(
    h_lines_pt: Sequence[Dict[str, Any]],
    x0: float,
    x1: float,
    y_boundary_pt: float,
    *,
    y_tol: float = 2.5,
    min_overlap_ratio: float = 0.35,
) -> bool:
    col_w = max(x1 - x0, 1.0)
    for ln in h_lines_pt:
        ly = (float(ln["y0"]) + float(ln["y1"])) / 2.0
        if abs(ly - y_boundary_pt) > y_tol:
            continue
        overlap = max(0.0, min(x1, float(ln["x1"])) - max(x0, float(ln["x0"])))
        if overlap / col_w >= min_overlap_ratio:
            return True
    return False


def _h_lines_to_pt(
    h_lines: Sequence[Dict[str, Any]],
    page_w: float,
    page_h: float,
) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    for ln in h_lines:
        if not isinstance(ln, dict):
            continue
        x0 = _norm_to_pt(float(ln.get("x0", 0)), page_w)
        x1 = _norm_to_pt(float(ln.get("x1", 0)), page_w)
        y0 = _norm_to_pt(float(ln.get("y0", 0)), page_h)
        y1 = _norm_to_pt(float(ln.get("y1", 0)), page_h)
        out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
    return out


def _cell_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("\r", "").replace("\n", " ").strip()


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[rj] = ri

    def groups(self) -> Dict[int, List[int]]:
        g: Dict[int, List[int]] = {}
        for i in range(len(self.parent)):
            r = self.find(i)
            g.setdefault(r, []).append(i)
        return g


def _plumber_row_boundary_y(
    plumber_rows: Sequence[Any],
    row_index: int,
    col: int,
) -> Optional[float]:
    """row_index 行の下端（次行との境界）を列 col のセル bbox から。"""
    if row_index < 0 or row_index >= len(plumber_rows) - 1:
        return None
    pr = plumber_rows[row_index]
    cells = getattr(pr, "cells", None) or []
    if col < len(cells) and cells[col]:
        return float(cells[col][3])
    nxt = plumber_rows[row_index + 1]
    ncells = getattr(nxt, "cells", None) or []
    if col < len(ncells) and ncells[col]:
        return float(ncells[col][1])
    bb = getattr(pr, "bbox", None)
    if bb:
        return float(bb[3])
    return None


def _merge_row_groups(
    n_rows: int,
    header_rows: int,
    x_bounds: Sequence[Tuple[float, float]],
    plumber_rows: Sequence[Any],
    h_lines_pt: Sequence[Dict[str, float]],
) -> List[List[int]]:
    """データ行の plumber 行インデックスを列ごとの D 罫線欠損で union。"""
    uf = _UnionFind(n_rows)
    for ri in range(header_rows, n_rows - 1):
        for col, (x0, x1) in enumerate(x_bounds):
            y_b = _plumber_row_boundary_y(plumber_rows, ri, col)
            if y_b is None:
                continue
            if not _hline_blocks_column_pt(h_lines_pt, x0, x1, y_b):
                uf.union(ri, ri + 1)
    groups = uf.groups()
    ordered: List[List[int]] = []
    for root in sorted(groups.keys()):
        idxs = sorted(groups[root])
        if idxs[0] < header_rows:
            continue
        ordered.append(idxs)
    ordered.sort(key=lambda g: g[0])
    return ordered


def _join_cell_texts(parts: List[str]) -> str:
    seen: List[str] = []
    for p in parts:
        t = _cell_str(p)
        if not t:
            continue
        if t in seen:
            continue
        seen.append(t)
    return " ".join(seen)


def align_extract_grid_with_d_lines(
    *,
    grid: List[List[Any]],
    plumber_table: Any,
    page: Any,
    cell_bundle: Dict[str, Any],
    header_rows: int = 1,
) -> Optional[Tuple[List[List[Any]], Dict[str, Any]]]:
    """
    B extract を D 罫線＋plumber セル bbox で論理行に再構成。

    失敗時は None（呼び出し側は従来経路）。
    """
    if not grid or len(grid) < header_rows + 2:
        return None
    if not cell_bundle.get("available"):
        return None

    plumber_rows = getattr(plumber_table, "rows", None) or []
    if len(plumber_rows) < len(grid):
        return None

    ps = cell_bundle.get("page_size_pt")
    if not isinstance(ps, (list, tuple)) or len(ps) < 2:
        ps = [float(page.width), float(page.height)]
    page_w, page_h = float(ps[0]), float(ps[1])

    unified = cell_bundle.get("unified_lines") or {}
    h_raw = list(unified.get("horizontal") or [])
    if not h_raw:
        return None

    h_lines_pt = _h_lines_to_pt(h_raw, page_w, page_h)
    x_bounds = _column_x_bounds(plumber_rows)
    if len(x_bounds) < 2:
        return None

    ncols = max(len(r) for r in grid if isinstance(r, (list, tuple)))
    if ncols < 5:
        return None

    n_rows = len(grid)
    body_groups = _merge_row_groups(n_rows, header_rows, x_bounds, plumber_rows, h_lines_pt)
    if not body_groups:
        return None

    head = [list(grid[i]) for i in range(header_rows)]
    out_body: List[List[Any]] = []

    for group in body_groups:
        row_out: List[Any] = []
        for col in range(ncols):
            texts: List[str] = []
            for ri in group:
                if ri < len(grid) and col < len(grid[ri]):
                    texts.append(_cell_str(grid[ri][col]))
            row_out.append(_join_cell_texts(texts))
        out_body.append(row_out)

    if len(out_body) >= n_rows - header_rows:
        return None

    merged = head + out_body
    meta = {
        "bd_grid_aligned": True,
        "vertical_merge_judge": G36_BD_GRID_CONTRACT,
        "lr_rebuilt": True,
        "source_rows": len(grid),
        "output_rows": len(merged),
        "bd_row_groups": body_groups,
        "b_extract_rows_preserved": [list(r) for r in grid],
    }
    logger.info(
        f"[G36-BD] rows {len(grid)}->{len(merged)} "
        f"groups={len(body_groups)} table_bbox={getattr(plumber_table, 'bbox', None)}"
    )
    return merged, meta


def try_bd_align_table_rec(
    table_rec: Dict[str, Any],
    page: Any,
    plumber_table: Any,
    cell_bundle: Dict[str, Any],
    *,
    header_rows: int = 1,
) -> bool:
    data = table_rec.get("data") or []
    if not data:
        return False
    res = align_extract_grid_with_d_lines(
        grid=data,
        plumber_table=plumber_table,
        page=page,
        cell_bundle=cell_bundle,
        header_rows=header_rows,
    )
    if res is None:
        return False
    merged, meta = res
    table_rec["data"] = merged
    m = dict(table_rec.get("metadata") or {})
    m.update(meta)
    table_rec["metadata"] = m
    return True
