"""
F51: 表の**内容**を読み、縦（上下）・横（左右）の結合セルと論理格子の対応を明示して再構成する。

契約: g36_merged_cell_correspondence_v3
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Literal, Optional, Tuple

from loguru import logger

from dms.pipeline.stage_g.g36_lr_merged_vertical_grid import VerticalMergeMode
from dms.pipeline.stage_g.merged_cell_grid import RowMergeMeta

G36_LR_VERTICAL_AI_CONTRACT = "g36_merged_cell_correspondence_v3"
G36_MERGED_CELL_AI_CONTRACT = G36_LR_VERTICAL_AI_CONTRACT
G36_LR_VERTICAL_AI_MODEL = "gemini-2.5-flash-lite"

LayoutKind = Literal[
    "row_aligned",
    "block_common",
    "full_resolution",
    "no_merge",
    "no_vertical_merge",
]
_NO_MERGE_KINDS = frozenset({"no_merge", "no_vertical_merge"})
_VALID_LAYOUT: frozenset[str] = frozenset(
    {"row_aligned", "block_common", "full_resolution", "no_merge", "no_vertical_merge"}
)
_ROW_KEYS = ("col0_item", "col1_amount", "col2_right", "col3_right", "col4_right")
_LEFT_BLOCK_KEYS = ("col0_item", "col1_amount")


class G36LRVerticalAIError(RuntimeError):
    """G36 結合セル AI の契約違反・呼び出し失敗。"""


def _extract_json(text: str) -> str:
    text = text.strip()
    
    start_obj = text.find('{')
    end_obj = text.rfind('}')
    
    start_arr = text.find('[')
    end_arr = text.rfind(']')
    
    if start_obj != -1 and start_arr != -1:
        if start_obj < start_arr:
            if end_obj != -1 and end_obj > end_arr:
                return text[start_obj:end_obj + 1]
            elif end_arr != -1:
                return text[start_arr:end_arr + 1]
        else:
            if end_arr != -1 and end_arr > end_obj:
                return text[start_arr:end_arr + 1]
            elif end_obj != -1:
                return text[start_obj:end_obj + 1]
    
    if start_obj != -1 and end_obj != -1 and start_obj < end_obj:
        return text[start_obj:end_obj + 1]
        
    if start_arr != -1 and end_arr != -1 and start_arr < end_arr:
        return text[start_arr:end_arr + 1]
        
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        return text[start:end].strip()
    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        return text[start:end].strip()
    return text


def _cell_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _format_table_for_reading(data: List[List[Any]], *, max_rows: int = 32) -> str:
    lines: List[str] = []
    ncols = max((len(r) for r in data if isinstance(r, (list, tuple))), default=0)
    lines.append(
        f"columns: 0..{ncols - 1} — 結合セルは同じ文字の繰り返し、空、None、または extract 上の改行で現れる"
    )
    for ri, row in enumerate(data[:max_rows]):
        if not isinstance(row, (list, tuple)):
            continue
        parts: List[str] = []
        for ci in range(max(ncols, 1)):
            v = row[ci] if ci < len(row) else ""
            s = _cell_str(v)
            if len(s) > 120:
                s = s[:117] + "..."
            parts.append(f"[{ci}]={s!r}")
        lines.append(f"extract_row={ri}: " + " ".join(parts))
    if len(data) > max_rows:
        lines.append(f"... ({len(data) - max_rows} more extract rows)")
    return "\n".join(lines)


def _cells_from_logical_item(item: Dict[str, Any], index: int) -> List[str]:
    if "cells" in item:
        raw = item["cells"]
        if not isinstance(raw, list) or not raw:
            raise G36LRVerticalAIError(f"g36_ai_logical_row_empty_cells: index={index}")
        return [_cell_str(c) for c in raw]
    if all(k in item for k in _ROW_KEYS):
        return [_cell_str(item[k]) for k in _ROW_KEYS]
    raise G36LRVerticalAIError(f"g36_ai_logical_row_missing_cells: index={index}")


def _parse_horizontal_merges(
    raw: Any,
    *,
    n_rows: int,
    ncols: int,
) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise G36LRVerticalAIError("g36_ai_horizontal_merges_invalid")
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise G36LRVerticalAIError("g36_ai_horizontal_merge_item_invalid")
        try:
            ri = int(item["row_index"])
            start = int(item["start_col"])
            span = int(item["colspan"])
        except (KeyError, TypeError, ValueError):
            raise G36LRVerticalAIError("g36_ai_horizontal_merge_fields_missing")
        if span < 2:
            logger.debug(f"[G36] horizontal_merge span<2 を無視: row={ri} span={span}")
            continue
        if ri < 0 or ri >= n_rows or start < 0 or start + span > ncols:
            raise G36LRVerticalAIError(f"g36_ai_horizontal_merge_out_of_range: row={ri}")
        out.append({"row_index": ri, "spans": [{"start": start, "colspan": span}]})
    return out


def _parse_vertical_merges(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise G36LRVerticalAIError("g36_ai_vertical_merges_invalid")
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise G36LRVerticalAIError("g36_ai_vertical_merge_item_invalid")
        note = item.get("description") or item.get("note")
        if not isinstance(note, str) or not note.strip():
            raise G36LRVerticalAIError("g36_ai_vertical_merge_missing_description")
        out.append(
            {
                "description": note.strip(),
                "anchor_row": item.get("anchor_row"),
                "anchor_col": item.get("anchor_col"),
                "rowspan": item.get("rowspan"),
                "source_extract_rows": item.get("source_extract_rows"),
            }
        )
    return out


def _parse_ai_correspondence(parsed: Dict[str, Any]) -> Dict[str, Any]:
    kind = parsed.get("layout_kind")
    if kind not in _VALID_LAYOUT:
        raise G36LRVerticalAIError(f"g36_ai_invalid_layout_kind: {kind!r}")

    summary = parsed.get("correspondence_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise G36LRVerticalAIError("g36_ai_missing_correspondence_summary")

    rationale = parsed.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise G36LRVerticalAIError("g36_ai_missing_rationale")

    try:
        conf = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        raise G36LRVerticalAIError("g36_ai_missing_confidence")
    if not (0.0 <= conf <= 1.0):
        raise G36LRVerticalAIError("g36_ai_confidence_out_of_range")

    out: Dict[str, Any] = {
        "layout_ai_contract": G36_LR_VERTICAL_AI_CONTRACT,
        "layout_kind": str(kind),
        "confidence": conf,
        "rationale": rationale.strip(),
        "correspondence_summary": summary.strip(),
        "logical_rows": [],
        "left_block": None,
        "horizontal_merges": [],
        "vertical_merges": [],
    }

    if kind in _NO_MERGE_KINDS:
        lr = parsed.get("logical_rows")
        if lr is not None and lr != []:
            raise G36LRVerticalAIError(
                "g36_ai_contradiction: no_merge_with_logical_rows"
            )
        return out

    logical_raw = parsed.get("logical_rows")
    if not isinstance(logical_raw, list) or not logical_raw:
        raise G36LRVerticalAIError("g36_ai_missing_logical_rows")

    logical: List[List[str]] = []
    for i, item in enumerate(logical_raw):
        if not isinstance(item, dict):
            raise G36LRVerticalAIError(f"g36_ai_logical_row_invalid: index={i}")
        logical.append(_cells_from_logical_item(item, i))
    if kind == "full_resolution" and logical:
        non_empty = sum(
            1 for row in logical for c in row if _cell_str(c)
        )
        if non_empty == 0:
            raise G36LRVerticalAIError("g36_ai_empty_logical_rows")
    out["logical_rows"] = logical

    ncols = max((len(r) for r in logical), default=0)
    header_rows = 1
    n_rows = header_rows + len(logical)
    out["horizontal_merges"] = _parse_horizontal_merges(
        parsed.get("horizontal_merges"),
        n_rows=n_rows,
        ncols=ncols,
    )
    out["vertical_merges"] = _parse_vertical_merges(parsed.get("vertical_merges"))
    _validate_logical_rows_contract(out)

    if "extract_header_row" in parsed:
        raw_ehr = parsed.get("extract_header_row")
        if raw_ehr is None:
            out["extract_header_row"] = None
        else:
            try:
                out["extract_header_row"] = int(raw_ehr)
            except (TypeError, ValueError) as e:
                raise G36LRVerticalAIError("g36_ai_invalid_extract_header_row") from e
    else:
        out["extract_header_row"] = 0

    if "header_cells" in parsed:
        raw_hc = parsed.get("header_cells")
        if raw_hc is None:
            out["header_cells"] = None
        elif isinstance(raw_hc, list):
            out["header_cells"] = [_cell_str(c) for c in raw_hc]
        else:
            raise G36LRVerticalAIError("g36_ai_header_cells_invalid")

    if kind == "block_common":
        lb = parsed.get("left_block")
        if not isinstance(lb, dict):
            raise G36LRVerticalAIError("g36_ai_missing_left_block")
        left_block: Dict[str, str] = {}
        for k in _LEFT_BLOCK_KEYS:
            if k not in lb:
                raise G36LRVerticalAIError(f"g36_ai_left_block_missing_{k}")
            left_block[k] = _cell_str(lb[k])
        out["left_block"] = left_block

    return out


def _validate_logical_rows_contract(ai: Dict[str, Any]) -> None:
    """logical_rows と vertical_merges / 禁止パターンの矛盾を契約違反として落とす。"""
    logical: List[List[str]] = ai.get("logical_rows") or []
    for row in logical:
        for cell in row:
            if " / " in _cell_str(cell):
                raise G36LRVerticalAIError(
                    "g36_ai_slash_joined_cell: use separate logical_rows per item"
                )
    for vm in ai.get("vertical_merges") or []:
        src = vm.get("source_extract_rows")
        if not isinstance(src, list) or len(src) <= 1:
            continue
        try:
            ac = int(vm["anchor_col"])
        except (KeyError, TypeError, ValueError):
            continue
        for row in logical:
            if ac < len(row) and " / " in _cell_str(row[ac]):
                raise G36LRVerticalAIError(
                    "g36_ai_slash_join_contradicts_vertical_merge"
                )


def judge_lr_vertical_layout_ai(
    *,
    table_preview: List[List[Any]],
    geometry_evidence: Dict[str, Any],
    geometry_hint: Optional[VerticalMergeMode | str] = None,
    document_id: Optional[str] = None,
    contract_error: Optional[str] = None,
) -> Dict[str, Any]:
    """表内容を読み、縦・横の結合と論理行を返す。"""
    from dms.common.gemini_studio_key import google_ai_studio_api_key

    api_key = (google_ai_studio_api_key() or os.environ.get("GOOGLE_AI_API_KEY") or "").strip()
    if not api_key:
        raise G36LRVerticalAIError("GOOGLE_AI_API_KEY_missing")

    if not table_preview:
        raise G36LRVerticalAIError("g36_ai_empty_table_preview")

    reading = _format_table_for_reading(table_preview)
    geom_json = json.dumps(geometry_evidence, ensure_ascii=False, indent=2)
    hint_line = (
        f"geometry_hint（参考のみ）: {geometry_hint!r}\n" if geometry_hint else ""
    )
    err_block = ""
    if contract_error and str(contract_error).strip():
        err_block = f"\n## 前回の契約違反（必ず修正）\n{contract_error.strip()}\n"
    prompt = f"""あなたは PDF 表の読解者です。**セルの文字内容**を読み、**縦（上下）・横（左右）どちら向きの結合セル**があるかを明らかにし、論理格子を出力してください。
{err_block}

帳票名だけで決めないでください。extract_row のセル文字列に `\\n` が含まれる場合は同一セル内の改行です（` / ` で連結した1セルとして出力しない）。

## 表データ（pdfplumber 抽出）
{reading}

## geometry_evidence（参考）
{geom_json}
{hint_line}
## 結合セルの見分け方
- **縦（上下）**: 1 extract 行に複数行の文字（セル内 `\\n`）→ **full_resolution** で logical_rows を1項目1行に分ける。複数列ブロックが並ぶ表では、左ブロックと右ブロックの**行数を揃える**。
- **横（左右）**: 1つの見た目セルが複数列にまたがる → アンカー列に文字、続く列は `""`（空）とし **horizontal_merges** に記録。
- **no_merge** は、データ行すべてが単一行セルで、縦積み `\\n` も横プレースホルダも無いときだけ。
- **extract_header_row**: extract 上のヘッダー行インデックス（通常 0）。**ヘッダー行が無い表**（データのみ）のときは **null** とし、**header_cells** は `[]`。
- **header_cells**: extract_header_row が null のとき必須。見出し文字を創作しない（空配列可。列見出しは下流が別途付与）。

## 手順
1. **correspondence_summary** … 縦結合・横結合・列ブロック対応を日本語3〜5文で説明。
2. **vertical_merges** … 縦結合ごとに {{description, anchor_row?, anchor_col?, rowspan?, source_extract_rows?}}（再構成の監査用。空配列可）。
3. **layout_kind**:
   - **full_resolution** … 縦横いずれかの結合を論理行に解いた（推奨・汎用）
   - **row_aligned** … 左縦結合＋右行対応（旧来型）
   - **block_common** … 左1ブロック＋右複数行
   - **no_merge** … 再構成不要
4. **logical_rows** … ヘッダー（extract_row=0）を除くデータ行。各行は **cells** 配列（列数は表に合わせる。文字は読んだまま）。
5. **horizontal_merges** … [{{"row_index": 0, "start_col": 1, "colspan": 3}}]（横結合が無ければ []）

## 禁止
- 入力に無い文字を創作しない
- 縦結合を解くとき、結合範囲外の行に同じ左文字を繰り返さない（block_common を除く）
- no_merge のとき logical_rows は []
- **logical_rows の1セルに ` / ` で複数項目を連結しない**（改行で分かれていた項目は別 logical_rows に分ける）

## 出力（JSON のみ）

```json
{{
  "layout_kind": "full_resolution",
  "confidence": 0.9,
  "extract_header_row": 0,
  "header_cells": null,
  "correspondence_summary": "行0は見出し。列0-1は縦積み、列2以降は行ごとに項目が並ぶ。",
  "rationale": "要約1文",
  "vertical_merges": [
    {{"description": "列0が2 extract 行を跨ぐ", "anchor_col": 0, "rowspan": 2, "source_extract_rows": [1,2]}}
  ],
  "horizontal_merges": [],
  "left_block": null,
  "logical_rows": [
    {{"cells": ["ラベルA", "100", "項目1", "200", "10"]}},
    {{"cells": ["ラベルB", "50", "項目2", "180", "9"]}}
  ]
}}
```
"""

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(G36_LR_VERTICAL_AI_MODEL)
        response = model.generate_content(prompt, request_options={"timeout": 120})
        raw_text = getattr(response, "text", None) or ""
        logger.info(f"[G36-AI] merged_cell GENERATION\n{raw_text[:14000]}")
        parsed = json.loads(_extract_json(raw_text))
    except json.JSONDecodeError as e:
        raise G36LRVerticalAIError(f"g36_ai_json_parse_failed: {e}") from e
    except G36LRVerticalAIError:
        raise
    except Exception as e:
        raise G36LRVerticalAIError(f"g36_ai_call_failed: {e}") from e

    out = _parse_ai_correspondence(parsed)

    try:
        from dms.common.ai_cost_logger import log_ai_usage

        usage_meta = getattr(response, "usage_metadata", None)
        pt = getattr(usage_meta, "prompt_token_count", 0) or 0 if usage_meta else 0
        ct = getattr(usage_meta, "candidates_token_count", 0) or 0 if usage_meta else 0
        tt = getattr(usage_meta, "thoughts_token_count", 0) or 0 if usage_meta else 0
        tot = getattr(usage_meta, "total_token_count", 0) or 0 if usage_meta else 0
        log_ai_usage(
            app="dms-pipeline",
            stage="F51-MERGED-CELL",
            model=G36_LR_VERTICAL_AI_MODEL,
            prompt_token_count=pt,
            candidates_token_count=ct,
            thoughts_token_count=tt,
            total_token_count=int(tot or (pt + ct + tt) or 1),
            session_id=document_id,
        )
    except Exception as e:
        logger.warning(f"[G36-AI] cost log failed: {e}")

    logger.info(
        f"[G36-AI] kind={out['layout_kind']} logical_rows={len(out.get('logical_rows') or [])} "
        f"v_merges={len(out.get('vertical_merges') or [])} h_merges={len(out.get('horizontal_merges') or [])}"
    )
    return out


def _pad_row(cells: List[str], width: int) -> List[str]:
    row = list(cells)
    while len(row) < width:
        row.append("")
    return row[:width]


def rebuild_grid_from_ai_correspondence(
    data: List[List[Any]],
    ai: Dict[str, Any],
    *,
    header_rows: int = 1,
) -> Tuple[List[List[Any]], List[RowMergeMeta], Dict[str, Any]]:
    """AI の logical_rows から格子を組み立て、横結合メタを返す。"""
    if ai.get("layout_ai_contract") != G36_LR_VERTICAL_AI_CONTRACT:
        raise G36LRVerticalAIError("g36_rebuild_contract_mismatch")

    kind = ai.get("layout_kind")
    if kind in _NO_MERGE_KINDS:
        raise G36LRVerticalAIError("g36_rebuild_called_for_no_merge")

    logical: List[List[str]] = ai.get("logical_rows") or []
    if not logical:
        raise G36LRVerticalAIError("g36_rebuild_empty_logical_rows")

    ehr = ai.get("extract_header_row")
    header_cells: List[str] = []
    if ehr is None:
        hc = ai.get("header_cells")
        if hc is None:
            raise G36LRVerticalAIError("g36_rebuild_missing_header_cells")
        header_cells = list(hc)
    elif isinstance(ehr, int) and ehr >= 0:
        if ai.get("header_cells") is not None:
            header_cells = list(ai["header_cells"])
        elif ehr < len(data):
            header_cells = [_cell_str(c) for c in data[ehr]]
        else:
            raise G36LRVerticalAIError(f"g36_rebuild_extract_header_row_oob: {ehr}")
    else:
        raise G36LRVerticalAIError("g36_rebuild_invalid_extract_header_row")

    ncols = max(len(header_cells), max((len(r) for r in logical), default=0), 1)
    has_header = bool(header_cells) and any(_cell_str(c) for c in header_cells)
    header: List[List[Any]] = [_pad_row(header_cells, ncols)] if has_header else []

    body: List[List[Any]] = []
    left_block = ai.get("left_block") if kind == "block_common" else None

    for i, cells in enumerate(logical):
        row = _pad_row(cells, ncols)
        if kind == "block_common" and isinstance(left_block, dict):
            if i == 0 and ncols >= 2:
                row[0] = left_block.get("col0_item", row[0])
                row[1] = left_block.get("col1_amount", row[1])
            elif ncols >= 2:
                row[0] = ""
                row[1] = ""
        body.append(row)

    grid = header + body
    h_merges: List[RowMergeMeta] = list(ai.get("horizontal_merges") or [])
    if has_header:
        layout_meta = {
            "header_rows": [0],
            "data_start_row": 1,
            "column_headers": list(header[0]),
            "row_label_col": 0 if ncols > 1 else None,
        }
    else:
        layout_meta = {
            "header_rows": [],
            "data_start_row": 0,
            "column_headers": [],
            "row_label_col": 0 if ncols > 1 else None,
        }
    return grid, h_merges, layout_meta
