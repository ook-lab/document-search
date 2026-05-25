"""収支報告 PDF の読み順・表 ID を確認。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_lab_dir = Path(__file__).resolve().parents[2]  # services/pipeline-lab/
sys.path.insert(0, str(_lab_dir))

import validate_two_pdfs as v  # noqa: E402

res = v._run(v.PDFS["shushu"], "val_shushu_fix")
print("success=", res.get("success"), "error=", res.get("error"))
print("quality_fails=", v._failures_shushu(res))
ui = res.get("ui_data_json") or {}
print("tables:", [t.get("table_id") for t in ui.get("tables") or []])
vs = (res.get("reading") or {}).get("visual_stream") or []
for i, e in enumerate(vs):
    k = e.get("kind")
    sy = e.get("sort_y")
    if k == "g_table":
        ti = int(e.get("table_index") or 0)
        tabs = ui.get("tables") or []
        tid = tabs[ti].get("table_id") if ti < len(tabs) else "?"
        print(f"{i}: table sy={sy} id={tid}")
    elif k == "g_table_group":
        tabs = ui.get("tables") or []
        ids = [
            tabs[int(ti)].get("table_id")
            for ti in (e.get("table_indices") or [])
            if int(ti) < len(tabs)
        ]
        print(f"{i}: table_group sy={sy} ids={ids}")
    else:
        t = (e.get("text") or "")[:55].replace("\n", " ")
        print(f"{i}: prose sy={sy} {t}")
