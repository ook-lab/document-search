"""Validate pipeline on two reference PDFs (generic checks only)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "pipeline-lab"))

import blueprints.lab as lab  # noqa: E402
from dms.pipeline.stage_f.table_md_emitters import build_table_html_for_md  # noqa: E402

PDFS = {
    "shushu": Path(
        r"H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom"
        r"\2025収支報告(新6年).pdf [1eBkcAj5QrAPv1-MW3UtFPGoY6kDYPKgU].pdf"
    ),
    "gakunen": Path(
        r"H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom"
        r"\学年通信（47）.pdf [1nl6AtwVZbuW1ljqFRaUU3dHdBIzQjaoD].pdf"
    ),
}
OUT = ROOT / "tests" / "_tmp_two_pdf_validation.md"


def _run(pdf: Path, test_id: str) -> Dict[str, Any]:
    return lab._run_pdf_pipeline_stages(pdf, Path(tempfile.mkdtemp()), test_id, 0)


def _table_ids(res: Dict[str, Any]) -> List[str]:
    return [str(t.get("table_id") or "") for t in (res.get("ui_data_json") or {}).get("tables") or []]


def _failures_shushu(res: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not res.get("success"):
        errs.append(f"pipeline failed: {res.get('error')}")
        return errs
    vs = (res.get("reading") or {}).get("visual_stream") or []
    def _stream_table_count() -> int:
        n = 0
        for e in vs:
            k = e.get("kind")
            if k == "g_table":
                n += 1
            elif k == "g_table_group":
                n += len(e.get("table_indices") or [])
        return n

    tables = [e for e in vs if e.get("kind") == "g_table"]
    n_stream_tables = _stream_table_count()
    if n_stream_tables < 2:
        errs.append(f"visual_stream tables={n_stream_tables} expected>=2")
    prose = [e for e in vs if e.get("kind") == "non_table_paragraph"]
    if tables and prose:
        first_table_y = min(float(t.get("sort_y") or 0) for t in tables)
        mid_prose = [p for p in prose if float(p.get("sort_y") or 0) > first_table_y + 0.05]
        if mid_prose and any(float(t.get("sort_y") or 0) < float(mid_prose[0].get("sort_y") or 0) for t in tables[2:]):
            errs.append("visual_stream: table after mid-page prose appears before prose")
    ui = (res.get("ui_data_json") or {}).get("tables") or []
    if len(ui) < 2:
        errs.append(f"ui tables={len(ui)} expected>=2")
    by_id = {str(t.get("table_id")): t for t in ui}
    if by_id.get("B_T1_S1") or by_id.get("B_T1_S2"):
        errs.append("B_T1 must stay unified (no B_T1_S1/B_T1_S2 col_split)")
    t1 = by_id.get("B_T1")
    if not t1:
        errs.append("missing B_T1")
    else:
        rows = t1.get("rows") or []
        dsr = int((t1.get("metadata") or {}).get("data_start_row") or 1)
        body = rows[dsr:] if dsr < len(rows) else rows
        income_rows = [
            r for r in body
            if r and str(r[0] or "").strip() and str(r[1] or "").strip()
        ]
        if len(income_rows) < 3:
            errs.append(f"B_T1 income-aligned rows={len(income_rows)} expected>=3")
        for r in body:
            c0 = str((r[0] if r else "") or "")
            if "前年度" in c0 and "積立金" in c0:
                errs.append("B_T1 income labels merged in one cell")
                break
        for r in body:
            if "転入" in str((r[0] if r else "") or "") and "③" in str((r[2] if len(r) > 2 else "") or ""):
                break
        else:
            errs.append("B_T1: 転入時追加納入 row must align with ③ on the right")
    b2 = by_id.get("B_T2")
    if not b2:
        errs.append("missing B_T2")
    else:
        meta = b2.get("metadata") or {}
        rows = b2.get("rows") or []
        if meta.get("header_rows") not in ([], None) and meta.get("header_rows") != []:
            if meta.get("data_start_row") != 0 and meta.get("header_rows"):
                pass
        dsr = int(meta.get("data_start_row") or 0)
        if len(rows) - dsr < 5:
            errs.append(f"B_T2 data rows={len(rows) - dsr} expected>=5")
    return errs


def _failures_gakunen(res: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not res.get("success"):
        errs.append(f"pipeline failed: {res.get('error')}")
        return errs
    vs = (res.get("reading") or {}).get("visual_stream") or []
    if not vs:
        errs.append("empty visual_stream")
    tables_vs = [e for e in vs if e.get("kind") == "g_table"]
    ui = (res.get("ui_data_json") or {}).get("tables") or []
    if not ui and not tables_vs:
        errs.append("no tables in ui or visual_stream")
    by_id = {str(t.get("table_id") or ""): t for t in ui}
    if not by_id:
        errs.append("no ui tables")
    sched = [k for k in by_id if k.startswith("P0_B1")]
    if not sched:
        errs.append("missing P0_B1 schedule subtable(s)")
    for sid, t in by_id.items():
        rows = t.get("rows") or []
        if not rows:
            errs.append(f"{sid}: empty rows")
            continue
        html = build_table_html_for_md(t)
        if "<table" not in html:
            errs.append(f"{sid}: no html table")
        for r in rows:
            for c in r or []:
                if isinstance(c, str) and " / " in c:
                    errs.append(f"{sid}: slash-joined cell {c[:40]!r}")
                    break
        if sid.startswith("P0_B2"):
            w = max((len(r) for r in rows if isinstance(r, (list, tuple))), default=0)
            if w < 2:
                errs.append(f"{sid}: expected >=2 columns, got {w}")
    for sid in sched:
        t = by_id[sid]
        rows = t.get("rows") or []
        dsr = int((t.get("metadata") or {}).get("data_start_row") or 1)
        body = rows[dsr:]
        if body and not str((body[0][0] if body[0] else "") or "").strip():
            errs.append(f"{sid}: first body row missing col0 date/label")
    return errs


def _report(name: str, pdf: Path, res: Dict[str, Any], failures: List[str]) -> List[str]:
    lines = [f"\n## {name}", f"path={pdf}", f"success={res.get('success')} error={res.get('error')}"]
    if failures:
        lines.append(f"FAILURES ({len(failures)}):")
        for f in failures:
            lines.append(f"  - {f}")
    else:
        lines.append("OK")
    vs = (res.get("reading") or {}).get("visual_stream") or []
    lines.append("\n### visual_stream")
    for i, e in enumerate(vs[:30]):
        k = e.get("kind")
        sy = e.get("sort_y")
        if k == "non_table_paragraph":
            t = (e.get("text") or "")[:60].replace("\n", " ")
            lines.append(f"{i}: {k} sy={sy} {t}")
        else:
            lines.append(f"{i}: {k} sy={sy} idx={e.get('table_index')}")
    if len(vs) > 30:
        lines.append(f"... ({len(vs) - 30} more)")
    for t in (res.get("ui_data_json") or {}).get("tables") or []:
        lines.append(f"\n### table {t.get('table_id')} headers={t.get('headers')}")
        meta = t.get("metadata") or {}
        lines.append(f"hr={meta.get('header_rows')} dsr={meta.get('data_start_row')}")
        lines.append(build_table_html_for_md(t)[:2000])
    return lines


def main() -> int:
    all_fail: List[Tuple[str, List[str]]] = []
    out_lines = ["# two PDF validation", ""]

    res_sh = _run(PDFS["shushu"], "val_shushu")
    fail_sh = _failures_shushu(res_sh)
    out_lines.extend(_report("shushu", PDFS["shushu"], res_sh, fail_sh))
    if fail_sh:
        all_fail.append(("shushu", fail_sh))

    res_gk = _run(PDFS["gakunen"], "val_gakunen")
    fail_gk = _failures_gakunen(res_gk)
    out_lines.extend(_report("gakunen", PDFS["gakunen"], res_gk, fail_gk))
    if fail_gk:
        all_fail.append(("gakunen", fail_gk))

    OUT.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"wrote {OUT}")
    pipeline_ok = res_sh.get("success") and res_gk.get("success")
    if all_fail:
        for name, fails in all_fail:
            print(f"QUALITY {name}:")
            for f in fails:
                print(f"  {f}")
    if not pipeline_ok:
        print("PIPELINE FAILED")
        return 1
    if all_fail:
        print("PIPELINE OK (G11 完走) — 品質チェックに差分あり（上記 QUALITY）")
        return 0
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
