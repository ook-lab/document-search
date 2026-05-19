"""
G26 罫線・分割プランの契約とパース（LLM は g26_semantic_estimator のみ）。
"""

from __future__ import annotations

from typing import Any, Dict, List

G26_LINE_SEMANTICS_CONTRACT = "g26_page_table_understanding_v1"

VALID_LINE_ROLES = frozenset(
    {
        "page_margin",
        "table_outer_border",
        "internal_row_divider",
        "internal_col_divider",
        "header_separator",
        "block_boundary",
        "decoration",
        "unknown",
    }
)

# LLM がプロンプト外の同義語を返したときのみ正規化（値の推測・欠損補完ではない）
_LINE_ROLE_ALIASES: Dict[str, str] = {
    "table_border": "table_outer_border",
    "outer_border": "table_outer_border",
    "table_frame": "table_outer_border",
    "row_divider": "internal_row_divider",
    "col_divider": "internal_col_divider",
    "column_divider": "internal_col_divider",
    "header_divider": "header_separator",
    "margin": "page_margin",
}


def normalize_line_role(role: Any) -> str:
    r = str(role or "").strip()
    if r in VALID_LINE_ROLES:
        return r
    return _LINE_ROLE_ALIASES.get(r, r)


class G26SemanticAIError(RuntimeError):
    """G26 ページ理解の契約違反・呼び出し失敗。"""


def _lines_preview(lines: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for ln in lines:
        rows.append(
            f"{ln.get('line_id')}: {ln.get('orientation')} "
            f"({ln.get('x0')},{ln.get('y0')})-({ln.get('x1')},{ln.get('y1')}) len={ln.get('length_norm')}"
        )
    return "\n".join(rows)


def _structured_tables_preview(tables: List[Dict[str, Any]], *, max_rows: int = 12) -> str:
    parts: List[str] = []
    for i, st in enumerate(tables):
        if not isinstance(st, dict):
            continue
        tid = st.get("table_id", f"T{i}")
        headers = st.get("headers") or []
        rows = st.get("rows") or []
        parts.append(f"--- table_index={i} table_id={tid!r} ---")
        if headers:
            parts.append(f"headers: {headers}")
        for ri, row in enumerate(rows[:max_rows]):
            parts.append(f"row{ri}: {list(row)}")
        if len(rows) > max_rows:
            parts.append(f"... {len(rows) - max_rows} more rows")
    return "\n".join(parts)


def parse_line_semantics(parsed: Dict[str, Any], source_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not source_lines:
        return []
    raw_lines = parsed.get("lines")
    if not isinstance(raw_lines, list):
        raise G26SemanticAIError("g26_ai_missing_lines_array")
    expected_ids = {str(ln["line_id"]) for ln in source_lines if ln.get("line_id")}
    out_lines: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_lines:
        if not isinstance(item, dict):
            raise G26SemanticAIError("g26_ai_line_item_invalid")
        lid = item.get("line_id")
        role = normalize_line_role(item.get("role"))
        meaning = item.get("meaning")
        if lid not in expected_ids:
            raise G26SemanticAIError(f"g26_ai_unknown_line_id: {lid!r}")
        if lid in seen:
            raise G26SemanticAIError(f"g26_ai_duplicate_line_id: {lid!r}")
        seen.add(str(lid))
        if role not in VALID_LINE_ROLES:
            raise G26SemanticAIError(f"g26_ai_invalid_role: {role!r}")
        if not isinstance(meaning, str):
            meaning = ""
        try:
            conf = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            raise G26SemanticAIError(f"g26_ai_missing_confidence: {lid!r}")
        if not (0.0 <= conf <= 1.0):
            raise G26SemanticAIError(f"g26_ai_confidence_out_of_range: {lid!r}")
        src = next((ln for ln in source_lines if ln.get("line_id") == lid), {})
        out_lines.append(
            {
                "line_id": str(lid),
                "orientation": src.get("orientation"),
                "x0": src.get("x0"),
                "y0": src.get("y0"),
                "x1": src.get("x1"),
                "y1": src.get("y1"),
                "role": str(role),
                "meaning": meaning.strip() if meaning else "",
                "confidence": conf,
            }
        )
    missing = expected_ids - seen
    if missing:
        raise G26SemanticAIError(f"g26_ai_missing_line_ids: {sorted(missing)[:20]}")
    return out_lines


def parse_table_layout_plans(raw: Any, *, n_tables: int) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise G26SemanticAIError("g26_ai_table_layout_plans_invalid")
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise G26SemanticAIError("g26_ai_table_plan_item_invalid")
        axis = item.get("split_axis")
        if axis not in ("none", "row", "col"):
            raise G26SemanticAIError(f"g26_ai_invalid_split_axis: {axis!r}")
        plan: Dict[str, Any] = {
            "table_index": item.get("table_index"),
            "table_id": item.get("table_id"),
            "split_axis": axis,
            "reason": str(item.get("reason") or "").strip(),
        }
        if not plan["reason"]:
            raise G26SemanticAIError("g26_ai_table_plan_missing_reason")
        if axis == "none":
            out.append(plan)
            continue
        blocks_key = "row_blocks" if axis == "row" else "col_blocks"
        blocks = item.get(blocks_key)
        if not isinstance(blocks, list) or len(blocks) < 2:
            raise G26SemanticAIError(f"g26_ai_table_plan_missing_{blocks_key}")
        norm_blocks: List[Dict[str, int]] = []
        for b in blocks:
            if not isinstance(b, dict):
                raise G26SemanticAIError("g26_ai_block_invalid")
            norm_blocks.append({"start": int(b["start"]), "end": int(b["end"])})
        norm_blocks.sort(key=lambda x: x["start"])
        for i in range(len(norm_blocks) - 1):
            if norm_blocks[i]["end"] >= norm_blocks[i + 1]["start"]:
                raise G26SemanticAIError(
                    f"g26_ai_{blocks_key}_overlap: {norm_blocks[i]!r} vs {norm_blocks[i + 1]!r}"
                )
        plan[blocks_key] = norm_blocks
        for ck in (
            "row_common_top",
            "row_common_bottom",
            "col_common_left",
            "col_common_right",
        ):
            v = item.get(ck)
            plan[ck] = list(v) if isinstance(v, list) else []
        out.append(plan)
    return out


def plan_to_g44_detection(plan: Dict[str, Any]) -> Dict[str, Any]:
    """G26 導出 table_layout_plan → G44 detection 辞書。"""
    axis = plan.get("split_axis", "none")
    empty = {
        "row_split": False,
        "row_blocks": None,
        "row_common_top": None,
        "row_common_bottom": None,
        "col_split": False,
        "col_blocks": None,
        "col_common_left": None,
        "col_common_right": None,
        "split_source": "g26_layout_split",
        "g26_split_reason": plan.get("reason"),
    }
    if axis == "none":
        return empty
    if axis == "row":
        return {
            **empty,
            "row_split": True,
            "row_blocks": plan.get("row_blocks"),
            "row_common_top": plan.get("row_common_top") or [],
            "row_common_bottom": plan.get("row_common_bottom") or [],
        }
    return {
        **empty,
        "col_split": True,
        "col_blocks": plan.get("col_blocks"),
        "col_common_left": plan.get("col_common_left") or [],
        "col_common_right": plan.get("col_common_right") or [],
    }


# 後方互換（旧 G25/F50 名）
plan_to_f56_detection = plan_to_g44_detection
G25DLineSemanticAIError = G26SemanticAIError
F50DLineSemanticAIError = G26SemanticAIError
G25_D_LINE_CONTRACT = G26_LINE_SEMANTICS_CONTRACT
F50_D_LINE_CONTRACT = G26_LINE_SEMANTICS_CONTRACT
_parse_lines = parse_line_semantics
_parse_table_layout_plans = parse_table_layout_plans
