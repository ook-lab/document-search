"""merged_cell_grid の機械展開（収支・使途表）。"""

from dms.pipeline.stage_g.merged_cell_grid import (
    unfold_left_label_amount_space_join,
    unfold_numbered_two_column_list,
)


def test_unfold_income_space_joined():
    grid = [
        ["収入の部", "金額（円）", "支出項目", "支出合計（円）", "一人当たり（円）"],
        [
            "前年度繰越金 積立金(72名） 転入時追加納入",
            "8,538,932 6,048,000 123,000",
            "①学習教材費",
            "1,208,484",
            "16,785",
        ],
    ]
    out = unfold_left_label_amount_space_join(grid)
    assert len(out) == 4
    assert out[1][0] == "前年度繰越金"
    assert out[1][1] == "8,538,932"
    assert out[2][0].startswith("積立金")
    assert out[2][1] == "6,048,000"
    assert out[3][0].startswith("転入")
    assert out[3][1] == "123,000"


def test_unfold_numbered_two_column_list():
    c0 = (
        "\u2460\u5b66\u7fd2\u6559\u6750\u8cbb"
        " \u2461\u7279\u5225\u5b66\u7fd2\u6d3b\u52d5\u8cbb"
        " \u2462\u6821\u5916\u6d3b\u52d5\u8cbb"
        " \u2463\u5b66\u6821\u751f\u6d3b\u7ba1\u7406\u8cbb"
        " \u2464\u5bbf\u6cca\u884c\u4e8b\u8cbb"
    )
    c1 = "a b c d e"
    grid = [[c0, c1]]
    out = unfold_numbered_two_column_list(grid)
    assert len(out) == 5
    assert out[0][0].startswith("\u2460")
    assert out[4][0].startswith("\u2464")
