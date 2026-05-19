"""F-55 AI レイアウト分割（モック）と正規化"""

import json
from unittest.mock import MagicMock, patch

import pytest

from dms.pipeline.stage_f.f55_ai_layout_splitter import (
    F55LayoutAIRequiredError,
    _normalize_detection,
    suggest_ai_table_split,
)
from dms.pipeline.stage_f.f55_repeating_header_detector import F55RepeatingHeaderDetector


def test_normalize_rejects_overlapping_col_blocks():
    raw = {
        "row_split": False,
        "col_split": True,
        "col_blocks": [{"start": 0, "end": 3}, {"start": 2, "end": 5}],
        "col_common_left": [],
        "col_common_right": [],
        "whole_table_intent": "テスト",
        "block_summaries": ["a", "b"],
    }
    with pytest.raises(F55LayoutAIRequiredError, match="overlap"):
        _normalize_detection(raw, nrows=5, ncols=8)


def test_normalize_attaches_semantics_for_valid_col_split():
    raw = {
        "row_split": False,
        "col_split": True,
        "col_blocks": [{"start": 0, "end": 2}, {"start": 3, "end": 5}],
        "col_common_left": [],
        "col_common_right": [],
        "whole_table_intent": " 行事予定  ",
        "block_summaries": [" 4月 ", "5月ブロック"],
    }
    out = _normalize_detection(raw, nrows=4, ncols=12)
    assert out is not None
    assert out["ai_whole_table_intent"] == "行事予定"
    assert out["ai_block_summaries"] == ["4月", "5月ブロック"]


def test_normalize_rejects_missing_block_summaries():
    raw = {
        "row_split": False,
        "col_split": True,
        "col_blocks": [{"start": 0, "end": 1}, {"start": 2, "end": 3}],
        "col_common_left": [],
        "col_common_right": [],
        "whole_table_intent": "x",
        "block_summaries": ["only one"],
    }
    assert _normalize_detection(raw, nrows=5, ncols=8) is None


def test_detect_prefers_ai_when_suggest_returns_value(monkeypatch):
    monkeypatch.setenv("DMS_F55_AI_LAYOUT", "1")

    def _fake_suggest(table, **kwargs):
        return {
            "row_split": False,
            "row_blocks": None,
            "row_common_top": None,
            "row_common_bottom": None,
            "col_split": True,
            "col_blocks": [{"start": 0, "end": 1}, {"start": 2, "end": 3}],
            "col_common_left": [],
            "col_common_right": [],
            "ai_whole_table_intent": "intent",
            "ai_block_summaries": ["A", "B"],
            "layout_ai_contract": "f55_layout_ai_v1",
        }

    monkeypatch.setattr(
        "dms.pipeline.stage_g.g41_repeating_header_detector.suggest_ai_table_split",
        _fake_suggest,
    )

    table = [[0, 1], [2, 3], [4, 5], [6, 7]]
    det = F55RepeatingHeaderDetector().detect(table)
    assert det["col_split"] is True
    assert det["row_split"] is False
    assert det["col_common_left"] == [0]
    assert det["col_blocks"] == [{"start": 1, "end": 1}, {"start": 2, "end": 3}]


@patch.dict("os.environ", {"DMS_F55_AI_LAYOUT": "1", "GOOGLE_AI_API_KEY": "k"}, clear=False)
@patch("google.generativeai.GenerativeModel")
def test_col_split_validation_failure_raises_without_header_derive(mock_model_cls):
    """AI が境界を誤ってもヘッダーからの機械導出で成功させない（再生成は最大1回）。"""
    payload = {
        "row_split": False,
        "col_split": True,
        "col_blocks": [{"start": 0, "end": 2}, {"start": 3, "end": 4}],
        "col_common_left": [],
        "col_common_right": [],
        "whole_table_intent": "二系統の表",
        "block_summaries": ["左", "右"],
    }
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(payload)
    mock_resp.usage_metadata = None
    mock_model_cls.return_value.generate_content.return_value = mock_resp

    table = [
        ["左部", "", "右部", "", ""],
        ["a", "1", "b", "2", "3"],
    ]
    with pytest.raises(F55LayoutAIRequiredError, match="boundary_mismatch"):
        suggest_ai_table_split(table, require=True)
    assert mock_model_cls.return_value.generate_content.call_count == 2


@patch.dict("os.environ", {"DMS_F55_AI_LAYOUT": "1", "GOOGLE_AI_API_KEY": "k"}, clear=False)
@patch("google.generativeai.GenerativeModel")
def test_col_split_forbidden_does_not_retry(mock_model_cls):
    payload = {
        "row_split": False,
        "col_split": True,
        "col_blocks": [{"start": 0, "end": 1}, {"start": 2, "end": 2}],
        "col_common_left": [],
        "col_common_right": [],
        "whole_table_intent": "名簿",
        "block_summaries": ["A", "B"],
    }
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(payload)
    mock_resp.usage_metadata = None
    mock_model_cls.return_value.generate_content.return_value = mock_resp

    table = [["A", "", "B"], ["1", "x", "2"]]
    with pytest.raises(F55LayoutAIRequiredError, match="forbidden"):
        suggest_ai_table_split(table, require=True)
    assert mock_model_cls.return_value.generate_content.call_count == 1
