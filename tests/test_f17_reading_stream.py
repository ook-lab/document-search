"""F17 reading_stream: prose and tables interleaved by sort_y."""

from dms.pipeline.stage_f.f17_reading_stream import build_f17_reading_stream


def test_reading_stream_interleaves_prose_and_table():
    prose = [
        {"page": 0, "y0": 0.1, "x0": 0, "text": "header", "source": "stage_b"},
        {"page": 0, "y0": 0.5, "x0": 0, "text": "footer", "source": "stage_b"},
    ]
    tables = [
        {
            "table_id": "P0_B1",
            "source": "stage_b",
            "bbox": [0, 0.3, 1, 0.4],
        },
    ]
    stream = build_f17_reading_stream(
        prose_blocks=prose,
        tables=tables,
        document_info={"page_height_pt": 792},
    )
    kinds = [e["kind"] for e in stream]
    assert kinds == ["non_table_paragraph", "table_ref", "non_table_paragraph"]
    assert stream[1]["table_id"] == "P0_B1"


def test_reading_stream_normalizes_pdf_pt_y0():
    """pdfplumber pt 座標 + page_height_pt で sort_y が付く。"""
    prose = [{"page": 0, "y0": 79.2, "x0": 0, "text": "header", "source": "stage_b"}]
    tables = [{"table_id": "B_T1", "bbox": [0, 200.0, 500, 220.0]}]
    stream = build_f17_reading_stream(
        prose_blocks=prose,
        tables=tables,
        document_info={"page_height_pt": 792.0},
    )
    assert stream[0].get("sort_y") is not None
    assert stream[0]["sort_y"] < stream[1]["sort_y"]
