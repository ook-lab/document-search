"""F55: shared row-label column promotion and narrow-table guard."""

import pytest

from dms.pipeline.stage_f.f55_ai_layout_splitter import (
    F55LayoutAIRequiredError,
    _f55_layout_error_retriable,
    _promote_leading_label_column_to_common_left,
    _validate_col_split_narrow_table,
)


def test_promote_col0_as_common_left():
    table = [
        ["", "5A", "", "", "", "", "", "", "5B", "", "", "", "", "", "", ""],
        ["27", "朝", "1", "2", "3", "4", "5", "6", "朝", "1", "2", "3", "4", "5", "6"],
        ["28", "算", "1", "2", "3", "4", "5", "6", "算", "1", "2", "3", "4", "5", "6"],
    ]
    det = {
        "col_split": True,
        "col_blocks": [{"start": 0, "end": 7}, {"start": 8, "end": 14}],
        "col_common_left": [],
        "col_common_right": [],
    }
    out = _promote_leading_label_column_to_common_left(table, det)
    assert out["col_common_left"] == [0]
    assert out["col_blocks"] == [{"start": 1, "end": 7}, {"start": 8, "end": 14}]


def test_reject_3col_col_split():
    with pytest.raises(F55LayoutAIRequiredError, match="unnecessary"):
        _validate_col_split_narrow_table(3, [{"start": 0, "end": 1}, {"start": 2, "end": 2}])


def test_retriable_vs_structural_errors():
    assert not _f55_layout_error_retriable("f55_col_split_unnecessary: ncols=3")
    assert not _f55_layout_error_retriable("f55_col_split_forbidden: ncols=2")
    assert _f55_layout_error_retriable("f55_col_split_boundary_mismatch: block0 end=2")
    assert _f55_layout_error_retriable("f55_ai_normalize_failed")
    assert _f55_layout_error_retriable("f55_ai_json_parse_failed: Expecting value")


def test_normalize_forbids_col_split_when_ncols_le_3():
    from dms.pipeline.stage_f.f55_ai_layout_splitter import _normalize_detection

    with pytest.raises(F55LayoutAIRequiredError, match="forbidden"):
        _normalize_detection(
            {"row_split": False, "col_split": True, "col_blocks": [{"start": 0, "end": 1}, {"start": 2, "end": 2}]},
            nrows=4,
            ncols=3,
        )
