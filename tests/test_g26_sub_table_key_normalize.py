"""G26: sub_tables key 正規化・layout_split / table_semantics 形修復。"""

import pytest

from dms.pipeline.stage_g.g26_semantic_estimator import (
    G26SemanticEstimator,
    _analysis_skeleton_cols,
    _analysis_skeleton_rows,
    _compose_layout_split_payload,
    _resolve_sub_table_key,
    _sanitize_layout_split,
    _sanitize_table_semantics,
    _validate_table_understanding_entry,
    build_g41_detection_from_entry,
)


def test_compose_rejects_legacy_coords_without_explicit_legacy_env(monkeypatch):
    monkeypatch.delenv("DMS_G26_LEGACY_LAYOUT_SPLIT", raising=False)
    p = {
        "layout_split": {
            "row_split": False,
            "col_split": True,
            "col_blocks": [{"start": 0, "end": 2}, {"start": 3, "end": 4}],
            "whole_table_intent": "x",
            "block_summaries": ["a", "b"],
        }
    }
    with pytest.raises(ValueError, match="layout_variant_id"):
        _compose_layout_split_payload(p, nrows=2, ncols=5)


def test_compose_requires_layout_variant_id(monkeypatch):
    monkeypatch.delenv("DMS_G26_LEGACY_LAYOUT_SPLIT", raising=False)
    with pytest.raises(ValueError, match="layout_variant_id"):
        _compose_layout_split_payload({}, nrows=1, ncols=1)


def test_invalid_layout_variant_id_raises(monkeypatch):
    monkeypatch.delenv("DMS_G26_LEGACY_LAYOUT_SPLIT", raising=False)
    table = [["a", "b"], ["1", "2"]]
    entry = {
        "table_semantics": {
            "type": "other",
            "type_ja": "表",
            "target": None,
            "scope": None,
            "date_range": None,
            "confidence": 0.9,
        },
        "layout_variant_id": "v_col_nope",
        "whole_table_intent": "x",
        "block_summaries": [],
        "row_analysis": _analysis_skeleton_rows(2),
        "col_analysis": _analysis_skeleton_cols(2),
    }
    with pytest.raises(ValueError, match="layout_variant_id"):
        _validate_table_understanding_entry(entry, data=table, key="T::")


def test_compose_accepts_legacy_coords_with_env(monkeypatch):
    monkeypatch.setenv("DMS_G26_LEGACY_LAYOUT_SPLIT", "1")
    p = {
        "layout_split": {
            "row_split": False,
            "col_split": True,
            "col_blocks": [{"start": 0, "end": 2}, {"start": 3, "end": 4}],
            "whole_table_intent": "x",
            "block_summaries": ["a", "b"],
        }
    }
    out = _compose_layout_split_payload(p, nrows=2, ncols=5)
    assert out["col_split"] is True


def test_resolve_sub_table_key_from_table_id():
    specs = [{"key": "P0_B1::", "table_id": "P0_B1", "data": [["a"]], "nrows": 1, "ncols": 1}]
    assert _resolve_sub_table_key({"table_id": "P0_B1"}, specs) == "P0_B1::"
    assert _resolve_sub_table_key({"key": "P0_B1"}, specs) == "P0_B1::"


def test_parse_sub_tables_accepts_table_id_only():
    specs = [
        {
            "key": "P0_B1::",
            "table_id": "P0_B1",
            "data": [["曜日", "5A"], ["27", "国語"]],
            "nrows": 2,
            "ncols": 2,
        }
    ]
    parsed = {
        "sub_tables": [
            {
                "table_id": "P0_B1",
                "table_semantics": {
                    "type": "timetable",
                    "summary": "時間割",
                },
                "row_analysis": [{"row_index": 0}, {"row_index": 1}],
                "col_analysis": [{"col_index": 0}, {"col_index": 1}],
                "layout_variant_id": "v_none",
                "whole_table_intent": "週間時間割",
                "block_summaries": [],
            }
        ]
    }
    by_key = G26SemanticEstimator._parse_sub_tables_response(parsed, specs)
    assert "P0_B1::" in by_key
    assert by_key["P0_B1::"]["table_semantics"]["type_ja"] == "時間割"


def test_sanitize_layout_split_nested_col_blocks():
    """block_summaries の件数がブロック数と不一致 → ValueError（フォールバック禁止）。"""
    raw = {
        "row_split": False,
        "col_split": True,
        "col_blocks": [
            {"col_analysis": 1, "layout_split": {"col_blocks": [{"start": 2, "end": 7}]}},
            {"start": 9, "end": 14},
        ],
        "col_common_left": [0],
        "whole_table_intent": "5Aと5B",
        "block_summaries": ["a", "b", "extra"],  # 3件だがブロックは2件
    }
    with pytest.raises(ValueError, match="block_summaries"):
        _sanitize_layout_split(raw, nrows=7, ncols=15)


def test_sanitize_table_semantics_summary_to_type_ja():
    sem = _sanitize_table_semantics({"type": "roster", "summary": "学級委員"}, key="P0_B2::")
    assert sem["type_ja"] == "学級委員"
    assert sem["type"] == "roster"


def test_malformed_layout_split_rejects_both_axes_true():
    """row_split と col_split を同時に true にした layout_split は ValueError（フォールバック禁止）。"""
    table = [["曜日", "5A", "朝", "1"], ["27", "国語", "国語", "社会"]]
    entry = {
        "layout_split": {
            "row_split": True,
            "col_split": True,
            "col_blocks": [{"start": 1, "end": 2}, {"start": 3, "end": 3}],
            "whole_table_intent": "x",
            "block_summaries": ["a", "b"],
        }
    }
    with pytest.raises(ValueError, match="row_split.*col_split|col_split.*row_split"):
        build_g41_detection_from_entry(entry, table)


def test_repair_clamps_col_blocks_past_grid():
    """閉区間の最大が ncols と一致する（排他的終端と誤認）ケースをクランプで整合させる。"""
    table = [["a", "b", "c", "d"], ["1", "2", "3", "4"]]
    entry = {
        "layout_split": {
            "col_split": True,
            "col_blocks": [{"start": 0, "end": 1}, {"start": 2, "end": 4}],
            "whole_table_intent": "左右",
            "block_summaries": ["左", "右"],
        }
    }
    det = build_g41_detection_from_entry(entry, table)
    assert det["col_split"] is True
    assert det["col_blocks"] == [{"start": 0, "end": 1}, {"start": 2, "end": 3}]


def test_layout_variant_v_col_program_geometry():
    """layout_variant_id はプログラム列挙の座標のみを正とする（AI は数値を書かない）。"""
    table = [["a", "b", "c", "d"], ["1", "2", "3", "4"]]
    entry = {
        "table_semantics": {
            "type": "other",
            "type_ja": "表",
            "target": None,
            "scope": None,
            "date_range": None,
            "confidence": 0.9,
        },
        "layout_variant_id": "v_col_1",
        "whole_table_intent": "左右",
        "block_summaries": ["左", "右"],
        "row_analysis": _analysis_skeleton_rows(2),
        "col_analysis": _analysis_skeleton_cols(4),
    }
    out = _validate_table_understanding_entry(entry, data=table, key="X::")
    det = out["g41_detection"]
    assert det["col_split"] is True
    assert det["col_blocks"] == [{"start": 0, "end": 1}, {"start": 2, "end": 3}]


def test_repair_drops_col_split_when_ncols_at_most_3():
    """ncols <= 3 で col_split → ValueError（フォールバック禁止・表が狭すぎる）。"""
    table = [["a", "b", "c"], ["1", "2", "3"]]
    entry = {
        "layout_split": {
            "col_split": True,
            "col_blocks": [{"start": 0, "end": 0}, {"start": 1, "end": 2}],
            "whole_table_intent": "三列",
            "block_summaries": ["x", "y"],
        }
    }
    with pytest.raises(ValueError, match="ncols=3"):
        build_g41_detection_from_entry(entry, table)
