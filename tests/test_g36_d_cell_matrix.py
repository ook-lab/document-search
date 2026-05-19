"""G36-D-MATRIX: D セル確定 → 文字割当 → マス内分割（AI なしテスト）。"""

from __future__ import annotations

from types import SimpleNamespace

from dms.pipeline.stage_g.g36_d_cell_matrix import (
    G36_D_CELL_MATRIX_CONTRACT,
    rebuild_table_from_d_cell_matrix,
)


def _page_with_words():
    words = [
        {"text": "いす", "x0": 10, "x1": 30, "top": 18, "bottom": 26},
        {"text": "出し", "x0": 10, "x1": 30, "top": 34, "bottom": 42},
        {"text": "国語", "x0": 60, "x1": 80, "top": 18, "bottom": 26},
        {"text": "社会", "x0": 60, "x1": 80, "top": 34, "bottom": 42},
    ]

    class _Crop:
        def extract_words(self, **_kw):
            return words

    page = SimpleNamespace(width=100, height=100, crop=lambda _b: _Crop())
    return page


def test_d_cell_matrix_merged_cell_two_interior_lines():
    """rowspan=2 の1マスにいす・出し → 2 D 行に割当（AI オフ）。"""
    cells = [
        {
            "cell_id": "R1C1",
            "row": 1,
            "col": 1,
            "rowspan": 2,
            "colspan": 1,
            "bbox": [0.05, 0.15, 0.45, 0.45],
        },
        {
            "cell_id": "R1C2",
            "row": 1,
            "col": 2,
            "rowspan": 1,
            "colspan": 1,
            "bbox": [0.55, 0.15, 0.95, 0.28],
        },
        {
            "cell_id": "R2C2",
            "row": 2,
            "col": 2,
            "rowspan": 1,
            "colspan": 1,
            "bbox": [0.55, 0.28, 0.95, 0.45],
        },
    ]
    bundle = {
        "available": True,
        "page_size_pt": [100.0, 100.0],
        "tables": [{"table_id": "T1", "bbox": [0.0, 0.1, 1.0, 0.5], "cells": cells}],
        "cells": cells,
    }
    page = _page_with_words()
    res = rebuild_table_from_d_cell_matrix(
        page=page,
        table_bbox=[0.0, 0.1, 1.0, 0.5],
        cell_bundle=bundle,
        table_id="T1",
        header_rows=0,
        use_interior_ai=False,
    )
    assert res is not None
    grid, meta = res
    assert meta["vertical_merge_judge"] == G36_D_CELL_MATRIX_CONTRACT
    assert len(grid) >= 2
    col0_texts = [str(row[0]) for row in grid if row]
    joined = " ".join(col0_texts)
    assert "いす" in joined
    assert "出し" in joined
    assert col0_texts[0] != col0_texts[1] if len(col0_texts) >= 2 else True
