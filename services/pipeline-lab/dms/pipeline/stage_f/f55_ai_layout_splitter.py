"""
F55 補助: LLM が 2D 表から row_split / col_split を提案する。

コスト最適化前に「能力の天井」を測るため、**行数・列数・セル文字数の上限は設けない**（全行・全列・全文をプロンプトに載せる）。
モデル名は **常に** ``gemini-2.5-flash-lite`` のみ（変更不可）。

有効化: 環境変数 ``DMS_F55_AI_LAYOUT`` を ``1`` / ``true`` / ``yes``（大文字小文字無視）。
``GOOGLE_AI_API_KEY`` が無い場合は何もしない。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from loguru import logger

F55_AI_LAYOUT_MODEL = "gemini-2.5-flash-lite"
F55_LAYOUT_AI_CONTRACT = "f55_layout_ai_v1"
# 初回 + 再生成1回のみ（JSON 壊れ・境界ミスなど修正指示が意味を持つときだけ）
F55_AI_MAX_ATTEMPTS = 2

_F55_NON_RETRIABLE_MARKERS = (
    "f55_col_split_forbidden:",
    "f55_col_split_unnecessary:",
    "GOOGLE_AI_API_KEY_missing",
    "f55_col_split_header_row_invalid",
)

_F55_RETRIABLE_MARKERS = (
    "f55_col_blocks_overlap:",
    "f55_col_split_boundary_mismatch:",
    "f55_ai_normalize_failed",
    "f55_ai_json_parse_failed",
)


class F55LayoutAIRequiredError(RuntimeError):
    """F55: レイアウト理解（LLM）が必須だが契約を満たせない。"""


def _f55_layout_error_retriable(message: str) -> bool:
    """再プロンプトで直る見込みがあるエラーのみ True（構造違反は False）。"""
    if any(m in message for m in _F55_NON_RETRIABLE_MARKERS):
        return False
    if message == "f55_ai_normalize_failed" or any(
        m in message for m in _F55_RETRIABLE_MARKERS
    ):
        return True
    return False


def _ai_layout_env_enabled() -> bool:
    v = os.environ.get("DMS_F55_AI_LAYOUT", "").strip().lower()
    return v in ("1", "true", "yes")


def _extract_json(text: str) -> str:
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        return text[start:end].strip()
    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        return text[start:end].strip()
    return text.strip()


def _cell_one_line(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("\r", "").replace("\n", " ")


def _header_col_split_hint(table: List[List]) -> Optional[str]:
    """行0の大見出し列位置から、列分割時に満たすべき境界をプロンプトへ明示する。"""
    if not table:
        return None
    header = table[0]
    if not isinstance(header, (list, tuple)):
        return None
    majors = [i for i, c in enumerate(header) if _cell_one_line(c)]
    if len(majors) < 2:
        return None
    ncols = max((len(r) for r in table if isinstance(r, (list, tuple))), default=0)
    if ncols < 2:
        return None
    s1_end = majors[1] - 1
    s2_start = majors[1]
    return (
        f"行0の大見出し列インデックス: {majors}。"
        f"列分割する場合は **必ず** "
        f"block0={{'start': 0, 'end': {s1_end}}}, "
        f"block1={{'start': {s2_start}, 'end': {ncols - 1}}} "
        f"（block0 に列{s2_start}以降を含めない）。"
    )


def _validate_col_split_narrow_table(ncols: int, blocks: List[Dict[str, int]]) -> None:
    """3列以下の表を列分割して複数ブロックにするのは契約違反（1表のまま読む）。"""
    if ncols <= 3 and len(blocks) >= 2:
        raise F55LayoutAIRequiredError(
            f"f55_col_split_unnecessary: ncols={ncols} keep single table"
        )


def _promote_leading_label_column_to_common_left(
    table: List[List],
    detection: Dict[str, Any],
) -> Dict[str, Any]:
    """
    列0が複数データ行の行ラベル（日付など）で、ブロック0が列0を含むとき col_common_left=[0] に昇格する。
    見出し・セル内容は変えず、分割インデックスだけ直す。
    """
    if not detection.get("col_split") or not table:
        return detection
    if detection.get("col_common_left"):
        return detection
    blocks = sorted(detection["col_blocks"], key=lambda x: x["start"])
    if not blocks or int(blocks[0].get("start", -1)) != 0:
        return detection
    if int(blocks[0].get("end", -1)) < 1:
        return detection
    body = table[1:]
    col0_labels = sum(
        1
        for r in body
        if isinstance(r, (list, tuple)) and r and str(r[0] or "").strip()
    )
    if col0_labels < 2:
        return detection
    new_blocks: List[Dict[str, int]] = []
    for b in blocks:
        s, e = int(b["start"]), int(b["end"])
        if s == 0:
            s = 1
        new_blocks.append({"start": s, "end": e})
    out = dict(detection)
    out["col_common_left"] = [0]
    out["col_blocks"] = new_blocks
    return out


def _validate_col_split_blocks(
    table: List[List],
    blocks: List[Dict[str, int]],
) -> None:
    """
    ヘッダー行の大見出し列位置と col_blocks が一致するか検証（不一致なら契約違反で失敗）。
    """
    if len(blocks) != 2 or not table:
        return
    header = table[0]
    if not isinstance(header, (list, tuple)):
        raise F55LayoutAIRequiredError("f55_col_split_header_row_invalid")
    non_empty = [i for i, c in enumerate(header) if _cell_one_line(c)]
    if len(non_empty) < 2:
        return
    section1_end = non_empty[1] - 1
    b0, b1 = blocks[0], blocks[1]
    if b0["end"] != section1_end:
        raise F55LayoutAIRequiredError(
            f"f55_col_split_boundary_mismatch: block0 end={b0['end']} "
            f"expected {section1_end} (header title cols={non_empty})"
        )
    if b1["start"] != section1_end + 1:
        raise F55LayoutAIRequiredError(
            f"f55_col_split_boundary_mismatch: block1 start={b1['start']} "
            f"expected {section1_end + 1}"
        )


def _grid_preview(table: List[List]) -> str:
    """表全体を省略せずテキスト化（コスト無視フェーズ用）。"""
    lines: List[str] = []
    for i, row in enumerate(table):
        if not isinstance(row, (list, tuple)):
            lines.append(f"行{i}: []")
            continue
        cells = [_cell_one_line(row[c]) if c < len(row) else "" for c in range(len(row))]
        lines.append(f"行{i}: {cells}")
    return "\n".join(lines)


def _normalize_detection(raw: Dict[str, Any], *, nrows: int, ncols: int) -> Optional[Dict[str, Any]]:
    rs = bool(raw.get("row_split"))
    cs = bool(raw.get("col_split"))
    if ncols <= 3 and cs:
        raise F55LayoutAIRequiredError(
            f"f55_col_split_forbidden: ncols={ncols} col_split must be false"
        )
    if not rs and not cs:
        return {
            "row_split": False,
            "row_blocks": None,
            "row_common_top": None,
            "row_common_bottom": None,
            "col_split": False,
            "col_blocks": None,
            "col_common_left": None,
            "col_common_right": None,
        }
    if rs and cs:
        return None

    def _semantics_required(n_blocks: int) -> Optional[Dict[str, Any]]:
        intent = raw.get("whole_table_intent")
        summaries = raw.get("block_summaries")
        if not isinstance(intent, str) or not intent.strip():
            logger.info("[F55 AI] whole_table_intent 欠落 → 棄却")
            return None
        if (
            not isinstance(summaries, list)
            or len(summaries) != n_blocks
            or not all(isinstance(s, str) and s.strip() for s in summaries)
        ):
            logger.info("[F55 AI] block_summaries がブロック数と一致しない → 棄却")
            return None
        return {
            "ai_whole_table_intent": intent.strip(),
            "ai_block_summaries": [str(s).strip() for s in summaries],
        }

    if rs:
        blocks = raw.get("row_blocks")
        if not isinstance(blocks, list) or len(blocks) < 2:
            return None
        norm: List[Dict[str, int]] = []
        for b in blocks:
            if not isinstance(b, dict):
                return None
            try:
                s, e = int(b["start"]), int(b["end"])
            except (KeyError, TypeError, ValueError):
                return None
            if s < 0 or e >= nrows or s > e:
                return None
            norm.append({"start": s, "end": e})
        norm.sort(key=lambda x: x["start"])
        for i in range(len(norm) - 1):
            if norm[i]["end"] >= norm[i + 1]["start"]:
                return None
        ct = raw.get("row_common_top") or []
        cb = raw.get("row_common_bottom") or []
        if not isinstance(ct, list) or not isinstance(cb, list):
            return None
        if not all(isinstance(x, int) and 0 <= x < nrows for x in ct + cb):
            return None
        sem = _semantics_required(len(norm))
        if sem is None:
            return None
        return {
            "row_split": True,
            "row_blocks": norm,
            "row_common_top": ct,
            "row_common_bottom": cb,
            "col_split": False,
            "col_blocks": None,
            "col_common_left": None,
            "col_common_right": None,
            **sem,
        }

    blocks = raw.get("col_blocks")
    if not isinstance(blocks, list) or len(blocks) < 2:
        return None
    norm = []
    for b in blocks:
        if not isinstance(b, dict):
            return None
        try:
            s, e = int(b["start"]), int(b["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if s < 0 or e >= ncols or s > e:
            return None
        norm.append({"start": s, "end": e})
    norm.sort(key=lambda x: x["start"])
    for i in range(len(norm) - 1):
        if norm[i]["end"] >= norm[i + 1]["start"]:
            raise F55LayoutAIRequiredError(
                f"f55_col_blocks_overlap: {norm[i]!r} and {norm[i + 1]!r} "
                f"(列 {norm[i]['end']} と {norm[i + 1]['start']} が重複)"
            )
    cl = raw.get("col_common_left") or []
    cr = raw.get("col_common_right") or []
    if not isinstance(cl, list) or not isinstance(cr, list):
        return None
    if not all(isinstance(x, int) and 0 <= x < ncols for x in cl + cr):
        return None
    sem = _semantics_required(len(norm))
    if sem is None:
        return None
    return {
        "row_split": False,
        "row_blocks": None,
        "row_common_top": None,
        "row_common_bottom": None,
        "col_split": True,
        "col_blocks": norm,
        "col_common_left": cl,
        "col_common_right": cr,
        **sem,
    }


def suggest_ai_table_split(
    table: List[List],
    *,
    document_id: Optional[str] = None,
    require: bool = False,
    layout_context: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not require and not _ai_layout_env_enabled():
        return None
    from dms.common.gemini_studio_key import google_ai_studio_api_key

    api_key = (google_ai_studio_api_key() or os.environ.get("GOOGLE_AI_API_KEY") or "").strip()
    if not api_key:
        if require:
            raise F55LayoutAIRequiredError("GOOGLE_AI_API_KEY_missing")
        logger.debug("[F55 AI] skip: GOOGLE_AI_API_KEY unset")
        return None

    nrows = len(table)
    ncols = max((len(r) for r in table), default=0)

    preview = _grid_preview(table)
    ctx_block = ""
    if layout_context and str(layout_context).strip():
        ctx_block = f"\n## 上流の表読解（F50/F51）\n{layout_context.strip()}\n"
    header_hint = _header_col_split_hint(table)
    header_block = ""
    if header_hint:
        header_block = f"\n## ヘッダー行から見える列ブロック境界\n{header_hint}\n"

    if ncols <= 3:
        prompt_base = f"""あなたは表レイアウトのアナリストです。次の2次元表（行0が上）を読み、**分割は行わない**前提で表全体の意図を述べてください。
{ctx_block}

## 表のサイズ
- 行数: {nrows}（行インデックス 0 〜 {nrows - 1}）
- 最大列数: {ncols}（列インデックス 0 〜 {ncols - 1}）

## 表データ（全セル・省略なし）
{preview}

## ルール（3列以下・固定）
- **row_split は false、col_split は false のみ許可**（列分割・行分割は禁止）。
- `whole_table_intent` のみ必須（日本語1〜2文）。`block_summaries` は空配列 `[]` でよい。
- 名簿・役職・クラス対照など、少数列で1表として読めるレイアウトです。

## 出力（JSON のみ）

```json
{{
  "row_split": false,
  "col_split": false,
  "row_blocks": null,
  "row_common_top": [],
  "row_common_bottom": [],
  "col_blocks": null,
  "col_common_left": [],
  "col_common_right": [],
  "whole_table_intent": "…",
  "block_summaries": [],
  "reason": "3列以下のため分割しない"
}}
```
"""
    else:
        prompt_base = f"""あなたは表レイアウトのアナリストです。次の2次元表（行0が上）を見て、
論理的に独立したサブ表へ分割するなら **行方向** か **列方向** のどちらで切るべきか決めてください。
{ctx_block}{header_block}

## 表のサイズ
- 行数: {nrows}（行インデックス 0 〜 {nrows - 1}）
- 最大列数: {ncols}（列インデックス 0 〜 {ncols - 1}）

## 表データ（全セル・省略なし）
{preview}

## ルール
- **row_split** と **col_split** のうち、どちらか一方だけ true。分割不要なら両方 false（その場合は本 JSON では意味欄も不要）。
- 複数ブロックに分けるときは **2個以上**のブロックを返す。
- row_split: 各ブロックは {{start, end}} の行範囲。ブロックは重ならず、行順に並ぶ。
- col_split: 各ブロックは {{start, end}} の列範囲（**両端含む・閉区間**）。ブロック間で列インデックスを**共有しない**（例: 0–1 と 2–4 は可、0–2 と 2–5 は不可）。
- **列分割**するときは、見出し行の大ブロック境界で切る。**ラベル列とその金額列を別ブロックに分けない**（片方だけの orphan 表にしない）。
- 縦結合を解消した直後など、**1表のまま読める**なら col_split=false を選んでよい。
- 全表が一塊でよいなら row_split=false, col_split=false。
- 先頭の共通行・共通列を各ブロックに複製する必要があるときだけ row_common_top / row_common_bottom / col_common_left / col_common_right を数値インデックスの配列で返す（通常は空配列）。
- **行ラベル列**（日付・曜日などが縦に続く列0）を各ブロックで共有する場合は `col_common_left: [0]` とし、列ブロックの start は 1 以上にする（列0をブロックに含めない）。
- **3列以下**の表は通常 col_split=false（名簿・役職表など）。
- **分割する場合は必須**: `whole_table_intent`（表全体が何のための表か、日本語1〜2文）、`block_summaries`（各ブロックが何を表すか、**ブロック数と同じ長さ**の日本語短文の配列）。セル内容から判断すること。

## 出力（JSON のみ）

```json
{{
  "row_split": false,
  "col_split": true,
  "row_blocks": null,
  "row_common_top": [],
  "row_common_bottom": [],
  "col_blocks": [{{"start": 0, "end": 2}}, {{"start": 3, "end": 5}}],
  "col_common_left": [],
  "col_common_right": [],
  "whole_table_intent": "学校の…をまとめた表である。",
  "block_summaries": ["4月分の…", "5月分の…"],
  "reason": "任意。補足一行"
}}
```
"""

    last_err: Optional[str] = None
    out: Optional[Dict[str, Any]] = None
    response = None
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(F55_AI_LAYOUT_MODEL)
        for attempt in range(F55_AI_MAX_ATTEMPTS):
            prompt = prompt_base
            if last_err:
                prompt += f"\n## 修正必須（前回の契約違反）\n{last_err}\n"
            response = model.generate_content(prompt, request_options={"timeout": 120})
            raw_text = getattr(response, "text", None) or ""
            logger.info(f"[F55 AI] GENERATION attempt={attempt + 1}\n{raw_text[:8000]}")
            try:
                parsed = json.loads(_extract_json(raw_text))
            except json.JSONDecodeError as exc:
                last_err = f"f55_ai_json_parse_failed: {exc}"
                if not _f55_layout_error_retriable(last_err) or attempt >= F55_AI_MAX_ATTEMPTS - 1:
                    if require:
                        raise F55LayoutAIRequiredError(last_err) from exc
                    return None
                continue
            try:
                candidate = _normalize_detection(parsed, nrows=nrows, ncols=ncols)
            except F55LayoutAIRequiredError as exc:
                last_err = str(exc)
                if not _f55_layout_error_retriable(last_err):
                    if require:
                        raise
                    return None
                if attempt >= F55_AI_MAX_ATTEMPTS - 1:
                    if require:
                        raise
                    return None
                continue
            if candidate is None:
                last_err = "f55_ai_normalize_failed"
                if attempt >= F55_AI_MAX_ATTEMPTS - 1:
                    if require:
                        raise F55LayoutAIRequiredError(last_err)
                    return None
                continue
            if candidate.get("col_split") and candidate.get("col_blocks"):
                candidate = _promote_leading_label_column_to_common_left(table, candidate)
                try:
                    _validate_col_split_narrow_table(ncols, candidate["col_blocks"])
                    _validate_col_split_blocks(table, candidate["col_blocks"])
                except F55LayoutAIRequiredError as exc:
                    last_err = str(exc)
                    if not _f55_layout_error_retriable(last_err):
                        if require:
                            raise
                        return None
                    if attempt >= F55_AI_MAX_ATTEMPTS - 1:
                        if require:
                            raise
                        return None
                    continue
            out = candidate
            break
        if out is None:
            if require:
                raise F55LayoutAIRequiredError(last_err or "f55_ai_normalize_failed")
            return None
        out["layout_ai_contract"] = F55_LAYOUT_AI_CONTRACT
        try:
            from dms.common.ai_cost_logger import log_ai_usage

            usage_meta = getattr(response, "usage_metadata", None)
            pt = getattr(usage_meta, "prompt_token_count", 0) or 0 if usage_meta else 0
            ct = getattr(usage_meta, "candidates_token_count", 0) or 0 if usage_meta else 0
            tt = getattr(usage_meta, "thoughts_token_count", 0) or 0 if usage_meta else 0
            tot = getattr(usage_meta, "total_token_count", 0) or 0 if usage_meta else 0
            tokens = int(tot or (pt + ct + tt) or 1)
            log_ai_usage(
                app="dms-pipeline",
                stage="F55-AI",
                model=F55_AI_LAYOUT_MODEL,
                prompt_token_count=pt,
                candidates_token_count=ct,
                thoughts_token_count=tt,
                total_token_count=tokens,
                session_id=document_id,
            )
        except Exception as _e:
            logger.warning(f"[F55 AI] cost log failed: {_e}")
        return out
    except F55LayoutAIRequiredError:
        raise
    except Exception as e:
        if require:
            raise F55LayoutAIRequiredError(f"f55_ai_call_failed: {e}") from e
        logger.warning(f"[F55 AI] failed: {e}")
        return None
