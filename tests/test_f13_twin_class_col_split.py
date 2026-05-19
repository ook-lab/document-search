"""F-55: 5A/5B 型二系統並列時間割 → AI 非列分割時 geometry 上書き。"""

from dms.pipeline.stage_f.f55_repeating_header_detector import F55RepeatingHeaderDetector


def test_twin_parallel_header_col_split_like_5a_5b(monkeypatch):
    """同一ヘッダー（時限列）が左右に2つ並び、データ行で左右が食い違う表。"""

    def _fake_ai_col_split(table, **kwargs):
        return {
            "row_split": False,
            "row_blocks": None,
            "row_common_top": None,
            "row_common_bottom": None,
            "col_split": True,
            "col_blocks": [{"start": 1, "end": 7}, {"start": 8, "end": 14}],
            "col_common_left": [0],
            "col_common_right": None,
            "ai_whole_table_intent": "時間割",
            "ai_block_summaries": ["5A", "5B"],
            "layout_ai_contract": "f55_layout_ai_v1",
        }

    monkeypatch.setattr(
        "dms.pipeline.stage_g.g41_repeating_header_detector.suggest_ai_table_split",
        _fake_ai_col_split,
    )

    r0 = ["", "5A", "", "", "", "", "", "", "5B", "", "", "", "", "", ""]
    r1 = ["", "朝", "1", "2", "3", "4", "5", "6", "朝", "1", "2", "3", "4", "5", "6"]
    r2 = ["27", "朝", "A1", "A2", "A3", "A4", "A5", "A6", "朝", "B1", "B2", "B3", "B4", "B5", "B6"]
    r3 = ["28", "朝", "C1", "C2", "C3", "C4", "C5", "C6", "朝", "D1", "D2", "D3", "D4", "D5", "D6"]
    table = [r0, r1, r2, r3]

    det = F55RepeatingHeaderDetector().detect(table)
    assert det["col_split"] is True
    assert det["row_split"] is False
    assert det["col_common_left"] == [0]
    assert det["col_blocks"] == [
        {"start": 1, "end": 7},
        {"start": 8, "end": 14},
    ]
    assert det.get("ai_block_summaries") == ["5A", "5B"]
