"""
Stage G の ui_data から最終 Markdown を生成し、03–05 系 raw の pdf_md_content に保存するためのユーティリティ。

正本は raw；doc-processor は 09 に本文を書かない。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

CLASSROOM_RAW_MD_TABLES = frozenset(
    {
        "03_ema_classroom_01_raw",
        "04_ikuya_classroom_01_raw",
        "05_ikuya_waseaca_01_raw",
    }
)


def ui_data_to_final_markdown(ui_data: Optional[Dict[str, Any]]) -> str:
    """ui_data を人間可読な Markdown に直列化する。"""
    if not ui_data or not isinstance(ui_data, dict):
        return ""

    parts: List[str] = []

    sections = ui_data.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            title = (sec.get("title") or "").strip()
            body = (sec.get("body") or "").strip()
            if title:
                parts.append(f"## {title}\n")
            if body:
                parts.append(body)
                parts.append("")

    tables = ui_data.get("tables") or []
    if isinstance(tables, list) and tables:
        parts.append("## 表\n")
        for i, tbl in enumerate(tables):
            if not isinstance(tbl, dict):
                continue
            desc = (tbl.get("description") or tbl.get("table_id") or f"表 {i + 1}").strip()
            parts.append(f"### {desc}\n")
            rows = tbl.get("rows")
            if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
                headers = sorted({k for r in rows for k in r.keys()})
                if headers:
                    parts.append("| " + " | ".join(headers) + " |")
                    parts.append("| " + " | ".join("---" for _ in headers) + " |")
                    for r in rows:
                        parts.append(
                            "| "
                            + " | ".join(str(r.get(h, "")).replace("\n", " ") for h in headers)
                            + " |"
                        )
                    parts.append("")
            else:
                parts.append("```json")
                parts.append(json.dumps(tbl, ensure_ascii=False, indent=2)[:80000])
                parts.append("```\n")

    timeline = ui_data.get("timeline") or []
    if isinstance(timeline, list) and timeline:
        parts.append("## タイムライン\n")
        for ev in timeline:
            if not isinstance(ev, dict):
                continue
            line = " - "
            if ev.get("event"):
                line += str(ev.get("event")).strip()
            if ev.get("date"):
                line += f"（{ev.get('date')}）"
            parts.append(line)
        parts.append("")

    actions = ui_data.get("actions") or []
    if isinstance(actions, list) and actions:
        parts.append("## アクション・タスク\n")
        for a in actions:
            if isinstance(a, dict):
                parts.append(f" - {json.dumps(a, ensure_ascii=False)}")
            else:
                parts.append(f" - {a}")
        parts.append("")

    notices = ui_data.get("notices") or []
    if isinstance(notices, list) and notices:
        parts.append("## お知らせ\n")
        for n in notices:
            if isinstance(n, dict):
                parts.append(f" - {json.dumps(n, ensure_ascii=False)}")
            else:
                parts.append(f" - {n}")
        parts.append("")

    return "\n".join(parts).strip()
