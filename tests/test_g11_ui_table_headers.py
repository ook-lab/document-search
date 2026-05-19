"""G11 ui_data: 丸数字2列表・LR 表の列見出しと data_start_row。"""

from __future__ import annotations

from dms.pipeline.stage_g.g11_controller import G11Controller
from dms.pipeline.stage_g.g62_table_layout import G62TableLayoutProcessor


def test_headerless_numbered_two_column_table():
    section = [
        ["①学習教材費", "ドリルなど"],
        ["②特別学習活動費", "漢字検定など"],
    ]
    analysis = [
        {
            "table_id": "B_T2",
            "sections": [
                {
                    "data": section,
                    "metadata": {
                        "header_rows": [0],
                        "data_start_row": 1,
                    },
                }
            ],
        }
    ]
    ui = G11Controller()._convert_analyses_to_ui_format(analysis)
    assert len(ui) == 1
    t = ui[0]
    assert t["headers"] == ["項目", "内容"]
    assert t["rows"][0][0].startswith("①")
    assert t["metadata"]["data_start_row"] == 0


def test_g62_skips_circled_row_as_header():
    data = [
        ["\u2460\u9805\u76ee", "\u8aac\u660e"],
        ["\u2461\u9805\u76ee2", "\u8aac\u660e2"],
    ]
    commonality = {
        "row_analysis": [
            {"row_index": 0, "abstraction_level": "category_name", "common_type": "見出し"},
            {"row_index": 1, "abstraction_level": "concrete_value", "common_type": "行"},
        ],
        "col_analysis": [
            {"col_index": 0, "abstraction_level": "category_name", "common_type": "項目"},
            {"col_index": 1, "abstraction_level": "concrete_value", "common_type": "内容"},
        ],
    }
    info = G62TableLayoutProcessor()._detect_headers_from_commonality(data, commonality, 2)
    assert info["header_rows"] == []
    assert info["data_start_row"] == 0
