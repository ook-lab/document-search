from dms.pipeline.stage_f.merged_cell_grid import apply_merged_cell_resolution


def test_apply_merged_cell_resolution_fills_mock_exam_span():
    grid = [
        ["", "朝", "1", "2", "3", "4", "5", "6"],
        ["11 （月）", "朝会", "模擬試験", "", "", "", "広間 練習", "広間 練習"],
    ]
    out, merges = apply_merged_cell_resolution(
        grid, data_start_row=1, row_label_col=0
    )
    assert out[1][2] == "模擬試験"
    assert out[1][3] == "模擬試験"
    assert merges == [{"row_index": 1, "spans": [{"start": 2, "colspan": 4}]}]
