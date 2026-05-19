"""F50→分割: AI table_layout_plans に従う物理分割。"""

from dms.pipeline.stage_g.g26_line_semantics import (
    G26_LINE_SEMANTICS_CONTRACT,
    plan_to_g44_detection as plan_to_f56_detection,
)
from dms.pipeline.stage_f.f50_d_line_split import (
    F50_D_LINE_SPLIT_CONTRACT,
    apply_d_line_split_structured_tables,
)


def test_plan_to_f56_row_split():
    det = plan_to_f56_detection(
        {
            "split_axis": "row",
            "row_blocks": [{"start": 0, "end": 1}, {"start": 2, "end": 3}],
            "row_common_top": [0],
            "row_common_bottom": [],
            "reason": "test",
        }
    )
    assert det["row_split"] is True
    assert det["row_blocks"] == [{"start": 0, "end": 1}, {"start": 2, "end": 3}]


def test_apply_split_from_ai_plans():
    digest = {
        "line_semantics_ai": {
            "line_semantics_contract": G26_LINE_SEMANTICS_CONTRACT,
            "table_layout_plans": [
                {
                    "table_index": 0,
                    "split_axis": "row",
                    "row_blocks": [{"start": 1, "end": 2}, {"start": 3, "end": 3}],
                    "row_common_top": [0],
                    "row_common_bottom": [],
                    "reason": "block boundary",
                }
            ],
        },
    }
    structured = [
        {
            "table_id": "P0_T0",
            "headers": ["A", "B"],
            "rows": [["1", "2"], ["3", "4"], ["5", "6"]],
        }
    ]
    out, meta = apply_d_line_split_structured_tables(structured, digest)
    assert meta["contract"] == F50_D_LINE_SPLIT_CONTRACT
    assert len(out) == 2
    assert out[0]["table_id"].endswith("_F1")
    assert out[0]["metadata"]["split_source"] == F50_D_LINE_SPLIT_CONTRACT
