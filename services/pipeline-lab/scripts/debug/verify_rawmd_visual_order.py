"""rawMD（buildSyntheticMarkdown）と Visual Editor が同じ visual_stream 順か検証する。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_lab_dir = Path(__file__).resolve().parents[2]  # services/pipeline-lab/
sys.path.insert(0, str(_lab_dir))

import blueprints.lab as lab  # noqa: E402

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


def _sort_visual_stream(stream: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(e: Dict[str, Any]) -> Tuple[float, float]:
        return (float(e.get("sort_y") or 0), float(e.get("tie") or 0))

    return sorted(stream, key=key)


def _visual_plan(
    res: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """Visual Editor と同じ並び: (kind, label)。"""
    vs = _sort_visual_stream(list((res.get("reading") or {}).get("visual_stream") or []))
    tabs = list((res.get("ui_data_json") or {}).get("tables") or [])
    plan: List[Tuple[str, str]] = []
    for seg in vs:
        k = seg.get("kind")
        if k == "non_table_paragraph":
            t = str(seg.get("text") or "").strip()
            if t:
                plan.append(("prose", t[:48]))
        elif k == "g_table":
            ti = int(seg.get("table_index") or 0)
            tbl = tabs[ti] if ti < len(tabs) else {}
            cap = str(tbl.get("description") or tbl.get("table_id") or f"表{ti + 1}")
            plan.append(("table", cap))
        elif k == "g_table_group":
            for ti in seg.get("table_indices") or []:
                tbl = tabs[int(ti)] if int(ti) < len(tabs) else {}
                cap = str(tbl.get("description") or tbl.get("table_id") or f"表{int(ti) + 1}")
                plan.append(("table", cap))
        elif k == "g21_article":
            if any(s.get("kind") == "non_table_paragraph" for s in vs):
                continue
            body = str(seg.get("body") or "").strip()
            if body:
                plan.append(("prose", body[:48]))
    return plan


def _rawmd_plan_from_build(res: Dict[str, Any]) -> List[Tuple[str, str]]:
    """buildSyntheticMarkdown と同じ走査で (kind, label) 列を得る。"""
    plan: List[Tuple[str, str]] = []
    ud = res.get("ui_data_json") or {}
    tabs = list(ud.get("tables") or [])
    vs = _sort_visual_stream(list((res.get("reading") or {}).get("visual_stream") or []))
    has_f1_prose = any(s.get("kind") == "non_table_paragraph" for s in vs)
    for seg in vs:
        k = seg.get("kind")
        if k == "non_table_paragraph":
            t = str(seg.get("text") or "").strip()
            if t:
                plan.append(("prose", t[:48]))
        elif k == "g21_article":
            if has_f1_prose:
                continue
            gb = str(seg.get("body") or "").strip()
            if gb:
                plan.append(("prose", gb[:48]))
        elif k == "g_table":
            ti = int(seg.get("table_index") or 0)
            tbl = tabs[ti] if ti < len(tabs) else {}
            cap = str(tbl.get("description") or tbl.get("table_id") or f"表{ti + 1}")
            plan.append(("table", cap))
        elif k == "g_table_group":
            for ti in seg.get("table_indices") or []:
                tbl = tabs[int(ti)] if int(ti) < len(tabs) else {}
                cap = str(tbl.get("description") or tbl.get("table_id") or f"表{int(ti) + 1}")
                plan.append(("table", cap))
    return plan


def _build_synthetic_markdown_python(res: Dict[str, Any]) -> str:
    """pipeline_lab.html buildSyntheticMarkdown の最小 Python 移植（検証用）。"""
    lines: List[str] = []
    ud = res.get("ui_data_json") or {}
    tabs = list(ud.get("tables") or [])
    vs = _sort_visual_stream(list((res.get("reading") or {}).get("visual_stream") or []))
    prose_hdr = False
    table_hdr = False
    has_f1_prose = any(s.get("kind") == "non_table_paragraph" for s in vs)

    for seg in vs:
        k = seg.get("kind")
        if k == "non_table_paragraph":
            t = str(seg.get("text") or "").strip()
            if not t:
                continue
            if not prose_hdr:
                lines.extend(["## 非表（F 地の文）", ""])
                prose_hdr = True
            else:
                lines.append("")
            lines.append(t)
        elif k == "g21_article":
            if has_f1_prose:
                continue
            gb = str(seg.get("body") or "").strip()
            if not gb:
                continue
            if not prose_hdr:
                lines.extend(["## 非表（F 地の文）", ""])
                prose_hdr = True
            else:
                lines.append("")
            lines.append(gb)
        elif k == "g_table":
            ti = int(seg.get("table_index") or 0)
            tbl = tabs[ti] if ti < len(tabs) else None
            if not tbl:
                continue
            if not table_hdr:
                lines.extend(["", "## 表（ui_data.tables）", ""])
                table_hdr = True
            cap = tbl.get("description") or tbl.get("table_id") or f"表 {ti + 1}"
            lines.extend([f"## {cap}", ""])
            rows = tbl.get("rows") or []
            if rows and isinstance(rows[0], list):
                hdr = tbl.get("headers") or rows[0]
                lines.append("| " + " | ".join(str(h) for h in hdr) + " |")
                lines.append("| " + " | ".join("---" for _ in hdr) + " |")
                dsr = int((tbl.get("metadata") or {}).get("data_start_row") or 1)
                for row in rows[dsr:]:
                    if isinstance(row, list):
                        lines.append("| " + " | ".join(str(c) for c in row) + " |")
            lines.append("")
        elif k == "g_table_group":
            if not table_hdr:
                lines.extend(["", "## 表（ui_data.tables）", ""])
                table_hdr = True
            for ti in seg.get("table_indices") or []:
                tbl = tabs[int(ti)] if int(ti) < len(tabs) else None
                if not tbl:
                    continue
                cap = tbl.get("description") or tbl.get("table_id") or f"表 {int(ti) + 1}"
                lines.extend([f"## {cap}", "", "_(group)_", ""])

    embed = str(ud.get("tables_md_embed") or "").strip()
    if embed:
        lines.extend(["## 表（埋め込み）", "", embed, ""])
    return "\n".join(lines) if lines else "（非表も表もまだありません。パイプライン実行後に再度開いてください。）"


def _compare_plans(visual: List[Tuple[str, str]], raw: List[Tuple[str, str]]) -> List[str]:
    errs: List[str] = []
    n = min(len(visual), len(raw))
    for i in range(n):
        if visual[i] != raw[i]:
            errs.append(f"pos {i}: visual={visual[i]!r} rawmd={raw[i]!r}")
    if len(visual) != len(raw):
        errs.append(f"length visual={len(visual)} rawmd={len(raw)}")
    return errs


def _check(name: str, pdf: Path, test_id: str) -> List[str]:
    if not pdf.is_file():
        return [f"PDF not found: {pdf}"]
    res = lab._run_pdf_pipeline_stages(pdf, Path(tempfile.mkdtemp()), test_id, 0)
    if not res.get("success"):
        return [f"pipeline failed: {res.get('error')}"]
    visual = _visual_plan(res)
    raw_plan = _rawmd_plan_from_build(res)
    md = _build_synthetic_markdown_python(res)
    errs = _compare_plans(visual, raw_plan)
    embed = str((res.get("ui_data_json") or {}).get("tables_md_embed") or "").strip()
    if embed and "## 表（埋め込み）" not in md:
        errs.append("tables_md_embed missing from synthetic md")
    print(f"\n=== {name} ===")
    print(f"visual_stream segments: {len(visual)}")
    for i, p in enumerate(visual):
        print(f"  {i}: {p[0]} {p[1]!r}")
    if errs:
        print("MISMATCH:")
        for e in errs:
            print(f"  - {e}")
    else:
        print("OK: rawMD stream order matches Visual (embed appendix excluded)")
    if embed:
        print(f"note: tables_md_embed {len(embed)} chars appended only in rawMD tab")
    return errs


def main() -> int:
    all_err: List[str] = []
    for key, pdf in PDFS.items():
        errs = _check(key, pdf, f"rawmd_{key}")
        all_err.extend(f"{key}: {e}" for e in errs)
    return 1 if all_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
