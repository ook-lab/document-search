from dms.pipeline.stage_f.merged_cell_signals import grid_needs_merged_cell_ai


def test_multiline_triggers_ai():
    grid = [["h", "h2"], ["a\nb", "c", "d"]]
    assert grid_needs_merged_cell_ai(grid, data_start_row=1)
