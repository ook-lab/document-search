"""G41 は G26 layout_split のみを採用し、geometry で上書きしない。"""

from dms.pipeline.stage_g.g41_repeating_header_detector import G41RepeatingHeaderDetector


def _g24_bundle(*, col_split: bool) -> dict:
    table = {
        "table_id": "P0_B1",
        "headers": ["曜日", "5A", "朝", "1", "2", "3", "4", "5", "6", "5B", "朝", "1", "2", "3", "4", "5", "6"],
        "rows": [["27", "国語"] + [""] * 14],
    }
    if col_split:
        layout_split = {
            "row_split": False,
            "col_split": True,
            "col_blocks": [{"start": 2, "end": 7}, {"start": 9, "end": 14}],
            "col_common_left": [0],
            "col_common_right": [],
            "whole_table_intent": "5Aと5Bの週間時間割",
            "block_summaries": ["5A", "5B"],
        }
    else:
        layout_split = {
            "row_split": False,
            "col_split": False,
            "whole_table_intent": "週間時間割",
            "block_summaries": [],
        }
    return {
        "structured_tables": [table],
        "semantic_inference": {"by_sub_table": {"P0_B1::": {"layout_split": layout_split}}},
    }


def test_g41_adopts_g26_col_split():
    out = G41RepeatingHeaderDetector().process(_g24_bundle(col_split=True))
    det = out["detections"][0]
    assert det["col_split"] is True
    assert len(det["col_blocks"]) == 2


def test_g41_keeps_g26_single_table():
    out = G41RepeatingHeaderDetector().process(_g24_bundle(col_split=False))
    det = out["detections"][0]
    assert det["col_split"] is False
    assert det["row_split"] is False
