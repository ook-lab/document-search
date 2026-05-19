"""F-55: 横並び複数月 → レイアウト AI が col_split を返す（ルール単独フォールバック廃止）。"""

import pytest

from dms.pipeline.stage_f.f55_repeating_header_detector import F55RepeatingHeaderDetector


def _mock_ai_col_split(col_blocks, *, common_left=None, common_right=None):
    def _fake(table, **kwargs):
        return {
            "row_split": False,
            "row_blocks": None,
            "row_common_top": None,
            "row_common_bottom": None,
            "col_split": True,
            "col_blocks": col_blocks,
            "col_common_left": common_left or [],
            "col_common_right": common_right or [],
            "ai_whole_table_intent": "行事予定表",
            "ai_block_summaries": ["b"] * len(col_blocks),
            "layout_ai_contract": "f55_layout_ai_v1",
        }

    return _fake


def test_wide_multi_month_col_split_four_blocks(monkeypatch):
    h = [f"Col{i}" for i in range(1, 13)]
    month_row = ["4 月", "", "", "5 月", "", "", "6 月", "", "", "7 月", "", ""]
    day_row = ["日", "曜日", "予定"] * 4
    data = ["1", "水", "", "1", "金", "A", "1", "月", "B", "1", "水", ""]
    table = [h, month_row, day_row, data]

    blocks = [
        {"start": 0, "end": 2},
        {"start": 3, "end": 5},
        {"start": 6, "end": 8},
        {"start": 9, "end": 11},
    ]
    monkeypatch.setattr(
        "dms.pipeline.stage_g.g41_repeating_header_detector.suggest_ai_table_split",
        _mock_ai_col_split(blocks),
    )

    det = F55RepeatingHeaderDetector().detect(table)
    assert det["col_split"] is True
    assert det["row_split"] is False
    # G41: 先頭列ラベルは col_common_left=[0] に昇格し先頭ブロックの start が +1
    assert det["col_common_left"] == [0]
    assert det["col_blocks"] == [
        {"start": 1, "end": 2},
        {"start": 3, "end": 5},
        {"start": 6, "end": 8},
        {"start": 9, "end": 11},
    ]


def test_wide_multi_month_common_left_before_first_month(monkeypatch):
    h = ["#", "4 月", "", "", "5 月", "", "", "6 月", "", ""]
    day_row = ["", "日", "曜日", "予定", "日", "曜日", "予定", "日", "曜日", "予定"]
    data = ["x", "1", "水", "", "1", "金", "", "1", "月", ""]
    table = [h, day_row, data]

    blocks = [
        {"start": 1, "end": 3},
        {"start": 4, "end": 6},
        {"start": 7, "end": 9},
    ]
    monkeypatch.setattr(
        "dms.pipeline.stage_g.g41_repeating_header_detector.suggest_ai_table_split",
        _mock_ai_col_split(blocks, common_left=[0]),
    )

    det = F55RepeatingHeaderDetector().detect(table)
    assert det["col_split"] is True
    assert det["col_common_left"] == [0]
    assert det["col_blocks"][0]["start"] == 1
