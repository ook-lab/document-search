"""editor_document から AI 向け JSON と Markdown を生成する。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


def _escape_md_cell(s: str) -> str:
    t = (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    t = t.replace("|", "\\|")
    t = re.sub(r"\n+", "<br>", t)
    return t


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    hs = [_escape_md_cell(h) for h in headers]
    line0 = "| " + " | ".join(hs) + " |"
    line1 = "| " + " | ".join(["---"] * len(hs)) + " |"
    body = []
    for row in rows:
        padded = list(row) + [""] * (len(hs) - len(row))
        body.append("| " + " | ".join(_escape_md_cell(str(c)) for c in padded[: len(hs)]) + " |")
    return "\n".join([line0, line1, *body])


def build_ai_payloads(editor_document: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """
    editor_document 形式:
      version: 1
      tables: [{ id, kind: "pair"|"grid", rows? | title?, cells? }]
      text_sections: [{ id, title, body }]
    """
    doc = editor_document or {}
    tables_in = doc.get("tables") or []
    texts_in = doc.get("text_sections") or []

    pair_tables: List[Dict[str, Any]] = []
    grid_tables: List[Dict[str, Any]] = []
    md_parts: List[str] = []

    title_doc = (doc.get("title") or "").strip()
    if title_doc:
        md_parts.append(f"# {title_doc}\n")

    for i, t in enumerate(tables_in):
        kind = (t.get("kind") or "").strip()
        if kind == "pair":
            rows = t.get("rows") or []
            if not isinstance(rows, list):
                rows = []
            qa_rows: List[Dict[str, str]] = []
            for r in rows:
                if not isinstance(r, list):
                    continue
                qa_rows.append(
                    {
                        "question": str(r[0]) if len(r) > 0 else "",
                        "answer": str(r[1]) if len(r) > 1 else "",
                    }
                )
            pair_tables.append(
                {
                    "kind": "one_to_one",
                    "qa_rows": qa_rows,
                }
            )
            md_parts.append(f"## 1対1の対応表 {i + 1}\n")
            md_parts.append(_md_table(["質問", "回答"], [[q["question"], q["answer"]] for q in qa_rows]))
            md_parts.append("")
        elif kind == "grid":
            title = (t.get("title") or "").strip() or f"表 {i + 1}"
            cells = t.get("cells") or []
            if not isinstance(cells, list):
                cells = []
            grid_rows: List[List[str]] = []
            for row in cells:
                if isinstance(row, list):
                    grid_rows.append([str(c or "") for c in row])
                else:
                    grid_rows.append([])
            grid_tables.append({"kind": "grid", "title": title, "rows": grid_rows})
            md_parts.append(f"## {title}（表）\n")
            if grid_rows:
                width = max(len(r) for r in grid_rows)
                headers = [f"列{j + 1}" for j in range(width)]
                padded = [r + [""] * (width - len(r)) for r in grid_rows]
                md_parts.append(_md_table(headers, padded))
            md_parts.append("")

    text_blocks: List[Dict[str, str]] = []
    for j, s in enumerate(texts_in):
        if not isinstance(s, dict):
            continue
        st = (s.get("title") or "").strip() or f"テキスト {j + 1}"
        body = s.get("body")
        body_s = body if isinstance(body, str) else ""
        text_blocks.append({"title": st, "body": body_s})
        md_parts.append(f"## {st}\n")
        md_parts.append(body_s.strip())
        md_parts.append("")

    ai_json: Dict[str, Any] = {
        "version": 1,
        "document_title": title_doc,
        "one_to_one_tables": pair_tables,
        "grid_tables": grid_tables,
        "text_blocks": text_blocks,
    }
    return ai_json, "\n".join(md_parts).strip()
