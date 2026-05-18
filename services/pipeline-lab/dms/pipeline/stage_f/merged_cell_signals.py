"""結合セル（縦・横）の痕跡検出 — F51 AI 対象の判定。"""

from __future__ import annotations

from typing import Any, List

from dms.pipeline.stage_f.lr_merged_vertical_grid import grid_has_unprocessed_left_stack
from dms.pipeline.stage_f.merged_cell_grid import is_merge_placeholder


def _max_cols(grid: List[List[Any]]) -> int:
    return max((len(r) for r in grid if isinstance(r, (list, tuple))), default=0)


def grid_has_multiline_cells(
    grid: List[List[Any]],
    *,
    data_start_row: int = 1,
) -> bool:
    """いずれかのセルに改行（縦結合・折り返しの痕跡）。"""
    dsr = max(0, int(data_start_row))
    for row in grid[dsr:]:
        if not isinstance(row, (list, tuple)):
            continue
        for cell in row:
            if isinstance(cell, str) and "\n" in cell and cell.strip():
                return True
    return False


def grid_has_horizontal_merge_placeholders(
    grid: List[List[Any]],
    *,
    data_start_row: int = 0,
) -> bool:
    """非空セルの直右に結合プレースホルダが続く（横結合の痕跡）。"""
    dsr = max(0, int(data_start_row))
    for row in grid[dsr:]:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        i = 0
        n = len(row)
        while i < n:
            if is_merge_placeholder(row[i]):
                i += 1
                continue
            j = i + 1
            while j < n and is_merge_placeholder(row[j]):
                j += 1
            if j - i > 1:
                return True
            i = j
    return False


def grid_needs_merged_cell_ai(
    grid: List[List[Any]],
    *,
    data_start_row: int = 1,
) -> bool:
    """縦・横いずれかの結合セル再構成が必要そうな表。"""
    if not grid or _max_cols(grid) < 2:
        return False
    if grid_has_unprocessed_left_stack(grid, data_start_row=data_start_row):
        return True
    if grid_has_multiline_cells(grid, data_start_row=data_start_row):
        return True
    if grid_has_horizontal_merge_placeholders(grid, data_start_row=0):
        return True
    return False
