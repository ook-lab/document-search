"""
G26 ページ理解（統合）: D 罫線の意味 + 表ごとの列・行意味・分割方針を **1 回の LLM** で返す。

``stage_d_line_digest`` あり/なしとも **1 回の LLM**（罫線ありは lines + sub_tables、なしは sub_tables のみ）。
行・列スロットと分割候補 ID は **プログラムが列挙**し、LLM は意味・variant 選択・文面のみを返す（複雑な境界はレガシーの ``layout_split`` も可）。
G41 は ``g41_detection`` を機械適用するだけ（別 LLM なし）。
G62 は ``by_sub_table`` の意味推定を配置に使う。
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from dms.pipeline.stage_g.g41_ai_layout_splitter import (
    G41_LAYOUT_AI_CONTRACT,
    G41LayoutAIRequiredError,
    _normalize_detection as _normalize_layout_split,
    _promote_leading_label_column_to_common_left,
)
from dms.pipeline.stage_g.g26_line_semantics import (
    G26_LINE_SEMANTICS_CONTRACT,
    G26SemanticAIError,
    VALID_LINE_ROLES,
    _lines_preview,
    _structured_tables_preview,
    parse_line_semantics,
    parse_table_layout_plans,
)

G26_TABLE_UNDERSTANDING_CONTRACT = "g26_table_understanding_v1"

_MAX_CELL_CHARS = 80
_GEN_LOG_MAX = 24000

_SEM_TYPE_ALIASES: Dict[str, str] = {
    "financial_report": "other",
    "financial": "other",
    "finance": "other",
    "budget": "other",
    "ledger": "other",
    "income_expense": "other",
    "balance_sheet": "other",
    "report": "other",
    "calendar": "schedule",
    "timetable_schedule": "timetable",
    "class_schedule": "timetable",
    "member_list": "roster",
    "name_list": "roster",
}


def normalize_sem_type(raw: Any) -> str:
    t = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if t in _VALID_SEM_TYPES:
        return t
    return _SEM_TYPE_ALIASES.get(t, str(raw or "").strip())


def _f46_grid_row_limit(data_len: int) -> int:
    """
    G26 意味推定プロンプトに載せる最大行数。未設定なら **全行**（data_len）。
    異常に大きい表での暴走防止にだけ ``DMS_F46_MAX_GRID_ROWS`` を使う。
    """
    raw = os.environ.get("DMS_F46_MAX_GRID_ROWS", "").strip()
    if not raw:
        return int(data_len)
    try:
        cap = int(raw)
    except ValueError:
        return int(data_len)
    return min(int(data_len), max(1, cap))


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


def _is_auto_column_name_row(row: List[Any]) -> bool:
    non_empty_cells = [cell for cell in row if cell and str(cell).strip()]
    if not non_empty_cells:
        return False
    return all(re.match(r"^(Col|列)\d+$", str(cell).strip()) for cell in non_empty_cells)


def _strip_auto_column_rows(data: List[List[Any]]) -> List[List[Any]]:
    return [row for row in data if not _is_auto_column_name_row(row)]


def _truncate_cell(v: Any) -> str:
    s = "" if v is None else str(v).replace("\n", " ").strip()
    if len(s) > _MAX_CELL_CHARS:
        return s[:_MAX_CELL_CHARS] + "…"
    return s


def _grid_to_prompt_lines(data: List[List[Any]], *, max_rows: Optional[int] = None) -> str:
    """表をプロンプト用テキスト化。max_rows が None のときは `_f46_grid_row_limit`（通常は全行）。"""
    lines: List[str] = []
    limit = max_rows if max_rows is not None else _f46_grid_row_limit(len(data))
    n = min(len(data), limit)
    for i in range(n):
        row = data[i]
        cells = [_truncate_cell(c) for c in row]
        lines.append(f"行{i}: {cells}")
    if len(data) > n:
        lines.append(f"...（以降 {len(data) - n} 行省略）")
    return "\n".join(lines)


_VALID_SEM_TYPES = frozenset(
    {
        "timetable",
        "schedule",
        "homework",
        "checklist",
        "roster",
        "price",
        "financial_report",
        "results",
        "agenda",
        "contact",
        "other",
    }
)


_SUB_TABLES_KEY_CONTRACT = """
## sub_tables の JSON 契約（厳守）
- 各要素は **必ず** `"key"` 文字列を持つ。`table_id` だけのオブジェクトは **無効**。
- `key` は下記「必須 sub_tables」に書いた文字列と **完全一致**（例: `"P0_B1::"`。末尾の `::` を省略しない）。
- `sub_tables` の件数 = 必須 key の件数。1 key につきちょうど 1 オブジェクト。
- **`layout_variant_id` を必ず出力**（各表ブロックの直後に付く「分割候補 ID の一覧」から **1 つだけ**選ぶ。start/end などの境界数値は書かない）。
- **`row_analysis` / `col_analysis`** は **プロンプトに埋め込まれたスロット配列と同一の長さ・同一の row_index/col_index** を維持する。
  変更してよいのは `abstraction_level` / `common_type` / `confidence` のみ（行・列を増減しない）。
- `table_semantics` / `whole_table_intent` / `block_summaries` は **key オブジェクトの直下** に置く（下記スキーマ参照）。
- `table_semantics` には `type` と **`type_ja`（日本語）** が必須。`summary` だけでは不可。
- **フォールバック禁止**: `layout_variant_id` を省略して別経路に逃げない。
- **レガシー座標契約**（LLM が `layout_split` に start/end を書く）は、Worker で **`DMS_G26_LEGACY_LAYOUT_SPLIT=1` を明示した場合のみ**許可（自動では使わない）。
"""

_LAYOUT_SPLIT_PROMPT = """
## 分割（プログラムが列挙した layout_variant_id が正本・G41/G44 はこれを機械適用）
- **境界の数値（start/end）は出力しない**。必ず `layout_variant_id` で選ぶ。
- 分割する variant を選んだときは **必須**: `whole_table_intent`（日本語1〜2文）、`block_summaries`（**その variant の列／行ブロック数と同じ長さ**の短文配列）。
- `v_none`（分割なし）のときは `block_summaries` は [] でよい。
- row_split と col_split を同時に選ぶ variant は **用意しない**（どちらか一方の軸のみ）。

## 分割判断ルール（必ず確認）

### 列分割すべきケース：繰り返しブロック構造
**「ブロックラベル行 ＋ ブロック内サブヘッダー行 ＋ データ列群」が左右に2回以上繰り返される**場合 → **必ず列分割**。

ブロック構造の見分け方:
1. **サブヘッダー列グループの繰り返し**（最重要）: ヘッダー行に同じ列グループが繰り返されていれば分割する
   - 「日・曜日・予定」が4回繰り返し → 4ブロック分割（ブロックラベル行がなくてもよい）
   - 「朝・1・2・3・4・5・6」が2回繰り返し → 2ブロック分割
   - 「日・月・火・水・木・金・土」が複数回繰り返し → 月数分のブロック分割
2. 先頭行にブロックラベルがある場合（クラス名・月名・支店名など）はそれも参考にする
3. 各ブロックのデータ列数はほぼ等しい

分割点の求め方: 2番目のブロックラベルが列インデックス N にある → `v_col_{N-1}` を選ぶ。

**重要**: 列ヘッダーが空や自動列名（列1・列3・Col3 など）であっても、**クラス名・組名・月名が 2 か所以上ある時点で分割対象**。自動列名は「空」と同じ扱いをする。

**【絶対ルール】均等N分割（v_col_X_Y... 形式）はブロックラベルがちょうどN個ある時のみ使う**:
- ブロックラベルが **2個** → 必ず **2分割**（v_col_N）。均等3分割(v_col_X_Y)は **禁止**
- ブロックラベルが **3個** → 3分割 (v_col_X_Y)
- ブロックラベルが **4個** → 4分割 (v_col_X_Y_Z)
- `block_summaries` の要素数は選んだ variant のブロック数と **必ず一致**させる

**【誤り例】列数が均等に分割できても、ブロックラベルの数が優先**:
- 15列・'5A'(列1)・'5B'(列8) → ブロックラベル2個 → v_col_7（2分割）が正解
  - ❌ 誤: v_col_4_9（3均等分割、15÷3=5で割り切れるが、ラベル2個なので不可）
  - ✅ 正: v_col_7（'5B'が列8 → v_col_7）、block_summaries=["5Aクラス分", "5Bクラス分"]

例:
- 行0=['','5A','','','','','','','5B',...], 行1=['','朝','1','2','3','4','5','6','朝','1',...] → '5B'が列8 → `v_col_7`
- 行0=['列1','5A','列3','列4','列5','列6','列7','列8','5B','列10',...] （'列N'は自動列名＝空扱い） → '5A'が列1・'5B'が列8 → `v_col_7`（15列でも均等3分割は禁止）
- 行0=['','4月','','','','','','5月',...], 行1=['','日','月','火','水','木','金','土','日',...] → '5月'が列7 → `v_col_6`
- 行0=['','4月','','','','','','5月',...], 行1=['','1','2','3','4','5','6','7','1',...] → '5月'が列7 → `v_col_6`
- 行0=['科目','東京','','','','大阪','','',''], 行1=['','A','B','C','D','A','B','C','D'] → '大阪'が列5 → `v_col_4`
- 行0=['日','曜日','予定','日','曜日','予定','日','曜日','予定','日','曜日','予定'] → ブロックラベルなし、'日'が2回目=列3 → `v_col_2`

### 行分割すべきケース：繰り返しブロック構造
**「ブロックラベル行 ＋ データ行群」が上下に2回以上繰り返される**場合 → **必ず行分割**。
同じ列構成のヘッダー行が複数回登場し、その下にそれぞれデータ行が続く構造が典型。

### 上記いずれも当てはまらない場合のみ → `v_none`
"""

_G26_ANALYSIS_SEM_KEYS = frozenset({"abstraction_level", "common_type", "confidence"})
_G26_LAYOUT_VARIANT_MAX = 80


def _analysis_skeleton_rows(nrows: int) -> List[Dict[str, Any]]:
    return [
        {"row_index": i, "abstraction_level": "concrete_value", "common_type": "行"}
        for i in range(nrows)
    ]


def _analysis_skeleton_cols(ncols: int) -> List[Dict[str, Any]]:
    return [
        {"col_index": j, "abstraction_level": "concrete_value", "common_type": "列"}
        for j in range(ncols)
    ]


def _materialize_row_analysis(llm_rows: Any, nrows: int) -> List[Dict[str, Any]]:
    """プログラム側スロットを正とし、LLM の意味だけ row_index でマージする。"""
    out = _analysis_skeleton_rows(nrows)
    if not isinstance(llm_rows, list):
        return out
    for it in llm_rows:
        if not isinstance(it, dict):
            continue
        try:
            ix = int(it.get("row_index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= ix < nrows):
            continue
        for k in _G26_ANALYSIS_SEM_KEYS:
            if k in it and it[k] is not None:
                out[ix][k] = it[k]
    return out


def _materialize_col_analysis(llm_cols: Any, ncols: int) -> List[Dict[str, Any]]:
    out = _analysis_skeleton_cols(ncols)
    if not isinstance(llm_cols, list):
        return out
    for it in llm_cols:
        if not isinstance(it, dict):
            continue
        try:
            ix = int(it.get("col_index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= ix < ncols):
            continue
        for k in _G26_ANALYSIS_SEM_KEYS:
            if k in it and it[k] is not None:
                out[ix][k] = it[k]
    return out


def _layout_variant_entries(
    *,
    nrows: int,
    ncols: int,
    max_variants: int = _G26_LAYOUT_VARIANT_MAX,
) -> List[Tuple[str, Dict[str, Any], str]]:
    """
    (variant_id, layout_split の座標部分（intent/summaries なし）, 説明文)
    intent / block_summaries は LLM が別フィールドで渡し、合成時に付与する。
    """
    out: List[Tuple[str, Dict[str, Any], str]] = []

    def _geom(
        *,
        row_split: bool,
        col_split: bool,
        row_blocks: Any,
        col_blocks: Any,
    ) -> Dict[str, Any]:
        return {
            "row_split": row_split,
            "col_split": col_split,
            "row_blocks": row_blocks,
            "col_blocks": col_blocks,
            "row_common_top": [],
            "row_common_bottom": [],
            "col_common_left": [],
            "col_common_right": [],
        }

    # 2分割を先に列挙（セマンティック判断が均等分割より優先されるよう上位に配置）
    if ncols > 3:
        for cut in range(ncols - 1):
            if len(out) >= max_variants:
                break
            bid = f"v_col_{cut}"
            geom = _geom(
                row_split=False,
                col_split=True,
                row_blocks=None,
                col_blocks=[
                    {"start": 0, "end": cut},
                    {"start": cut + 1, "end": ncols - 1},
                ],
            )
            desc = (
                f"列で2分割（左ブロック＝列 0〜{cut}、右ブロック＝列 {cut + 1}〜{ncols - 1}）"
            )
            out.append((bid, geom, desc))
    # 均等 N 分割（N=3,4,...）: ブロックサイズが等しい場合のみ。2分割の後に配置
    if ncols > 3:
        for n_blocks in range(3, min(ncols // 2, 9) + 1):
            if len(out) >= max_variants:
                break
            if ncols % n_blocks != 0:
                continue
            block_size = ncols // n_blocks
            if block_size < 2:
                continue
            col_blocks = [
                {"start": i * block_size, "end": (i + 1) * block_size - 1}
                for i in range(n_blocks)
            ]
            cuts = [i * block_size - 1 for i in range(1, n_blocks)]
            bid = "v_col_" + "_".join(str(c) for c in cuts)
            desc = (
                f"列で{n_blocks}等分（各{block_size}列ずつ、合計{ncols}列）"
                f" — ブロック境界: {', '.join(f'列{c}と列{c+1}の間' for c in cuts)}"
            )
            geom = _geom(row_split=False, col_split=True, row_blocks=None, col_blocks=col_blocks)
            out.append((bid, geom, desc))
    if nrows >= 2:
        for cut in range(nrows - 1):
            if len(out) >= max_variants:
                break
            bid = f"v_row_{cut}"
            geom = _geom(
                row_split=True,
                col_split=False,
                row_blocks=[
                    {"start": 0, "end": cut},
                    {"start": cut + 1, "end": nrows - 1},
                ],
                col_blocks=None,
            )
            desc = (
                f"行で2分割（上ブロック＝行 0〜{cut}、下ブロック＝行 {cut + 1}〜{nrows - 1}）"
            )
            out.append((bid, geom, desc))
    # v_none は末尾（「分割しない」はデフォルトではなく明示的な選択として最後に置く）
    out.append(
        (
            "v_none",
            _geom(row_split=False, col_split=False, row_blocks=None, col_blocks=None),
            "分割しない（1つの表ブロックのまま）",
        )
    )
    return out[:max_variants]


def _layout_variant_catalog(*, nrows: int, ncols: int) -> Dict[str, Dict[str, Any]]:
    return {bid: geom for bid, geom, _ in _layout_variant_entries(nrows=nrows, ncols=ncols)}


def _layout_variant_prompt_lines_for_spec(spec: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"#### layout_variant_id 候補（key={spec['key']!r}・次から **1つだけ**選ぶ）",
    ]
    for bid, _, desc in _layout_variant_entries(nrows=spec["nrows"], ncols=spec["ncols"]):
        lines.append(f"- `{bid}` — {desc}")
    return "\n".join(lines)


def _legacy_layout_split_env_enabled() -> bool:
    """レガシー（LLM が layout_split に座標を書く）契約。環境で明示したときのみ。"""
    v = (os.environ.get("DMS_G26_LEGACY_LAYOUT_SPLIT") or "").strip().lower()
    return v in ("1", "true", "yes")


def _compose_layout_split_payload(
    parsed: Dict[str, Any],
    *,
    nrows: int,
    ncols: int,
) -> Dict[str, Any]:
    """
    正本は ``layout_variant_id``（カタログ由来の座標）。フォールバックで別案に逃げない。
    レガシー座標は ``DMS_G26_LEGACY_LAYOUT_SPLIT=1`` を明示したときのみ受理。
    """
    vid_raw = parsed.get("layout_variant_id")
    nested_ls = parsed.get("layout_split") if isinstance(parsed.get("layout_split"), dict) else {}
    catalog = _layout_variant_catalog(nrows=nrows, ncols=ncols)

    def _merge_sem(geom: Dict[str, Any]) -> Dict[str, Any]:
        ls = {**geom}
        wt = parsed.get("whole_table_intent") or nested_ls.get("whole_table_intent")
        bs = parsed.get("block_summaries") or nested_ls.get("block_summaries")
        ls["whole_table_intent"] = wt.strip() if isinstance(wt, str) else ""
        ls["block_summaries"] = list(bs) if isinstance(bs, list) else []
        return ls

    if isinstance(vid_raw, str) and vid_raw.strip():
        vid = vid_raw.strip()
        geom = catalog.get(vid)
        if geom is None:
            valid = ", ".join(sorted(catalog.keys()))
            raise ValueError(
                f"[G26] layout_variant_id={vid!r} が無効です（この表で許可: {valid}）"
            )
        parsed["layout_variant_id"] = vid
        return _merge_sem(geom)

    has_legacy_coords = bool(nested_ls.get("row_split") or nested_ls.get("col_split"))
    if has_legacy_coords:
        if not _legacy_layout_split_env_enabled():
            raise ValueError(
                "[G26] layout_variant_id が無いまま layout_split に分割座標があるのは禁止。"
                " layout_variant_id を返すか、レガシー契約なら Worker に "
                "DMS_G26_LEGACY_LAYOUT_SPLIT=1 を明示してください。"
            )
        logger.info("[G26] レガシー layout_split 座標（DMS_G26_LEGACY_LAYOUT_SPLIT 明示契約）")
        parsed["layout_variant_id"] = parsed.get("layout_variant_id") or "legacy_coords"
        return dict(nested_ls)

    raise ValueError(
        "[G26] layout_variant_id が必須です（分割なしなら v_none）。"
        " フォールバックで座標を推測しません。"
    )


def _layout_split_failure_detail(
    raw: Dict[str, Any],
    *,
    nrows: int,
    ncols: int,
) -> str:
    """normalize 失敗理由を LLM 再試行用に短文化。"""
    rs = bool(raw.get("row_split"))
    cs = bool(raw.get("col_split"))
    if rs and cs:
        return "row_split と col_split を同時に true にしない"
    if not rs and not cs:
        intent = raw.get("whole_table_intent")
        if not isinstance(intent, str) or not str(intent).strip():
            return "分割なしでも whole_table_intent が空"
        return "不明（分割なし指定だが g41_detection 化に失敗）"
    axis = "row" if rs else "col"
    blocks = raw.get(f"{axis}_blocks")
    if not isinstance(blocks, list) or len(blocks) < 2:
        return f"{axis}_split 時は {axis}_blocks を2件以上"
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            return f"{axis}_blocks[{i}] が dict でない"
        try:
            s, e = int(b["start"]), int(b["end"])
        except (KeyError, TypeError, ValueError):
            return f"{axis}_blocks[{i}] に start/end がない"
        limit = nrows if rs else ncols
        if s < 0 or e >= limit or s > e:
            return (
                f"{axis}_blocks[{i}] 範囲 {s}..{e} が表サイズ外 "
                f"({'nrows' if rs else 'ncols'}={limit})"
            )
    summaries = raw.get("block_summaries")
    if not isinstance(summaries, list) or len(summaries) != len(blocks):
        return (
            f"block_summaries の長さ {len(summaries) if isinstance(summaries, list) else '?'}"
            f" != ブロック数 {len(blocks)}"
        )
    if not all(isinstance(s, str) and s.strip() for s in summaries):
        return "block_summaries に空文字がある"
    intent = raw.get("whole_table_intent")
    if not isinstance(intent, str) or not intent.strip():
        return "whole_table_intent が空"
    return "座標・共通列インデックスが表サイズと不一致"


_SEM_TYPE_JA_DEFAULTS: Dict[str, str] = {
    "timetable": "時間割",
    "schedule": "予定・行事",
    "homework": "宿題",
    "checklist": "チェックリスト",
    "roster": "名簿",
    "price": "価格表",
    "financial_report": "収支報告",
    "results": "成績・結果",
    "agenda": "議題",
    "contact": "連絡",
    "other": "表",
}


def _extract_coord_blocks(val: Any) -> List[Dict[str, int]]:
    """start/end を持つブロックだけ抽出（LLM のネスト・余計キーを無視）。"""
    if isinstance(val, dict):
        if "start" in val and "end" in val:
            try:
                return [{"start": int(val["start"]), "end": int(val["end"])}]
            except (TypeError, ValueError):
                return []
        out: List[Dict[str, int]] = []
        for v in val.values():
            out.extend(_extract_coord_blocks(v))
        return out
    if not isinstance(val, list):
        return []
    out = []
    for b in val:
        out.extend(_extract_coord_blocks(b))
    return out


def _repair_layout_split_grid_alignment(ls: Dict[str, Any], *, nrows: int, ncols: int) -> None:
    """
    LLM 出力と抽出グリッドの典型的なズレだけを幾何で整える（インデックス・閉区間の契約は維持）。

    - 3 列以下での col_split は G41 正規化と同様に禁止。
    - start/end がグリッド外 → 0..n-1 にクランプ。
    - 共通行・列インデックスが範囲外 → 除去。
    """

    if ncols <= 3 and ls.get("col_split"):
        raise ValueError(
            f"[G26] layout_split: ncols={ncols} では col_split 禁止（表が狭すぎる）"
        )

    def _clamp_blocks(raw: Any, limit: int, label: str) -> Optional[List[Dict[str, int]]]:
        if raw is None:
            return None
        if not isinstance(raw, list):
            return None
        out: List[Dict[str, int]] = []
        for i, b in enumerate(raw):
            if not isinstance(b, dict):
                continue
            try:
                s, e = int(b["start"]), int(b["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if limit <= 0:
                continue
            orig_s, orig_e = s, e
            s = max(0, min(s, limit - 1))
            e = max(0, min(e, limit - 1))
            if orig_s != s or orig_e != e:
                logger.warning(
                    f"[G26] layout_split {label}[{i}] をグリッドに整合 "
                    f"({orig_s}..{orig_e} → {s}..{e}, limit=0..{limit - 1})"
                )
            if s <= e:
                out.append({"start": s, "end": e})
        out.sort(key=lambda x: x["start"])
        return out if out else None

    if ls.get("col_split"):
        ls["col_blocks"] = _clamp_blocks(ls.get("col_blocks"), ncols, "col_blocks")
    elif ls.get("col_blocks"):
        ls["col_blocks"] = None

    if ls.get("row_split"):
        ls["row_blocks"] = _clamp_blocks(ls.get("row_blocks"), nrows, "row_blocks")
    elif ls.get("row_blocks"):
        ls["row_blocks"] = None

    def _filter_ix(vals: List[int], limit: int, label: str) -> List[int]:
        bad = [x for x in vals if x < 0 or x >= limit]
        good = [x for x in vals if 0 <= x < limit]
        if bad:
            logger.warning(f"[G26] layout_split {label} から範囲外を除去: {bad}")
        return good

    ls["col_common_left"] = _filter_ix(list(ls.get("col_common_left") or []), ncols, "col_common_left")
    ls["col_common_right"] = _filter_ix(list(ls.get("col_common_right") or []), ncols, "col_common_right")
    ls["row_common_top"] = _filter_ix(list(ls.get("row_common_top") or []), nrows, "row_common_top")
    ls["row_common_bottom"] = _filter_ix(list(ls.get("row_common_bottom") or []), nrows, "row_common_bottom")

    if ls.get("col_split"):
        blocks = ls.get("col_blocks")
        if not isinstance(blocks, list) or len(blocks) < 2:
            raise ValueError(
                f"[G26] layout_split: col_split=True だが col_blocks が不足 "
                f"(got {blocks!r})"
            )

    if ls.get("row_split"):
        blocks = ls.get("row_blocks")
        if not isinstance(blocks, list) or len(blocks) < 2:
            raise ValueError(
                f"[G26] layout_split: row_split=True だが row_blocks が不足 "
                f"(got {blocks!r})"
            )


def _sanitize_layout_split(
    raw: Any,
    *,
    nrows: int,
    ncols: int,
) -> Dict[str, Any]:
    """LLM の layout_split を契約形に整える（意味の変更はしない。形だけ修復）。"""
    if not isinstance(raw, dict):
        raise ValueError("[G26] layout_split must be dict")
    ls = dict(raw)
    if bool(ls.get("row_split")) and bool(ls.get("col_split")):
        raise ValueError("[G26] layout_split: row_split と col_split を同時に true にしてはならない")

    col_blocks = _extract_coord_blocks(ls.get("col_blocks"))
    row_blocks = _extract_coord_blocks(ls.get("row_blocks"))
    if bool(ls.get("col_split")) and len(col_blocks) < 2 and len(row_blocks) >= 2:
        raise ValueError(
            "[G26] layout_split: col_split=True だが col_blocks が空で row_blocks にデータがある"
            "（軸の誤記・自動修正はしない）"
        )
    elif bool(ls.get("row_split")) and len(row_blocks) < 2 and len(col_blocks) >= 2:
        raise ValueError(
            "[G26] layout_split: row_split=True だが row_blocks が空で col_blocks にデータがある"
            "（軸の誤記・自動修正はしない）"
        )

    if bool(ls.get("col_split")) and len(col_blocks) >= 2:
        ls["col_blocks"] = col_blocks
        ls["row_split"] = False
        ls["row_blocks"] = None
    elif bool(ls.get("row_split")) and len(row_blocks) >= 2:
        ls["row_blocks"] = row_blocks
        ls["col_split"] = False
        ls["col_blocks"] = None
    else:
        ls["row_split"] = False
        ls["col_split"] = False
        ls["row_blocks"] = None
        ls["col_blocks"] = None

    cl = ls.get("col_common_left")
    cr = ls.get("col_common_right")
    ls["col_common_left"] = [int(x) for x in cl] if isinstance(cl, list) else []
    ls["col_common_right"] = [int(x) for x in cr] if isinstance(cr, list) else []
    ct = ls.get("row_common_top")
    cb = ls.get("row_common_bottom")
    ls["row_common_top"] = [int(x) for x in ct] if isinstance(ct, list) else []
    ls["row_common_bottom"] = [int(x) for x in cb] if isinstance(cb, list) else []

    _repair_layout_split_grid_alignment(ls, nrows=nrows, ncols=ncols)

    intent = ls.get("whole_table_intent")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("[G26] layout_split: whole_table_intent が空（AI が必須で返すフィールド）")
    ls["whole_table_intent"] = intent.strip()

    summaries = ls.get("block_summaries")
    if not isinstance(summaries, list):
        summaries = []
    n_blocks = 0
    if ls.get("col_split") and isinstance(ls.get("col_blocks"), list):
        n_blocks = len(ls["col_blocks"])
    elif ls.get("row_split") and isinstance(ls.get("row_blocks"), list):
        n_blocks = len(ls["row_blocks"])
    if n_blocks >= 2:
        clean = [str(s).strip() for s in summaries if isinstance(s, str) and str(s).strip()]
        if len(clean) != n_blocks:
            logger.warning(
                f"[G26] layout_split: block_summaries の件数 {len(clean)} != ブロック数 {n_blocks}"
                f" → 件数を合わせて続行"
            )
            if len(clean) > n_blocks:
                clean = clean[:n_blocks]
            else:
                # 不足分は空文字で補完（後段は使わない）
                clean = clean + [""] * (n_blocks - len(clean))
        ls["block_summaries"] = clean
    else:
        ls["block_summaries"] = []

    return ls


def _sanitize_table_semantics(sem: Any, *, key: str) -> Dict[str, Any]:
    if not isinstance(sem, dict):
        raise ValueError(f"[G26] table_semantics missing or invalid key={key!r}")
    out = dict(sem)
    raw_type = out.get("type")
    out["type"] = normalize_sem_type(raw_type)
    type_ja = str(out.get("type_ja") or "").strip()
    if not type_ja:
        for alt in ("summary", "label", "description", "name"):
            type_ja = str(out.get(alt) or "").strip()
            if type_ja:
                break
    if not type_ja:
        type_ja = _SEM_TYPE_JA_DEFAULTS.get(out["type"], "表")
    out["type_ja"] = type_ja
    return out


def _resolve_sub_table_key(
    item: Dict[str, Any],
    sub_specs: List[Dict[str, Any]],
) -> str:
    """LLM 出力の key / table_id を契約 key に正規化。"""
    raw_key = str(item.get("key") or "").strip()
    if raw_key:
        if any(s["key"] == raw_key for s in sub_specs):
            return raw_key
        tid = raw_key.rstrip(":")
        candidate = f"{tid}::"
        if any(s["key"] == candidate for s in sub_specs):
            logger.info(f"[G26] key 正規化: {raw_key!r} → {candidate!r}")
            return candidate

    tid = str(item.get("table_id") or item.get("tableId") or "").strip()
    if tid:
        candidate = tid if tid.endswith("::") else f"{tid}::"
        exact = [s for s in sub_specs if s["key"] == candidate]
        if len(exact) == 1:
            logger.info(f"[G26] table_id → key: {tid!r} → {candidate!r}")
            return candidate
        by_tid = [s for s in sub_specs if s["table_id"] == tid.rstrip(":")]
        if len(by_tid) == 1:
            logger.info(f"[G26] table_id 単一一致 → key={by_tid[0]['key']!r}")
            return by_tid[0]["key"]

    return ""



def _lines_skeleton(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """罫線スケルトン: AI が role / meaning だけ上書きする。line_id は変更禁止。"""
    return [
        {
            "line_id": ln["line_id"],
            "role": "（role を入れる）",
            "meaning": "（意味を入れる）",
            "confidence": 0.9,
        }
        for ln in lines
        if ln.get("line_id")
    ]


def _sub_tables_prefilled_template(
    sub_specs: List[Dict[str, Any]],
    lines: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """プロンプト用: AI が意味フィールドだけ埋めればよい事前充填 JSON テンプレート。

    key / row_analysis / col_analysis / lines のインデックス・配列長はすべて埋め込み済み。
    AI は role / meaning / abstraction_level / common_type / table_semantics などのみ上書き。
    """
    entries = []
    for spec in sub_specs:
        entries.append(
            {
                "key": spec["key"],  # 変更禁止
                "layout_variant_id": "v_none",  # 分割が必要な場合のみ変更
                "table_semantics": {
                    "type": "other",
                    "type_ja": "表",
                    "target": None,
                    "scope": None,
                    "date_range": None,
                    "confidence": 0.9,
                },
                "whole_table_intent": "（この表の目的を1文で記述）",
                "block_summaries": ["（v_none 以外の variant を選んだ場合、各ブロックの説明を1つずつ入れる）"],
                "row_analysis": _analysis_skeleton_rows(spec["nrows"]),
                "col_analysis": _analysis_skeleton_cols(spec["ncols"]),
            }
        )
    lines_value: Any = _lines_skeleton(lines) if lines else []
    return json.dumps(
        {"page_summary": "（ページ全体の要約を1文で）", "lines": lines_value, "sub_tables": entries},
        ensure_ascii=False,
        indent=2,
    )


def build_g41_detection_from_entry(
    entry: Dict[str, Any],
    table: List[List],
) -> Dict[str, Any]:
    """G32 推定結果から G44 用 detection を返す（契約済み）。"""
    det = entry.get("g41_detection")
    if isinstance(det, dict) and det.get("layout_ai_contract"):
        return det
    ls_raw = entry.get("layout_split")
    if not isinstance(ls_raw, dict):
        raise ValueError("[G26] layout_split or g41_detection required")
    nrows = len(table)
    ncols = max((len(r) for r in table if isinstance(r, (list, tuple))), default=0)
    ls = _sanitize_layout_split(ls_raw, nrows=nrows, ncols=ncols)
    entry["layout_split"] = ls

    def _try_normalize(split: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            return _normalize_layout_split(split, nrows=nrows, ncols=ncols), None
        except G41LayoutAIRequiredError as exc:
            return None, str(exc)

    normalized, norm_err = _try_normalize(ls)
    if normalized is None:
        detail = norm_err or _layout_split_failure_detail(ls, nrows=nrows, ncols=ncols)
        raise ValueError(
            f"[G26] layout_split invalid for table dimensions "
            f"(nrows={nrows} ncols={ncols}): {detail}"
        )
    out = _promote_leading_label_column_to_common_left(table, normalized)
    out["layout_ai_contract"] = G41_LAYOUT_AI_CONTRACT
    return out


def _coerce_analysis_list(val: Any, *, axis: str) -> List[Any]:
    """LLM が row_analysis: 10 のように件数だけ返す場合をリストに正規化。"""
    if isinstance(val, list):
        return list(val)
    if isinstance(val, int) and val >= 0:
        common = "行" if axis == "row" else "列"
        idx_key = "row_index" if axis == "row" else "col_index"
        return [
            {
                idx_key: i,
                "abstraction_level": "concrete_value",
                "common_type": common,
            }
            for i in range(val)
        ]
    return []


def _align_analysis_to_grid(
    entry: Dict[str, Any],
    data: List[List[Any]],
    *,
    col_indices: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """G44 分割後サブ表の行・列数に row/col_analysis を合わせる（親コピー時の寸法ずれ修復）。

    col_indices: サブ表の列 i が元表の何列目か（G44 列分割時に渡す）。
                 指定時は元表の col_analysis から正しい列を抽出して再マッピングする。
                 未指定時は従来どおり先頭 N 列に切り詰め／延長する。
    """
    out = deepcopy(entry)
    nrows = len(data)
    ncols = max((len(r) for r in data), default=0)
    ra = _coerce_analysis_list(out.get("row_analysis"), axis="row")
    ca = _coerce_analysis_list(out.get("col_analysis"), axis="col")
    if nrows:
        if len(ra) < nrows:
            while len(ra) < nrows:
                tpl = ra[-1] if ra else {
                    "row_index": len(ra),
                    "abstraction_level": "concrete_value",
                    "common_type": "行",
                }
                ra.append({**tpl, "row_index": len(ra)})
        else:
            ra = ra[:nrows]
        for i, item in enumerate(ra):
            if isinstance(item, dict):
                item["row_index"] = i
    if ncols:
        if col_indices and len(col_indices) == ncols:
            # 列分割サブ表: 元表の col_analysis から正しい列を取り出して再マッピング
            orig_by_idx = {
                int(item.get("col_index", -1)): item
                for item in ca
                if isinstance(item, dict)
            }
            _fallback = {"abstraction_level": "concrete_value", "common_type": "列"}
            new_ca: List[Dict[str, Any]] = []
            for new_i, orig_i in enumerate(col_indices):
                src = orig_by_idx.get(orig_i)
                item = dict(src) if src else dict(_fallback)
                item["col_index"] = new_i
                new_ca.append(item)
            ca = new_ca
        else:
            if len(ca) < ncols:
                while len(ca) < ncols:
                    tpl = ca[-1] if ca else {
                        "col_index": len(ca),
                        "abstraction_level": "concrete_value",
                        "common_type": "列",
                    }
                    ca.append({**tpl, "col_index": len(ca)})
            else:
                ca = ca[:ncols]
            for i, item in enumerate(ca):
                if isinstance(item, dict):
                    item["col_index"] = i
    else:
        ca = []
    out["row_analysis"] = ra
    out["col_analysis"] = ca
    return out


def propagate_semantics_to_sub_tables(
    semantic_inference: Dict[str, Any],
    e14_reconstructed: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """G44 分割後: 親表の G32 結果を各 sub_table キーへ複製し、グリッド寸法に合わせる。"""
    out = dict(semantic_inference)
    by = dict(out.get("by_sub_table") or {})
    for entry in e14_reconstructed:
        tid = str(entry.get("table_id") or "")
        parent_key = f"{tid}::"
        parent = by.get(parent_key)
        if not isinstance(parent, dict):
            continue
        for sub in entry.get("sub_tables") or []:
            if not isinstance(sub, dict):
                continue
            stid = str(sub.get("sub_table_id") or "")
            key = f"{tid}::{stid}" if stid else parent_key
            data = _strip_auto_column_rows(list(sub.get("data") or []))
            # 列分割サブ表: extract_col_* から元表の列インデックス列を復元し正しく再マッピング
            col_indices: Optional[List[int]] = None
            sub_meta = sub.get("metadata") or {}
            col_start = sub_meta.get("extract_col_start")
            col_end = sub_meta.get("extract_col_end")
            if col_start is not None and col_end is not None:
                common_left = list(sub_meta.get("extract_col_common_left") or [])
                col_indices = common_left + list(range(int(col_start), int(col_end) + 1))
            if key not in by:
                copied = deepcopy(parent)
                if data:
                    copied = _align_analysis_to_grid(copied, data, col_indices=col_indices)
                by[key] = copied
            elif data:
                by[key] = _align_analysis_to_grid(by[key], data, col_indices=col_indices)
    out["by_sub_table"] = by
    return out


def _f13_layout_hint_section(
    detection: Optional[Dict[str, Any]],
    *,
    sub_index: int,
    n_subs: int,
) -> str:
    """
    F55（AI レイアウト採用時）のメタを G32 プロンプト用テキストにする。
    block_summaries の件数がサブ表数と一致しないときは表全体の意図のみ（誤マッピング防止）。
    """
    if not detection or not isinstance(detection, dict):
        return ""
    intent = detection.get("ai_whole_table_intent")
    if not isinstance(intent, str) or not intent.strip():
        return ""
    block_line = ""
    summaries = detection.get("ai_block_summaries")
    if (
        isinstance(summaries, list)
        and len(summaries) == n_subs
        and 0 <= sub_index < len(summaries)
    ):
        bs = str(summaries[sub_index]).strip()
        if bs:
            block_line = f"- **このブロックの要約（G41 AI）**: {bs}\n"
    elif isinstance(summaries, list) and summaries:
        logger.warning(
            f"[G26] ai_block_summaries len={len(summaries)} != n_subs={n_subs} "
            "→ ブロック要約は省略（表全体の意図のみ渡す）"
        )
    return (
        "## G41 レイアウト判断メタ（仮説。グリッドと矛盾する場合はグリッドを優先）\n"
        f"- **表全体の意図（G41 AI）**: {intent.strip()}\n"
        f"{block_line}\n"
    )


def table_layout_plans_from_by_sub_table(
    structured_tables: List[Dict[str, Any]],
    by_sub_table: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """G26 ``layout_split`` から G45 用 ``table_layout_plans`` を機械導出（LLM 二重出力なし）。"""
    raw_plans: List[Dict[str, Any]] = []
    for i, st in enumerate(structured_tables):
        if not isinstance(st, dict):
            continue
        tid = str(st.get("table_id") or f"T{i}")
        key = f"{tid}::"
        entry = by_sub_table.get(key)
        if not isinstance(entry, dict):
            continue
        ls = entry.get("layout_split")
        if not isinstance(ls, dict):
            continue
        reason = str(ls.get("whole_table_intent") or entry.get("description") or "").strip()
        if not reason:
            reason = "G26 表理解に基づく分割"
        plan: Dict[str, Any] = {
            "table_index": i,
            "table_id": tid,
            "split_axis": "none",
            "reason": reason,
            "row_common_top": list(ls.get("row_common_top") or []),
            "row_common_bottom": list(ls.get("row_common_bottom") or []),
            "col_common_left": list(ls.get("col_common_left") or []),
            "col_common_right": list(ls.get("col_common_right") or []),
        }
        if ls.get("row_split"):
            blocks = ls.get("row_blocks")
            if isinstance(blocks, list) and len(blocks) >= 2:
                plan["split_axis"] = "row"
                plan["row_blocks"] = blocks
                raw_plans.append(plan)
                continue
        if ls.get("col_split"):
            blocks = ls.get("col_blocks")
            if isinstance(blocks, list) and len(blocks) >= 2:
                plan["split_axis"] = "col"
                plan["col_blocks"] = blocks
                raw_plans.append(plan)
                continue
        raw_plans.append(plan)
    return parse_table_layout_plans(raw_plans, n_tables=len(structured_tables))


def _validate_table_understanding_entry(
    parsed: Dict[str, Any],
    *,
    data: List[List[Any]],
    key: str,
) -> Dict[str, Any]:
    """単表 JSON を検証し g41_detection を付与。"""
    parsed["success"] = True
    nrows = len(data)
    ncols = max((len(r) for r in data), default=0)
    parsed["table_semantics"] = _sanitize_table_semantics(parsed.get("table_semantics"), key=key)
    sem = parsed["table_semantics"]
    if sem.get("type") not in _VALID_SEM_TYPES:
        raise ValueError(f"[G26] invalid table_semantics.type={sem.get('type')!r} key={key!r}")
    ls_payload = _compose_layout_split_payload(parsed, nrows=nrows, ncols=ncols)
    parsed["layout_split"] = _sanitize_layout_split(ls_payload, nrows=nrows, ncols=ncols)
    parsed["row_analysis"] = _materialize_row_analysis(parsed.get("row_analysis"), nrows)
    parsed["col_analysis"] = _materialize_col_analysis(parsed.get("col_analysis"), ncols)
    ra = parsed["row_analysis"]
    ca = parsed["col_analysis"]
    if len(ra) != nrows:
        raise ValueError(f"[G26] row_analysis length {len(ra)} != nrows {nrows} key={key!r}")
    if ncols == 0:
        if ca:
            raise ValueError(f"[G26] col_analysis must be empty when ncols=0 key={key!r}")
    elif len(ca) != ncols:
        raise ValueError(f"[G26] col_analysis length {len(ca)} != ncols {ncols} key={key!r}")
    if not isinstance(parsed.get("layout_split"), dict):
        raise ValueError(f"[G26] layout_split required key={key!r}")
    g41_det = build_g41_detection_from_entry(parsed, data)
    parsed["g41_detection"] = g41_det
    parsed["ai_whole_table_intent"] = g41_det.get("ai_whole_table_intent")
    parsed["ai_block_summaries"] = g41_det.get("ai_block_summaries")
    return parsed


class G26SemanticEstimator:
    """D 罫線 + 表セル内容から行・列の意味と分割方針を推定（配置・colspan は出さない）。"""

    def __init__(self, document_id: Optional[str] = None, model_name: str = "gemini-2.5-flash-lite"):
        self.document_id = document_id
        self.model_name = model_name
        import google.generativeai as genai

        api_key = os.environ.get("GOOGLE_AI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY is not set (G26 意味推定に必須)")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
        self._last_raw_text = ""
        logger.info(f"[G26] 意味推定モデル初期化: {model_name}")

    @staticmethod
    def _collect_sub_table_specs(
        e14_reconstructed: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        sub_specs: List[Dict[str, Any]] = []
        for entry in e14_reconstructed:
            table_id = str(entry.get("table_id") or "T")
            for sub in entry.get("sub_tables") or []:
                if not isinstance(sub, dict):
                    continue
                stid = str(sub.get("sub_table_id") or "")
                data = _strip_auto_column_rows(list(sub.get("data") or []))
                if not data:
                    continue
                key = f"{table_id}::{stid}" if stid else f"{table_id}::"
                sub_specs.append(
                    {
                        "key": key,
                        "table_id": table_id,
                        "sub_table_id": stid,
                        "data": data,
                        "nrows": len(data),
                        "ncols": max((len(r) for r in data), default=0),
                        "grid_text": _grid_to_prompt_lines(data),
                    }
                )
        return sub_specs

    @staticmethod
    def _required_sub_tables_prompt_block(sub_specs: List[Dict[str, Any]]) -> str:
        rows = ["## 必須 sub_tables（以下の key を **すべて** ちょうど 1 件ずつ）"]
        for spec in sub_specs:
            nv = len(_layout_variant_entries(nrows=spec["nrows"], ncols=spec["ncols"]))
            rows.append(
                f"- {spec['key']!r}: 行スロット {spec['nrows']} 件 / 列スロット {spec['ncols']} 件 / "
                f"layout_variant 候補は **{nv}** 個（表データの直後に一覧あり）。"
                f"row_index は 0..{spec['nrows'] - 1}、col_index は 0..{spec['ncols'] - 1} に固定。"
            )
        return "\n".join(rows)

    @staticmethod
    def _required_line_ids_prompt_block(lines: List[Dict[str, Any]]) -> str:
        ids = [str(ln["line_id"]) for ln in lines if ln.get("line_id")]
        if not ids:
            return ""
        return "## 必須 lines の line_id（すべて 1 件ずつ）\n" + ", ".join(ids)

    @staticmethod
    def _format_sub_specs_block(sub_specs: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for spec in sub_specs:
            st_label = spec["sub_table_id"] or "(単一)"
            slot_json = json.dumps(
                {
                    "row_analysis": _analysis_skeleton_rows(spec["nrows"]),
                    "col_analysis": _analysis_skeleton_cols(spec["ncols"]),
                },
                ensure_ascii=False,
                indent=2,
            )
            parts.append(
                f"### key={spec['key']!r} table_id={spec['table_id']!r} "
                f"sub_table_id={st_label!r} "
                f"rows={spec['nrows']} cols={spec['ncols']}\n"
                f"#### 行・列入力スロット（この row_index/col_index を維持し、意味フィールドだけ埋める）\n"
                f"```json\n{slot_json}\n```\n"
                f"{_layout_variant_prompt_lines_for_spec(spec)}\n"
                f"{spec['grid_text']}"
            )
        return "\n\n".join(parts)

    def _call_and_log_llm(self, prompt: str, *, stage_label: str) -> Tuple[int, Dict[str, int]]:
        gen_cfg: Dict[str, Any] = {"response_mime_type": "application/json"}
        try:
            response = self.model.generate_content(prompt, generation_config=gen_cfg)
        except TypeError:
            response = self.model.generate_content(prompt)
        raw = getattr(response, "text", None) or ""
        self._last_raw_text = raw
        body = raw
        if len(body) > _GEN_LOG_MAX:
            body = body[:_GEN_LOG_MAX] + f"\n... [{stage_label}] truncated ...\n"
        logger.info(f"[G26] GENERATION | {stage_label}\n{body}")

        usage_meta = getattr(response, "usage_metadata", None)
        pt = getattr(usage_meta, "prompt_token_count", 0) or 0 if usage_meta else 0
        ct = getattr(usage_meta, "candidates_token_count", 0) or 0 if usage_meta else 0
        tt = getattr(usage_meta, "thoughts_token_count", 0) or 0 if usage_meta else 0
        tot = getattr(usage_meta, "total_token_count", 0) or 0 if usage_meta else 0
        tokens = int(tot or (pt + ct + tt) or (max(len(prompt) + len(raw), 1) // 4))

        try:
            from dms.common.ai_cost_logger import log_ai_usage

            log_ai_usage(
                app="dms-pipeline",
                stage=stage_label,
                model=self.model_name,
                prompt_token_count=pt,
                candidates_token_count=ct,
                thoughts_token_count=tt,
                total_token_count=tokens,
                session_id=self.document_id,
            )
        except Exception as _e:
            logger.warning(f"[G26] cost log failed ({stage_label}): {_e}")

        return tokens, {"prompt": int(pt), "candidates": int(ct), "thoughts": int(tt)}

    @staticmethod
    def _parse_sub_tables_response(
        parsed: Dict[str, Any],
        sub_specs: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        raw_subs = parsed.get("sub_tables")
        if not isinstance(raw_subs, list):
            raise ValueError("[G26] sub_tables array required")
        by_key: Dict[str, Dict[str, Any]] = {}
        for item in raw_subs:
            if not isinstance(item, dict):
                raise ValueError("[G26] sub_tables item invalid")
            key = _resolve_sub_table_key(item, sub_specs)
            if not key:
                raise ValueError(
                    "[G26] sub_tables item missing key "
                    f"(table_id={item.get('table_id')!r}; use key like 'P0_B1::')"
                )
            spec = next((s for s in sub_specs if s["key"] == key), None)
            if spec is None:
                logger.warning(f"[G26] ignoring unknown sub_table key={key!r}")
                continue
            entry = {k: v for k, v in item.items() if k != "key"}
            by_key[key] = _validate_table_understanding_entry(
                entry, data=spec["data"], key=key
            )
        return by_key

    def infer_all(
        self,
        e14_reconstructed: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        chain_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], int, Dict[str, int]]:
        """
        Returns:
            (payload, tokens, usage_sums)
            payload = {
              "by_sub_table": { "<table_id>::<sub_table_id>": commonality_dict },
              "line_semantics_ai": {...},  # digest あり時のみ
              "model_name": str,
            }
            usage_sums = {"prompt": int, "candidates": int, "thoughts": int}

        chain_context:
            ``stage_d_line_digest`` があるとき D 罫線と表理解を **1 LLM** に統合。
            ``structured_tables`` は table_layout_plans 検証用。
            ``detections`` は G41 仮説メタ（罫線なしの per-table 経路のみ）。
        """
        if not e14_reconstructed:
            return (
                {"by_sub_table": {}, "model_name": self.model_name},
                0,
                {"prompt": 0, "candidates": 0, "thoughts": 0},
            )

        digest = (chain_context or {}).get("stage_d_line_digest")
        structured_tables = (chain_context or {}).get("structured_tables")
        if isinstance(digest, dict) and digest.get("available"):
            if digest.get("lines_truncated"):
                from dms.pipeline.stage_f.stage_d_line_digest import _MAX_LINES_PER_ORIENTATION

                raise G26SemanticAIError(
                    f"d_lines_truncated: exceeds cap per orientation ({_MAX_LINES_PER_ORIENTATION})"
                )
            return self._infer_page_unified(
                e14_reconstructed,
                digest,
                structured_tables=list(structured_tables or []),
                year_context=year_context,
                chain_context=chain_context,
            )

        structured_tables = (chain_context or {}).get("structured_tables")
        return self._infer_tables_batch(
            e14_reconstructed,
            structured_tables=list(structured_tables or []),
            year_context=year_context,
        )

    def _infer_tables_batch(
        self,
        e14_reconstructed: List[Dict[str, Any]],
        *,
        structured_tables: List[Dict[str, Any]],
        year_context: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], int, Dict[str, int]]:
        """罫線 digest なし: 全表を 1 回の LLM で理解（列・行・layout_split のみ）。"""
        sub_specs = self._collect_sub_table_specs(e14_reconstructed)
        if not sub_specs:
            raise RuntimeError("[G26] tables batch: no sub_tables with data")

        year_info = self._year_info(year_context)
        tables_block = self._format_sub_specs_block(sub_specs)
        st_preview = ""
        if structured_tables:
            st_preview = (
                f"\n## 構造化表\n{_structured_tables_preview(structured_tables)}\n"
            )

        prefilled = _sub_tables_prefilled_template(sub_specs)

        prompt = f"""あなたは表構造のアナリストです。**全セルを根拠に**各ブロックの列・行の意味と切り方を一括で決めてください。

{year_info}
{st_preview}
## 表データ（サブブロック単位）
{tables_block}

## 厳守
- ヘッダー行だけで決めず、データと列の共通性から逆算する。
- **分割は `layout_variant_id` で選ぶ**（各表ブロック直後の候補一覧のみ。start/end は書かない）。
{_SUB_TABLES_KEY_CONTRACT}
{_LAYOUT_SPLIT_PROMPT}
- table_layout_plans は出力しない。

## 出力指示（テンプレートを完成させて返す）

以下の JSON テンプレートを**そのまま**コピーし、意味フィールドのみ上書きして返すこと。

**変更してよいフィールド**:
- `page_summary`, `table_semantics`, `whole_table_intent`, `block_summaries`
- `layout_variant_id`（表の直後に列挙された候補から必ず1つを選ぶ。分割不要なら "v_none"）
- `row_analysis` の `abstraction_level` / `common_type`
- `col_analysis` の `abstraction_level` / `common_type`

**分割判断の指針**:
- 先頭行にクラス名・組名・月名・支店名などが **2か所以上** ある（空・自動列名「列N/ColN」を挟んでいてもよい）→ 列で分割（v_col_N）
- 左右に同じ構造の列グループが繰り返されている → 列で分割（v_col_N）
- 上下に同じ構造の行グループが繰り返されている → 行で分割（v_row_N）
- どちらでもなければ → v_none
- **均等N分割（v_col_X_Y...）はブロックラベルがN個の時のみ。ラベル2個なら2分割（v_col_N）、列数が均等でも禁止**

**絶対に変更禁止**:
- `key` の文字列（1文字も変えない・末尾の `::` を含め完全一致で保持）
- `row_analysis` の `row_index` と配列の長さ
- `col_analysis` の `col_index` と配列の長さ

```json
{prefilled}
```
"""

        total_tokens = 0
        usage_acc = {"prompt": 0, "candidates": 0, "thoughts": 0}
        by_key: Dict[str, Dict[str, Any]] = {}

        tokens, usage = self._call_and_log_llm(prompt, stage_label="G26-TABLES")
        total_tokens += tokens
        for k in usage_acc:
            usage_acc[k] += int(usage.get(k) or 0)
        parsed = json.loads(_extract_json(self._last_raw_text))
        ps = parsed.get("page_summary")
        if not isinstance(ps, str) or not ps.strip():
            raise G26SemanticAIError("g26_ai_missing_page_summary")
        partial = self._parse_sub_tables_response(parsed, sub_specs)
        by_key.update(partial)
        missing = [s["key"] for s in sub_specs if s["key"] not in by_key]
        if missing:
            raise RuntimeError(f"[G26] sub_tables 欠落 key: {missing}")

        out: Dict[str, Any] = {
            "by_sub_table": by_key,
            "model_name": self.model_name,
        }
        self._assert_expected_sub_table_keys(e14_reconstructed, by_key)
        logger.info(f"[G26] 表一括理解完了 tables={len(by_key)} tokens={total_tokens}")
        return out, total_tokens, usage_acc

    def _infer_page_unified(
        self,
        e14_reconstructed: List[Dict[str, Any]],
        digest: Dict[str, Any],
        *,
        structured_tables: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        chain_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], int, Dict[str, int]]:
        """D 罫線 + 全表理解を 1 回の LLM で返す。"""
        if not digest.get("available"):
            raise G26SemanticAIError("stage_d_line_digest_unavailable")

        lines = digest.get("lines") or []
        tables_meta = digest.get("tables") or []
        tables_json = json.dumps(
            [
                {
                    "table_index": i,
                    "table_id": t.get("table_id"),
                    "bbox_norm": t.get("bbox"),
                    "line_ids_near": t.get("line_ids_near"),
                }
                for i, t in enumerate(tables_meta)
                if isinstance(t, dict)
            ],
            ensure_ascii=False,
            indent=2,
        )
        st_preview = ""
        if structured_tables:
            st_preview = (
                f"\n## 構造化表（G24・セル文字）\n"
                f"{_structured_tables_preview(structured_tables)}\n"
            )

        sub_specs = self._collect_sub_table_specs(e14_reconstructed)
        if not sub_specs:
            raise RuntimeError("[G26] page unified: no sub_tables with data")

        tables_block = self._format_sub_specs_block(sub_specs)
        year_info = self._year_info(year_context)
        lines_preview = _lines_preview(lines) if lines else "（罫線なし）"
        req_lines = self._required_line_ids_prompt_block(lines)
        role_list = ", ".join(sorted(VALID_LINE_ROLES))
        type_list = ", ".join(sorted(_VALID_SEM_TYPES))
        prefilled = _sub_tables_prefilled_template(sub_specs, lines=lines)

        prompt_base = f"""あなたは PDF ページの表レイアウト解析器です。**罫線とセル内容を一体で読み**、各表ブロックの意味と切り方を決めてください。帳票種別名だけで決めないこと。

{year_info}
{req_lines}

## 罫線（正規化座標 0–1・表理解の根拠。罫線だけで分割を決めない）
{lines_preview}

## D 表領域メタ
{tables_json}
{st_preview}

## 表データ（サブブロック単位）
{tables_block}

## 出力要件
1. **lines**: テンプレートに全 line_id のスケルトンが **事前充填済み**。`role` と `meaning` だけ上書きすること。line_id と配列長は **変更禁止**。
   - role は次のいずれか **のみ**: {role_list}
2. **sub_tables**: テンプレートの全 key をそのまま返す。**分割は `layout_variant_id` のみ**（候補一覧から選択）
   - table_semantics.type は次のいずれか **のみ**: {type_list}
{_SUB_TABLES_KEY_CONTRACT}
{_LAYOUT_SPLIT_PROMPT}
3. **table_layout_plans は出力しない**

## 出力指示（テンプレートを完成させて返す）

以下の JSON テンプレートを**そのまま**コピーし、意味フィールドのみ上書きして返すこと。

**変更してよいフィールド（意味・内容）**:
- `page_summary`（ページ全体の要約）
- `lines`（罫線の role）
- `table_semantics`（type / type_ja / target / scope / date_range / confidence）
- `whole_table_intent`（表の目的の1文）
- `block_summaries`（分割時のブロック説明）
- `layout_variant_id`（表の直後に列挙された候補から必ず1つを選ぶ。分割不要なら "v_none"）
- `row_analysis` の `abstraction_level` / `common_type`
- `col_analysis` の `abstraction_level` / `common_type`

**分割判断の指針**:
- 先頭行にクラス名・組名・月名・支店名などが **2か所以上** ある（空・自動列名「列N/ColN」を挟んでいてもよい）→ 列で分割（v_col_N）
- 左右に同じ構造の列グループが繰り返されている → 列で分割（v_col_N）
- 上下に同じ構造の行グループが繰り返されている → 行で分割（v_row_N）
- どちらでもなければ → v_none
- **均等N分割（v_col_X_Y...）はブロックラベルがN個の時のみ。ラベル2個なら2分割（v_col_N）、列数が均等でも禁止**

**絶対に変更禁止**:
- `key` の文字列（1文字も変えない・末尾の `::` を含め完全一致で保持）
- `row_analysis` の `row_index` と配列の長さ
- `col_analysis` の `col_index` と配列の長さ

```json
{prefilled}
```
"""

        total_tokens = 0
        usage_acc = {"prompt": 0, "candidates": 0, "thoughts": 0}
        by_key: Dict[str, Dict[str, Any]] = {}
        page_summary = ""

        tokens, usage = self._call_and_log_llm(prompt_base, stage_label="G26-PAGE")
        total_tokens += tokens
        for k in usage_acc:
            usage_acc[k] += int(usage.get(k) or 0)
        parsed = json.loads(_extract_json(self._last_raw_text))
        ps = parsed.get("page_summary")
        if not isinstance(ps, str) or not ps.strip():
            raise G26SemanticAIError("g26_ai_missing_page_summary")
        page_summary = ps.strip()
        partial = self._parse_sub_tables_response(parsed, sub_specs)
        by_key.update(partial)
        missing = [s["key"] for s in sub_specs if s["key"] not in by_key]
        if missing:
            raise RuntimeError(f"[G26] sub_tables 欠落 key: {missing}")

        self._assert_expected_sub_table_keys(e14_reconstructed, by_key)

        out_lines: List[Dict[str, Any]] = []
        if lines:
            out_lines = parse_line_semantics(parsed, lines)

        plans = table_layout_plans_from_by_sub_table(structured_tables, by_key)
        line_semantics_ai = {
            "line_semantics_contract": G26_LINE_SEMANTICS_CONTRACT,
            "lines": out_lines,
            "page_summary": page_summary or "（要約なし）",
            "table_layout_plans": plans,
        }
        out: Dict[str, Any] = {
            "by_sub_table": by_key,
            "line_semantics_ai": line_semantics_ai,
            "model_name": self.model_name,
        }
        logger.info(
            f"[G26] ページ統合理解完了 lines={len(out_lines)} plans={len(plans)} "
            f"tables={len(by_key)} tokens={total_tokens}"
        )
        return out, total_tokens, usage_acc

    @staticmethod
    def _year_info(year_context: Optional[int]) -> str:
        from datetime import datetime

        if year_context:
            return f"\n**年度ヒント**: {year_context}年の文書です。\n"
        y = datetime.now().year
        return f"\n**年度情報**: 不明な場合は {y} 年を参考に。\n"

    @staticmethod
    def _assert_expected_sub_table_keys(
        e14_reconstructed: List[Dict[str, Any]],
        by_sub_table: Dict[str, Any],
    ) -> None:
        expected_keys: List[str] = []
        for entry in e14_reconstructed:
            tid = entry.get("table_id") or "T"
            for sub in entry.get("sub_tables") or []:
                data = _strip_auto_column_rows(list(sub.get("data") or []))
                if not data:
                    continue
                stid = str(sub.get("sub_table_id") or "")
                expected_keys.append(f"{tid}::{stid}" if stid else f"{tid}::")
        missing = [k for k in expected_keys if k not in by_sub_table]
        if missing:
            raise RuntimeError(f"[G26] semantic_inference incomplete; missing keys: {missing!r}")
