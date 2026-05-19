"""G26 線意味の割当（旧 F50 テストを G26 契約へ移行）。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from dms.pipeline.stage_f.f46_table_line_semantics import _build_assigned_lines
from dms.pipeline.stage_g.g26_line_semantics import G26_LINE_SEMANTICS_CONTRACT, G26SemanticAIError


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


@patch.dict("os.environ", {"GOOGLE_AI_API_KEY": "test-key"}, clear=False)
@patch("google.generativeai.GenerativeModel")
def test_g26_line_semantics_in_digest(mock_model_cls):
    from dms.pipeline.stage_g.g26_semantic_estimator import G26SemanticEstimator

    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "page_summary": "表",
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
                    "table_semantics": {
                        "type": "other",
                        "type_ja": "その他",
                        "target": None,
                        "scope": None,
                        "date_range": None,
                        "confidence": 0.8,
                    },
                    "row_analysis": [
                        {"row_index": 0, "abstraction_level": "category_name", "common_type": "h"},
                        {"row_index": 1, "abstraction_level": "concrete_value", "common_type": "v"},
                    ],
                    "col_analysis": [
                        {"col_index": 0, "abstraction_level": "concrete_value", "common_type": "c"},
                    ],
                    "layout_variant_id": "v_none",
                    "whole_table_intent": "x",
                    "block_summaries": [],
                }
            ],
        }
    )
    mock_resp.usage_metadata = None
    mock_model_cls.return_value.generate_content.return_value = mock_resp

    e14 = [{"table_id": "P0_T0", "sub_tables": [{"sub_table_id": "", "data": [["h"], ["v"]]}]}]
    out, _, _ = G26SemanticEstimator().infer_all(
        e14,
        chain_context={"stage_d_line_digest": _digest_with_lines(), "structured_tables": []},
    )
    lsa = out["line_semantics_ai"]
    assert lsa["line_semantics_contract"] == G26_LINE_SEMANTICS_CONTRACT
    assert lsa["lines"][0]["meaning"] == "上辺"


def test_build_assigned_lines_uses_g26():
    digest = {
        "lines": [{"line_id": "h0"}],
        "line_semantics_ai": {
            "line_semantics_contract": G26_LINE_SEMANTICS_CONTRACT,
            "lines": [
                {
                    "line_id": "h0",
                    "orientation": "horizontal",
                    "x0": 0.1,
                    "y0": 0.2,
                    "x1": 0.8,
                    "y1": 0.2,
                    "role": "internal_row_divider",
                    "meaning": "行区切り",
                    "confidence": 0.85,
                }
            ],
        },
    }
    d_row = {"bbox": [0.0, 0.0, 1.0, 1.0], "table_id": "P0_T0"}
    assigned = _build_assigned_lines(digest, d_row, {})
    ruling = [a for a in assigned if a.get("source") == "g26_page_understanding"]
    assert len(ruling) == 1
    assert ruling[0]["detail"]["meaning"] == "行区切り"


def test_build_assigned_lines_raises_without_g26():
    digest = {"lines": [{"line_id": "h0"}]}
    with pytest.raises(G26SemanticAIError, match="g26_line_semantics_required"):
        _build_assigned_lines(digest, {"bbox": [0, 0, 1, 1]}, {})
