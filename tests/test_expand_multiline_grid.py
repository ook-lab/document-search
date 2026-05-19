from dms.pipeline.stage_f.merged_cell_grid import expand_multiline_cells_in_grid


def test_expand_single_row_usage_table():
    grid = [
        [
            "①a\n②b",
            "A\nB",
        ],
    ]
    out = expand_multiline_cells_in_grid(grid)
    assert len(out) == 2
    assert out[0][0] == "①a"
    assert out[1][0] == "②b"


def test_expand_income_row_splits_three_lines():
    grid = [
        ["収入の部", "", "支出の部", "", ""],
        ["前年度繰越金\n積立金\n転入", "8\n6\n1", "①", "100", "10"],
        ["", "", "②", "200", "20"],
    ]
    out = expand_multiline_cells_in_grid(grid)
    assert len(out) == 5
    assert out[1][0] == "前年度繰越金"
    assert out[2][0] == "積立金"
    assert out[3][0] == "転入"
    assert out[1][2] == "①"
    assert out[2][2] == ""
