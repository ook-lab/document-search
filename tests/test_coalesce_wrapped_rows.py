"""広い表の折り返し行結合（時間割の いす/出し 誤分割）。"""

from dms.pipeline.stage_g.merged_cell_grid import coalesce_wrapped_extract_rows


def test_merge_wrap_propagation_row():
    grid = [
        ["\u9805\u76ee", "5A", "1", "2", "3", "4", "5", "6"],
        ["", "\u671d", "1", "2", "3", "4", "5", "6"],
        ["27", "\u3044\u3059", "\u56fd\u8a9e", "\u56fd\u8a9e", "\u793e\u4f1a", "\u7b97\u6570", "\u97f3\u697d", "\u7406\u79d1"],
        ["\uff08\u6708\uff09", "\u51fa\u3057", "\u51fa\u3057", "\u51fa\u3057", "\u51fa\u3057", "\u51fa\u3057", "\u51fa\u3057", "\u51fa\u3057"],
        ["", "\u671d\u8aad\u66f8", "\u671d\u8aad\u66f8", "\u671d\u8aad\u66f8", "\u671d\u8aad\u66f8", "\u671d\u8aad\u66f8", "\u671d\u8aad\u66f8", "\u671d\u8aad\u66f8"],
    ]
    out = coalesce_wrapped_extract_rows(grid)
    assert len(out) == 4
    assert out[2][1] == "\u3044\u3059\u51fa\u3057"
    assert out[2][2] == "\u56fd\u8a9e"
    assert out[3][1] == "\u671d\u8aad\u66f8"


def test_holiday_row_unchanged():
    grid = [
        ["", "\u671d", "1", "2", "3"],
        ["29", "\u795d\u65e5", "\u795d\u65e5", "\u795d\u65e5", "\u795d\u65e5"],
    ]
    out = coalesce_wrapped_extract_rows(grid, data_start_row=0)
    assert len(out) == 2
    assert out[1][1] == "\u795d\u65e5"
