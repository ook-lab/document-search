"""G36: 右 Y 帯へ左収入クラスタを割当。"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from dms.pipeline.stage_g.g36_lr_merged_vertical_grid import (
    _left_y_clusters,
    _pick_left_cluster_for_band,
    classify_vertical_merge_mode,
    rebuild_lr_merged_vertical_table,
    G36_GEOMETRY_CONTRACT,
)


def test_pick_cluster_by_vertical_overlap():
    clusters = [
        (10.0, 18.0, "前年度繰越金", "8,538,932"),
        (40.0, 48.0, "積立金(72名）", "6,048,000"),
        (70.0, 78.0, "転入時追加納入", "123,000"),
    ]
    used: set[int] = set()
    b1 = _pick_left_cluster_for_band((8.0, 20.0), clusters, used)
    assert b1 is not None
    used.add(b1[0])
    assert b1[1][2] == "前年度繰越金"
    b3 = _pick_left_cluster_for_band((68.0, 80.0), clusters, used)
    assert b3 is not None
    assert b3[1][2] == "転入時追加納入"


@pytest.mark.integration
def test_shushu_income_rows_align_with_expense_bands():
    pdf = Path(
        r"H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom"
        r"\2025収支報告(新6年).pdf [1eBkcAj5QrAPv1-MW3UtFPGoY6kDYPKgU].pdf"
    )
    if not pdf.is_file():
        pytest.skip("fixture pdf missing")

    with pdfplumber.open(pdf) as doc:
        page = doc.pages[0]
        table = page.find_tables()[0]
        raw = table.extract() or []
        mode, evidence = classify_vertical_merge_mode(page, table)
        assert evidence["left_cluster_count"] >= 3
        out, _meta = rebuild_lr_merged_vertical_table(
            page,
            table,
            mode=mode,
            judge_meta={"vertical_merge_judge": G36_GEOMETRY_CONTRACT},
        )

    rows = [r for r in out[1:] if any(str(c or "").strip() for c in r)]
    left_rows = {
        (str(r[0] or "").strip(), str(r[1] or "").strip())
        for r in rows
        if str(r[0] or "").strip()
    }
    assert ("前年度繰越金", "8,538,932") in left_rows
    assert ("積立金(72名）", "6,048,000") in left_rows
    assert ("転入時追加納入", "123,000") in left_rows

    for r in rows:
        left = str(r[0] or "")
        exp = str(r[2] or "")
        if "転入" in left:
            assert "③" in exp or "③" in str(r[2] or ""), (
                f"転入行は右③と同じ帯であるべき: {r}"
            )
            break
    else:
        pytest.fail("転入時追加納入の行が見つからない")
