"""G26 ページ理解（罫線 + 表を 1 LLM、layout_split 正本）。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from dms.pipeline.stage_g.g26_line_semantics import (
    G26_LINE_SEMANTICS_CONTRACT,
    G26SemanticAIError,
)
from dms.pipeline.stage_g.g26_semantic_estimator import (
    G26SemanticEstimator,
    build_g41_detection_from_entry,
    normalize_sem_type,
    table_layout_plans_from_by_sub_table,
)


def _digest_with_lines():
    return {
        "available": True,
        "lines_truncated": False,
        "lines": [
            {
                "line_id": "h0",
                "orientation": "horizontal",
                "x0": 0.1,
                "y0": 0.2,
                "x1": 0.8,
                "y1": 0.2,
                "length_norm": 0.7,
            }
        ],
        "tables": [{"table_id": "P0_T0", "bbox": [0.0, 0.0, 1.0, 1.0]}],
    }


def _minimal_sub_table_json():
    return {
        "page_summary": "表1つ",
        "lines": [
            {
                "line_id": "h0",
                "role": "table_outer_border",
                "meaning": "上辺",
                "confidence": 0.9,
            }
        ],
        "sub_tables": [
            {
                "key": "P0_T0::",
                "description": "単表",
                "table_semantics": {
                    "type": "other",
                    "type_ja": "その他",
                    "target": None,
                    "scope": None,
                    "date_range": None,
                    "confidence": 0.8,
                },
                "row_analysis": [
                    {"row_index": 0, "abstraction_level": "category_name", "common_type": "見出し"},
                    {"row_index": 1, "abstraction_level": "concrete_value", "common_type": "値"},
                ],
                "col_analysis": [
                    {"col_index": 0, "abstraction_level": "category_name", "common_type": "項目"},
                    {"col_index": 1, "abstraction_level": "concrete_value", "common_type": "内容"},
                ],
                "layout_variant_id": "v_none",
                "whole_table_intent": "2列の表",
                "block_summaries": [],
            }
        ],
    }


@patch.dict("os.environ", {"GOOGLE_AI_API_KEY": "test-key"}, clear=False)
@patch("google.generativeai.GenerativeModel")
def test_g26_page_unified_single_llm(mock_model_cls):
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(_minimal_sub_table_json())
    mock_resp.usage_metadata = None
    mock_model_cls.return_value.generate_content.return_value = mock_resp

    e14 = [
        {
            "table_id": "P0_T0",
            "sub_tables": [{"sub_table_id": "", "data": [["A", "B"], ["1", "2"]]}],
        }
    ]
    structured = [{"table_id": "P0_T0", "headers": ["A", "B"], "rows": [["1", "2"]]}]

    out, tokens, _ = G26SemanticEstimator().infer_all(
        e14,
        chain_context={"stage_d_line_digest": _digest_with_lines(), "structured_tables": structured},
    )
    assert tokens > 0
    assert mock_model_cls.return_value.generate_content.call_count == 1
    lsa = out["line_semantics_ai"]
    assert lsa["line_semantics_contract"] == G26_LINE_SEMANTICS_CONTRACT
    assert lsa["lines"][0]["meaning"] == "上辺"
    assert "P0_T0::" in out["by_sub_table"]
    assert lsa["table_layout_plans"][0]["split_axis"] == "none"


def test_table_layout_plans_derived_from_layout_split():
    structured = [{"table_id": "T1", "headers": [], "rows": []}]
    by = {
        "T1::": {
            "layout_split": {
                "row_split": True,
                "row_blocks": [{"start": 0, "end": 1}, {"start": 2, "end": 3}],
                "row_common_top": [],
                "row_common_bottom": [],
                "whole_table_intent": "上下2ブロック",
            }
        }
    }
    plans = table_layout_plans_from_by_sub_table(structured, by)
    assert len(plans) == 1
    assert plans[0]["split_axis"] == "row"
    assert plans[0]["reason"] == "上下2ブロック"


def test_build_g41_detection_rejects_invalid_layout_split():
    """閉区間で列ブロックが重なる layout_split はクランプでは直せず失敗する（再試行の契約）。"""
    table = [["曜日", "5A", "朝", "1"], ["27", "国語", "国語", "社会"]]
    entry = {
        "layout_split": {
            "row_split": False,
            "col_split": True,
            "col_blocks": [{"start": 0, "end": 2}, {"start": 2, "end": 3}],
            "col_common_left": [],
            "col_common_right": [],
            "whole_table_intent": "時間割",
            "block_summaries": ["A", "B"],
        }
    }
    with pytest.raises(ValueError, match="layout_split invalid"):
        build_g41_detection_from_entry(entry, table)


def test_build_g41_detection_parallel_class_timetable_col_split():
    """G26 が返す 5A/5B 並列時間割の layout_split を G41 detection に変換できる。"""
    table = [
        ["曜日", "5A", "", "", "", "", "", "", "5B", "", "", "", "", "", ""],
        ["", "朝", "1", "2", "3", "4", "5", "6", "朝", "1", "2", "3", "4", "5", "6"],
        ["27 （月）", "いす", "国語", "国語", "社会", "算数", "音楽", "理科", "社会", "算数", "国語", "国語", "理科", "音楽", ""],
    ]
    entry = {
        "layout_split": {
            "row_split": False,
            "col_split": True,
            "col_blocks": [{"start": 2, "end": 7}, {"start": 9, "end": 14}],
            "col_common_left": [0],
            "col_common_right": [],
            "whole_table_intent": "5Aと5Bの週間時間割を横並びにした表",
            "block_summaries": ["5A組の授業列", "5B組の授業列"],
        }
    }
    det = build_g41_detection_from_entry(entry, table)
    assert det["col_split"] is True
    assert det["col_blocks"] == [{"start": 2, "end": 7}, {"start": 9, "end": 14}]
    assert det["col_common_left"] == [0]


def test_normalize_sem_type_aliases():
    assert normalize_sem_type("financial_report") == "financial_report"
    assert normalize_sem_type("calendar") == "schedule"
    assert normalize_sem_type("timetable") == "timetable"



def test_infer_d_line_semantics_ai_removed():
    from dms.pipeline.stage_g.g25_d_line_semantic_ai import infer_d_line_semantics_ai

    with pytest.raises(G26SemanticAIError, match="removed"):
        infer_d_line_semantics_ai(_digest_with_lines())
