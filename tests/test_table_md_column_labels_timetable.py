"""列分割サブ表の複数行ヘッダから 朝・1〜6 を列ラベルに出す。"""

from dms.pipeline.stage_g.table_md_emitters import resolve_ui_column_labels


def test_col_split_subtable_merges_two_header_rows():
    ui = {
        "rows": [
            ["", "5A", "", "", "", "", "", "", ""],
            ["", "", "朝", "1", "2", "3", "4", "5", "6", ""],
            ["27 （月）", "いす 出し", "国語", "国語", "社会", "算数", "音楽", "理科"],
        ],
        "metadata": {
            "f56_split_axis": "col",
            "header_rows": [0, 1],
            "data_start_row": 2,
        },
    }
    labels = resolve_ui_column_labels(ui)
    assert labels[1] == "5A"
    assert labels[2] == "朝"
    assert labels[3] == "1"
    assert labels[8] == "6"
