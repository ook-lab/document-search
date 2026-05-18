"""結合セル（空プレースホルダ）の左方向展開と表示用 colspan メタデータ。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

MergeSpan = Dict[str, int]  # {"start": int, "colspan": int}
RowMergeMeta = Dict[str, Any]  # {"row_index": int, "spans": List[MergeSpan]}


def is_merge_placeholder(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return True
        if re.match(r"^列\d+$", s):
            return True
        if re.match(r"^Col\d+$", s, re.I):
            return True
    return False


def resolve_horizontal_merges_in_row(
    row: List[Any],
    *,
    start_col: int = 0,
) -> Tuple[List[Any], List[MergeSpan]]:
    """
    結合セル由来の空セルを左のアンカー値で埋め、colspan 用スパンを返す。

    start_col より左（行ラベル列など）はそのまま。
    """
    out = list(row)
    spans: List[MergeSpan] = []
    i = max(0, start_col)
    n = len(out)
    while i < n:
        if is_merge_placeholder(out[i]):
            i += 1
            continue
        val = out[i]
        j = i + 1
        while j < n and is_merge_placeholder(out[j]):
            j += 1
        span = j - i
        if span > 1:
            spans.append({"start": i, "colspan": span})
            for k in range(i + 1, j):
                out[k] = val
        i = j
    return out, spans


def _none_to_empty(val: Any) -> Any:
    return "" if val is None else val


def normalize_grid_cells(grid: List[List[Any]]) -> List[List[Any]]:
    """Stage B / pdfplumber の None を結合プレースホルダ '' に揃える。"""
    return [[_none_to_empty(c) for c in (row or [])] for row in grid]


def row_has_cell_text(row: Any) -> bool:
    if not isinstance(row, (list, tuple)):
        return False
    return any(str(c).strip() for c in row if c is not None and str(c).strip())


def _expand_one_body_row(row: List[Any]) -> List[List[Any]]:
    """1 extract 行内の改行セルを複数行に展開する。"""
    if not isinstance(row, (list, tuple)):
        return [list(row) if row is not None else []]
    split_cols: Dict[int, List[str]] = {}
    max_lines = 1
    for ci, cell in enumerate(row):
        s = "" if cell is None else str(cell)
        if "\n" not in s:
            continue
        parts = [p.strip() for p in s.split("\n")]
        parts = [p for p in parts if p]
        if len(parts) > 1:
            split_cols[ci] = parts
            max_lines = max(max_lines, len(parts))
    if max_lines <= 1:
        return [[_none_to_empty(c) for c in row]]
    width = max(len(row), max(split_cols.keys(), default=-1) + 1)
    out: List[List[Any]] = []
    for li in range(max_lines):
        new_row: List[Any] = []
        for ci in range(width):
            if ci in split_cols:
                parts = split_cols[ci]
                new_row.append(parts[li] if li < len(parts) else "")
            else:
                val = row[ci] if ci < len(row) else ""
                s = "" if val is None else str(val).strip()
                new_row.append(val if (li == 0 and s) else "")
        out.append(new_row)
    return out


def expand_multiline_cells_in_grid(grid: List[List[Any]]) -> List[List[Any]]:
    """
    pdfplumber 等で1 extract 行にまとまったセル内改行を、複数 extract 行に展開する。

    同一行の複数列に改行があるときは、最も多い行数に揃え、足りない列は先頭行のみ値を残す。
    """
    if not grid:
        return []
    if len(grid) == 1:
        return _expand_one_body_row(list(grid[0]))
    out: List[List[Any]] = [list(grid[0])]
    for row in grid[1:]:
        if not isinstance(row, (list, tuple)):
            out.append(list(row) if row is not None else [])
            continue
        out.extend(_expand_one_body_row(list(row)))
    return out


def prune_blank_body_rows(
    grid: List[List[Any]],
    *,
    data_start_row: int = 1,
) -> List[List[Any]]:
    """データ部の全空行を除去（列分割後の orphan 行など）。"""
    if not grid:
        return []
    dsr = max(0, int(data_start_row))
    if dsr <= 0:
        return [list(r) for r in grid if row_has_cell_text(r)]
    head = [list(r) for r in grid[:dsr]]
    body = [list(r) for r in grid[dsr:] if row_has_cell_text(r)]
    return head + body


def apply_merged_cell_resolution(
    grid: List[List[Any]],
    *,
    data_start_row: int,
    row_label_col: Optional[int],
) -> Tuple[List[List[Any]], List[RowMergeMeta]]:
    """データ行に結合セル解決を適用。ヘッダー行はそのまま。"""
    if not grid:
        return [], []
    dsr = max(0, int(data_start_row))
    if row_label_col is not None:
        start_col = int(row_label_col) + 1
    else:
        start_col = 1 if grid and len(grid[0]) > 1 else 0

    out: List[List[Any]] = []
    merges: List[RowMergeMeta] = []
    for ri, row in enumerate(grid):
        if ri < dsr:
            out.append(list(row))
            continue
        filled, spans = resolve_horizontal_merges_in_row(list(row), start_col=start_col)
        out.append(filled)
        if spans:
            merges.append({"row_index": ri, "spans": spans})
    return out, merges
