"""左縦結合・右行分割表: F51 geometry 判定 + 再構成。"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from unittest.mock import patch

from dms.pipeline.stage_f.f51_lr_vertical_ai_judge import F51_LR_VERTICAL_AI_CONTRACT
from dms.pipeline.stage_f.f51_lr_vertical_orchestrator import run_f51_lr_vertical_on_tables
from dms.pipeline.stage_f.lr_merged_vertical_grid import (
    F51_AI_JUDGE_CONTRACT,
    F51_GEOMETRY_CONTRACT,
    LR_MERGED_VERTICAL_CONTRACT,
    classify_vertical_merge_mode,
    is_lr_merged_vertical_candidate,
    rebuild_lr_merged_vertical_table,
)


def test_candidate_rejects_keyword_only_header():
    """ヘッダー語だけでは候補にならない（5列・geometry 不足）。"""
    grid = [["収入の部", "", "支出の部", "", ""]]
    assert not is_lr_merged_vertical_candidate(None, type("T", (), {"rows": []})(), grid)


def test_lr_rebuild_row_aligned_fixture_pdf():
    pdf = Path(
        r"H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom"
        r"\2025収支報告(新6年).pdf [1eBkcAj5QrAPv1-MW3UtFPGoY6kDYPKgU].pdf"
    )
    if not pdf.is_file():
        pytest.skip("fixture pdf missing")

    with pdfplumber.open(pdf) as doc:
        page = doc.pages[0]
        table = page.find_tables()[0]
        raw = table.extract()
        assert is_lr_merged_vertical_candidate(page, table, raw)
        mode, evidence = classify_vertical_merge_mode(page, table)
        assert mode == "row_aligned"
        assert evidence["left_cluster_count"] >= 2
        judge_meta = {
            "vertical_merge_judge": F51_GEOMETRY_CONTRACT,
            "geometry_evidence": evidence,
        }
        out, meta = rebuild_lr_merged_vertical_table(
            page, table, mode=mode, judge_meta=judge_meta
        )

    tables = [
        {
            "table_id": "P0_B1",
            "source": "stage_b",
            "page": 0,
            "b_plumber_index": 0,
            "bbox": table.bbox,
            "data": raw,
            "metadata": {},
        }
    ]
    with patch(
        "dms.pipeline.stage_f.f51_lr_vertical_orchestrator.judge_lr_vertical_layout_ai",
        return_value={
            "layout_ai_contract": F51_LR_VERTICAL_AI_CONTRACT,
            "layout_kind": "row_aligned",
            "extract_header_row": 0,
            "confidence": 0.9,
            "correspondence_summary": "左縦結合3項目と右①②③が1対1",
            "rationale": "左縦結合セル",
            "vertical_merges": [{"description": "左3行"}],
            "horizontal_merges": [],
            "left_block": None,
            "logical_rows": [
                ["前年度繰越金", "", "①", "", ""],
                ["積立金(72名）", "", "②", "", ""],
                ["③項目", "", "③", "", ""],
            ],
        },
    ):
        run_f51_lr_vertical_on_tables(tables, pdf)

    meta = tables[0]["metadata"]
    assert meta["lr_merged_vertical_contract"] == LR_MERGED_VERTICAL_CONTRACT
    assert meta["vertical_merge_judge"] == F51_AI_JUDGE_CONTRACT
    assert meta["vertical_merge_mode"] == "row_aligned"
    assert meta["lr_rebuilt"] is True
    assert out[1][0] == "前年度繰越金"
    assert "①" in str(out[1][2])
    assert tables[0]["data"][2][0] == "積立金(72名）"
    assert "②" in str(tables[0]["data"][2][2])
    assert "\n" not in str(tables[0]["data"][1][0])
