"""stage_d_line_digest: unified_lines 座標の載せ方。"""

from dms.pipeline.stage_f.stage_d_line_digest import build_stage_d_line_digest


def test_build_digest_includes_line_coordinates():
    stage_d = {
        "page_index": 0,
        "tables": [
            {
                "table_id": "P0_T0",
                "origin_uid": "ou1",
                "bbox": [0.1, 0.1, 0.9, 0.9],
                "cell_map": [{}, {}],
            }
        ],
        "debug": {
            "vector_lines": {"page_size": [595.0, 842.0]},
            "grid_result": {
                "intersections": [1, 2],
                "unified_lines": {
                    "horizontal": [{"x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.2, "source": "vector"}],
                    "vertical": [{"x0": 0.5, "y0": 0.1, "x1": 0.5, "y1": 0.9, "source": "raster"}],
                },
            },
        },
    }
    d = build_stage_d_line_digest(stage_d)
    assert d["available"] is True
    assert len(d["lines"]) == 2
    assert d["lines"][0]["line_id"] == "h0"
    assert d["tables"][0]["line_ids_near"] == ["h0", "v0"]


def test_build_digest_empty_when_no_input():
    assert build_stage_d_line_digest(None)["available"] is False
