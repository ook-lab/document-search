"""結合セル内部の論理行（隠れ行）判定 — D マス確定後の第2段のみ。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from loguru import logger

G36_CELL_INTERIOR_AI_CONTRACT = "g36_cell_interior_split_v1"
G36_CELL_INTERIOR_AI_MODEL = "gemini-2.5-flash-lite"


class G36CellInteriorAIError(RuntimeError):
    pass


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


def judge_cell_interiors_ai(
    cells_in: List[Dict[str, Any]],
    *,
    document_id: Optional[str] = None,
) -> Dict[str, List[str]]:
    """
  結合セルごとに内部論理行を返す。

  Returns:
      {cell_id: [line0, line1, ...]}  — 1行のみなら長さ1
    """
    if not cells_in:
        return {}

    from dms.common.gemini_studio_key import google_ai_studio_api_key

    api_key = (google_ai_studio_api_key() or os.environ.get("GOOGLE_AI_API_KEY") or "").strip()
    if not api_key:
        raise G36CellInteriorAIError("GOOGLE_AI_API_KEY_missing")

    payload = []
    for c in cells_in:
        payload.append(
            {
                "cell_id": c.get("cell_id"),
                "row": c.get("row"),
                "col": c.get("col"),
                "rowspan": c.get("rowspan", 1),
                "colspan": c.get("colspan", 1),
                "assigned_text": c.get("assigned_text", ""),
                "word_lines": c.get("geometry_lines") or [],
            }
        )

    prompt = f"""あなたは表セルの読解者です。各セルは **Stage D の実線で囲まれた1マス**（結合セル含む）です。
**表種・見出し語で決めない。** 与えられた文字と word_lines（Y 座標の参考）だけで、**そのマス内に論理行が何段あるか** を判定してください。

## 入力セル
{json.dumps(payload, ensure_ascii=False, indent=2)}

## 規則
- **line_count = 1** … 1マス1段（改行・折り返しは同一行）
- **line_count > 1** … マス内に独立した複数行がある（例: 上「いす」下「出し」）
- 創作禁止。入力に無い語を足さない
- ` / ` で連結しない。段があるなら **lines** 配列に分ける
- geometry_lines が複数あるときは整合するよう lines を分ける

## 出力（JSON のみ）
```json
{{
  "contract": "{G36_CELL_INTERIOR_AI_CONTRACT}",
  "cells": [
    {{"cell_id": "R2C2", "line_count": 2, "lines": ["いす", "出し"]}}
  ]
}}
```
"""

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(G36_CELL_INTERIOR_AI_MODEL)
        response = model.generate_content(prompt, request_options={"timeout": 120})
        raw_text = getattr(response, "text", None) or ""
        logger.info(f"[G36-CELL-AI] interior\n{raw_text[:8000]}")
        parsed = json.loads(_extract_json(raw_text))
    except json.JSONDecodeError as e:
        raise G36CellInteriorAIError(f"g36_cell_interior_json_failed: {e}") from e

    if parsed.get("contract") != G36_CELL_INTERIOR_AI_CONTRACT:
        raise G36CellInteriorAIError("g36_cell_interior_contract_missing")

    out: Dict[str, List[str]] = {}
    for item in parsed.get("cells") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("cell_id") or "")
        lines = item.get("lines")
        if not cid or not isinstance(lines, list) or not lines:
            continue
        cleaned = [str(x).strip() for x in lines if str(x).strip()]
        if cleaned:
            out[cid] = cleaned
    return out
