"""F51 結合セル（縦・横）AI 判定。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from dms.pipeline.stage_f.f51_lr_vertical_ai_judge import (
    F51_LR_VERTICAL_AI_CONTRACT,
    F51LRVerticalAIError,
    judge_lr_vertical_layout_ai,
    rebuild_grid_from_ai_correspondence,
)
from dms.pipeline.stage_f.f51_lr_vertical_orchestrator import _apply_f51_ai
from dms.pipeline.stage_f.merged_cell_signals import (
    grid_has_horizontal_merge_placeholders,
    grid_needs_merged_cell_ai,
)


@patch.dict("os.environ", {"GOOGLE_AI_API_KEY": "test-key"}, clear=False)
@patch("google.generativeai.GenerativeModel")
def test_judge_returns_full_resolution(mock_model_cls):
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "layout_kind": "full_resolution",
            "extract_header_row": 0,
            "header_cells": None,
            "confidence": 0.92,
            "correspondence_summary": "列0縦結合、右は行対応",
            "rationale": "縦横を解釈",
            "vertical_merges": [
                {"description": "列0が2行", "anchor_col": 0, "rowspan": 2}
            ],
            "horizontal_merges": [],
            "left_block": None,
            "logical_rows": [
                {"cells": ["A", "1", "R1", "", ""]},
                {"cells": ["B", "2", "R2", "", ""]},
            ],
        }
    )
    mock_resp.usage_metadata = None
    mock_model_cls.return_value.generate_content.return_value = mock_resp

    out = judge_lr_vertical_layout_ai(
        table_preview=[["h"] * 5, ["a\nb", "", "1", "", ""]],
        geometry_evidence={},
    )
    assert out["layout_kind"] == "full_resolution"
    assert len(out["logical_rows"]) == 2
    assert len(out["vertical_merges"]) == 1


def test_rebuild_without_extract_header_row():
    ai = {
        "layout_ai_contract": F51_LR_VERTICAL_AI_CONTRACT,
        "layout_kind": "full_resolution",
        "extract_header_row": None,
        "header_cells": [],
        "logical_rows": [["A", "1"], ["B", "2"]],
        "left_block": None,
        "horizontal_merges": [],
    }
    grid, _merges, layout = rebuild_grid_from_ai_correspondence(
        [["A", "1"], ["B", "2"]], ai
    )
    assert len(grid) == 2
    assert layout["header_rows"] == []
    assert layout["data_start_row"] == 0


def test_rebuild_with_horizontal_merge_metadata():
    ai = {
        "layout_ai_contract": F51_LR_VERTICAL_AI_CONTRACT,
        "layout_kind": "full_resolution",
        "extract_header_row": 0,
        "header_cells": None,
        "logical_rows": [["見出し", "", "右1"]],
        "left_block": None,
        "horizontal_merges": [{"row_index": 1, "spans": [{"start": 0, "colspan": 2}]}],
    }
    grid, merges, _layout = rebuild_grid_from_ai_correspondence(
        [["c0", "c1", "c2"], ["x", "", "y"]], ai
    )
    assert len(grid) == 2
    assert merges[0]["row_index"] == 1
    assert merges[0]["spans"][0]["colspan"] == 2


def test_grid_signals_detect_horizontal():
    grid = [["A", "B", "", "D"], ["1", "2", "3", "4"]]
    assert grid_has_horizontal_merge_placeholders(grid, data_start_row=0)
    assert grid_needs_merged_cell_ai(grid, data_start_row=1)


@patch("dms.pipeline.stage_f.f51_lr_vertical_orchestrator.judge_lr_vertical_layout_ai")
def test_orchestrator_no_merge_skips_rebuild(mock_judge):
    mock_judge.return_value = {
        "layout_ai_contract": F51_LR_VERTICAL_AI_CONTRACT,
        "layout_kind": "no_merge",
        "confidence": 0.88,
        "correspondence_summary": "既に1行1セル",
        "rationale": "結合なし",
        "logical_rows": [],
        "vertical_merges": [],
        "horizontal_merges": [],
    }
    rec = {
        "table_id": "T1",
        "data": [["h"] * 5, ["a", "b", "c", "d", "e"]],
        "metadata": {},
    }
    ok = _apply_f51_ai(rec, rec["data"], document_id=None)
    assert ok is False
    assert rec["metadata"]["vertical_merge_mode"] == "no_merge"
