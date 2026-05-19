"""One-off check for 2025収支報告 PDF pipeline output."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "pipeline-lab"))

import blueprints.lab as lab  # noqa: E402
from dms.pipeline.stage_f.table_md_emitters import build_table_html_for_md  # noqa: E402

PDF = Path(
    r"H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom"
    r"\2025収支報告(新6年).pdf [1eBkcAj5QrAPv1-MW3UtFPGoY6kDYPKgU].pdf"
)
OUT = ROOT / "tests" / "_tmp_shushu_check.md"


def main() -> None:
    res = lab._run_pdf_pipeline_stages(PDF, Path(tempfile.mkdtemp()), "sh3", 0)
    vs = (res.get("reading") or {}).get("visual_stream") or []
    lines = [f"success={res.get('success')} error={res.get('error')}", "", "# visual_stream", ""]
    for i, e in enumerate(vs):
        k = e.get("kind")
        sy = e.get("sort_y")
        if k == "non_table_paragraph":
            t = (e.get("text") or "")[:80].replace("\n", " ")
            lines.append(f"{i}: {k} sy={sy} {t}")
        else:
            idx = e.get("table_index", e.get("table_indices"))
            lines.append(f"{i}: {k} sy={sy} idx={idx}")

    tables = (res.get("ui_data_json") or {}).get("tables") or []
    for ti, t in enumerate(tables):
        lines.append(f"\n# table {ti} id={t.get('table_id')} headers={t.get('headers')}")
        meta = t.get("metadata") or {}
        lines.append(f"hr={meta.get('header_rows')} dsr={meta.get('data_start_row')} merges={meta.get('horizontal_merges')}")
        lines.append(build_table_html_for_md(t))

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
