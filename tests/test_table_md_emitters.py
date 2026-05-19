"""Regression tests for MD/HTML/YAML table embed helpers (F60 → ui_data.tables_md_embed)."""

from __future__ import annotations

from dms.pipeline.stage_f.table_md_emitters import (
    build_table_html_for_md,
    build_tables_markdown_embed,
    infer_column_labels,
    infer_month_column_groups,
    table_yaml_record,
)


def test_infer_month_column_groups_sparse_month_row():
    header = ["4月", "", "", "5月", "", "日", ""]
    groups = infer_month_column_groups(header)
    assert len(groups) == 2
    assert groups[0]["label"] == "4月" and groups[0]["start"] == 0 and groups[0]["colspan"] == 3
    assert groups[1]["label"] == "5月" and groups[1]["start"] == 3 and groups[1]["colspan"] == 4


def test_build_table_html_month_colspan_two_header_rows():
    ui = {
        "table_id": "t1",
        "rows": [
            ["4月", "", "5月", ""],
            ["日", "曜", "日", "曜"],
            ["1", "月", "2", "火"],
        ],
        "metadata": {"header_rows": [0, 1], "table_semantics": {"role": "calendar"}},
    }
    html = build_table_html_for_md(ui)
    assert 'class="md-embed-table"' in html
    assert 'colspan="2"' in html
    assert "4月" in html and "5月" in html


def test_build_tables_markdown_embed_yaml_and_html():
    ui = {
        "table_id": "tbl_a",
        "description": "Demo",
        "rows": [["A", "B"], ["1", "2"]],
        "metadata": {},
    }
    md = build_tables_markdown_embed([ui])
    assert "<!-- dms:tables-md-embed v1 -->" in md
    assert "```yaml" in md
    assert "tables:" in md
    assert "<!-- table:tbl_a -->" in md
    assert "md-embed-table" in md


def test_table_yaml_record_string_header_rows():
    ui = {
        "table_id": "x",
        "rows": [["4月", "5月"], ["日", "日"], ["x", "y"]],
        "metadata": {"header_rows": ["0", "1"]},
    }
    rec = table_yaml_record(ui)
    assert rec["header_row_indices"] == [0, 1]
    assert "month_blocks" in rec and "data_rows" in rec


def test_table_yaml_record_all_rows_marked_header_still_has_data_rows():
    ui = {
        "table_id": "P0_B2",
        "rows": [
            ["6年A組", "", "6年B組"],
            ["関口 葵", "学級委員長", "林 桃那"],
            ["上條 真由", "副学級委員長", "山﨑 夢奏"],
            ["蒲池 直仁", "学級委員", "河内 晴琳"],
        ],
        "metadata": {"header_rows": [0, 1, 2, 3], "table_semantics": {"type": "roster"}},
    }
    rec = table_yaml_record(ui)
    assert rec["header_row_indices"] == [0]
    assert len(rec["data_rows"]) == 3
    assert rec["data_rows"][0]["cells"][0] == "関口 葵"


def test_build_table_html_roster_uses_td_for_body():
    ui = {
        "table_id": "r",
        "rows": [
            ["A組", "役職", "B組"],
            ["x", "委員長", "y"],
        ],
        "metadata": {"header_rows": [0, 1]},
    }
    html = build_table_html_for_md(ui)
    assert "<td>" in html
    assert html.count("<th>") >= 2


def test_infer_column_labels_merges_header_rows():
    ui = {
        "rows": [
            ["", "5A", "", "", "", "", "", ""],
            ["", "朝", "1", "2", "3", "4", "5", "6"],
            ["27（月）", "朝読", "国語", "国語", "社会", "算数", "音楽", "理科"],
        ],
        "metadata": {"header_rows": [0, 1]},
    }
    labs = infer_column_labels(ui)
    assert len(labs) == 8
    assert "5A" in labs[1] or str(labs[1]).startswith("5A")


def test_timetable_merged_period_cells_colspan_and_yaml_fill():
    """11日 1〜4時限の横結合: 空プレースホルダを展開し HTML は colspan。"""
    ui = {
        "table_id": "P0_B1_6A",
        "rows": [
            ["", "6A", "", "", "", "", "", ""],
            ["", "朝", "1", "2", "3", "4", "5", "6"],
            ["11 （月）", "朝会", "模擬試験", "", "", "", "広間 練習", "広間 練習"],
        ],
        "metadata": {
            "header_rows": [0, 1],
            "data_start_row": 2,
            "horizontal_merges": [
                {"row_index": 2, "spans": [{"start": 2, "colspan": 4}]},
            ],
        },
    }
    filled_row = ui["rows"][2]
    rec = table_yaml_record(ui)
    cells = rec["data_rows"][0]["cells"]
    assert cells[2] == "模擬試験"
    assert cells[3] == "模擬試験"
    assert cells[4] == "模擬試験"
    assert cells[5] == "模擬試験"
    html = build_table_html_for_md(ui)
    assert 'colspan="4"' in html
    assert "模擬試験" in html
    assert html.count("<td></td>") < 3


def test_data_start_row_clips_bloated_header_rows_for_labels_and_yaml():
    """F58 の data_start_row があれば、過剰な header_rows でもデータ行を列ラベルに混ぜない。"""
    ui = {
        "table_id": "sched",
        "rows": [
            ["週", "A", "B"],
            ["", "s1", "s2"],
            ["27（月）", "国語", "算数"],
            ["28（火）", "社会", "理科"],
        ],
        "metadata": {
            "header_rows": [0, 1, 2, 3],
            "data_start_row": 2,
        },
    }
    labs = infer_column_labels(ui)
    assert "27" not in labs[0] and "28" not in labs[0]
    rec = table_yaml_record(ui)
    assert rec["header_row_indices"] == [0, 1]
    assert rec["data_rows"][0]["sheet_row"] == 2
    html = build_table_html_for_md(ui)
    assert html.count("<th>") == 6
    assert "<td>" in html
