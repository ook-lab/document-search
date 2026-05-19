"""G36-BD: D 罫線欠損で plumber 行を union（列パターン coalesce は使わない）。"""

from __future__ import annotations

from types import SimpleNamespace

from dms.pipeline.stage_g.g36_bd_grid_aligner import (
    G36_BD_GRID_CONTRACT,
    _merge_row_groups,
    align_extract_grid_with_d_lines,
)


def _mock_plumber_rows(cells_per_row):
    rows = []
    for cells in cells_per_row:
        rows.append(SimpleNamespace(cells=cells, bbox=cells[0] if cells else None))
    return rows


def test_merge_row_groups_unions_when_no_hline_in_column():
    """列1に横線が無い境界では行を union。"""
    cells = [
        [(0, 10, 20, 30), (20, 10, 40, 30)],
        [(0, 30, 20, 50), (20, 30, 40, 50)],
        [(0, 50, 20, 70), (20, 50, 40, 70)],
    ]
    plumber_rows = _mock_plumber_rows(cells)
    x_bounds = [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0), (60.0, 80.0), (80.0, 100.0)]
    h_lines = [{"x0": 0, "x1": 20, "y0": 30, "y1": 30}]
    groups = _merge_row_groups(
        n_rows=3,
        header_rows=0,
        x_bounds=x_bounds,
        plumber_rows=plumber_rows,
        h_lines_pt=h_lines,
    )
    assert any(len(g) >= 2 for g in groups)


def test_align_merges_isu_dashi_rows():
    """左列に線・列1に線無し → いすと出しが1行に。"""
    grid = [
        ["27", "いす", "国語", "社会", "算数", "音楽", "理科"],
        ["（月）", "出し", "国語", "道徳", "理科", "音楽", "国語"],
    ]
    cells = [
        [(0, 10, 15, 30), (15, 10, 35, 30), (35, 10, 50, 30), (50, 10, 65, 30), (65, 10, 80, 30), (80, 10, 95, 30), (95, 10, 110, 30)],
        [(0, 30, 15, 50), (15, 30, 35, 50), (35, 30, 50, 50), (50, 30, 65, 50), (65, 30, 80, 50), (80, 30, 95, 50), (95, 30, 110, 50)],
    ]
    plumber_rows = _mock_plumber_rows(cells)
    table = SimpleNamespace(rows=plumber_rows, bbox=(0, 10, 110, 50))
    page = SimpleNamespace(width=612, height=792)
    h_lines = [{"x0": 0, "x1": 15, "y0": 30, "y1": 30}]
    bundle = {
        "available": True,
        "page_size_pt": [612.0, 792.0],
        "unified_lines": {"horizontal": h_lines, "vertical": []},
    }
    res = align_extract_grid_with_d_lines(
        grid=grid,
        plumber_table=table,
        page=page,
        cell_bundle=bundle,
        header_rows=0,
    )
    assert res is not None
    merged, meta = res
    assert meta["vertical_merge_judge"] == G36_BD_GRID_CONTRACT
    assert len(merged) == 1
    assert "いす" in str(merged[0][1]) and "出し" in str(merged[0][1])
