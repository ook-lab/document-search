"""F6 向け: 表の構造化正本（SSOT）と人が読む用 HTML 断片の生成。"""

from __future__ import annotations

import html
from typing import Any, Dict, List

from dms.pipeline.stage_g.table_md_emitters import build_table_html_for_md, build_tables_markdown_embed


def build_tables_ssot(e13_result: Dict[str, Any], e14_result: Dict[str, Any]) -> Dict[str, Any]:
    """パイプラインが再検証・差分に使う表まわりの正本（JSON 互換の dict）。"""
    return {
        "structured_tables": e13_result.get("structured_tables") or [],
        "detections": e13_result.get("detections") or [],
        "e14_reconstructed": e14_result.get("e14_reconstructed") or [],
    }


def build_tables_review_html(ui_tables: List[Dict[str, Any]]) -> str:
    """印刷・ブラウザ閲覧向けの単純 HTML（`<section>` + `<table>`・月 colspan 対応）。"""
    parts: List[str] = []
    for t in ui_tables or []:
        tid = html.escape(str(t.get("table_id") or ""))
        desc = html.escape(str(t.get("description") or ""))
        parts.append(f'<section class="review-table"><h3 class="review-table-id">{tid}</h3>')
        if desc:
            parts.append(f'<p class="review-table-desc">{desc}</p>')
        inner = build_table_html_for_md(t).replace(
            'class="md-embed-table"', 'class="review-table-grid"'
        )
        if not inner:
            parts.append("<p>(no rows)</p></section>")
            continue
        parts.append(inner)
        parts.append("</section>")
    return "\n".join(parts)
