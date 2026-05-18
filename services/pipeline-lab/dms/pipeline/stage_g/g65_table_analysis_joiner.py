"""
F59: Table Analysis Joiner（配置後の論理表結合）

結合は **物理的に続いている同一表の行分割** に限る（G62 multi_section が付けた
`f58_row_split_sequence` + `f58_row_split_base_id` が揃った `*_S1..Sn` のみ）。

行わないこと:
- 列幅や表種（時間割・名簿など）が似ているだけで縦結合しない
- 別クラス・別ブロックの時間割を 1 本にまとめない（別 `table_id` のまま）
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

_SUFFIX_RE = re.compile(r"^(?P<base>.+)_S(?P<n>\d+)$")


def _analysis_top_metadata(a: Dict[str, Any]) -> Dict[str, Any]:
    md = a.get("metadata")
    if isinstance(md, dict) and md:
        return md
    secs = a.get("sections") or []
    if secs and isinstance(secs[0], dict):
        inner = secs[0].get("metadata")
        if isinstance(inner, dict):
            return inner
    return {}


def _eligible_f58_row_split_sequence(seq: List[Dict[str, Any]], base: str) -> bool:
    """`*_S1..Sn` が G62 の同一表行分割由来か。欠片が 1 つでも未マークなら結合しない。"""
    for a in seq:
        md = _analysis_top_metadata(a)
        if not md.get("f58_row_split_sequence"):
            return False
        if str(md.get("f58_row_split_base_id") or "") != base:
            return False
        tid = str(a.get("table_id") or "")
        m = _SUFFIX_RE.match(tid)
        if not m or m.group("base") != base:
            return False
    return True


def _norm_cell(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() == "null":
        return ""
    return s


def _rows_equal(a: List[List[Any]], b: List[List[Any]]) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if not isinstance(ra, (list, tuple)) or not isinstance(rb, (list, tuple)):
            return False
        if len(ra) != len(rb):
            return False
        for x, y in zip(ra, rb):
            if _norm_cell(x) != _norm_cell(y):
                return False
    return True


def _header_depth_from_meta(meta: Dict[str, Any]) -> int:
    hr = meta.get("header_rows")
    if isinstance(hr, list) and hr:
        if all(isinstance(x, int) for x in hr):
            return max(hr) + 1
        return len(hr)
    dsr = meta.get("data_start_row")
    if isinstance(dsr, int) and dsr > 0:
        return dsr
    return 1


def _append_block_strip_headers(
    merged_rows: List[List[Any]],
    data_next: List[List[Any]],
    header_depth: int,
) -> List[List[Any]]:
    """後続ブロックを付与。先頭のヘッダー行が先頭表と同一なら落とし、境界重複行も除去。"""
    if not data_next:
        return merged_rows
    dr = [list(r) for r in data_next]
    if not dr:
        return merged_rows
    h = min(max(header_depth, 0), len(dr))

    if h > 0 and len(merged_rows) >= h:
        if _rows_equal(dr[:h], merged_rows[:h]):
            return merged_rows + dr[h:]

    max_k = min(len(merged_rows), len(dr), max(h, 1) + 12)
    for k in range(max_k, 0, -1):
        if _rows_equal(merged_rows[-k:], dr[:k]):
            return merged_rows + dr[k:]
    return merged_rows + dr


def _merge_same_width_suffix_sequence(
    analyses: List[Dict[str, Any]],
    base_id: str,
) -> Optional[Dict[str, Any]]:
    """同一列幅の *_S1..n 解析結果を 1 つの table_analysis にまとめる（ヘッダー行の重複を落とす）。"""
    if len(analyses) < 2:
        return None

    first = analyses[0]
    sections = first.get("sections") or []
    if not sections:
        return None
    sec0 = sections[0]
    rows0 = sec0.get("data") or []
    if not rows0:
        return None
    width = len(rows0[0]) if rows0[0] else 0
    if width < 1:
        return None
    for a in analyses[1:]:
        sec = (a.get("sections") or [{}])[0]
        for row in sec.get("data") or []:
            if row and len(row) != width:
                return None

    meta0 = sec0.get("metadata") or {}
    h0 = _header_depth_from_meta(meta0)
    merged_rows: List[List[Any]] = [list(r) for r in rows0]

    for nxt in analyses[1:]:
        sec = (nxt.get("sections") or [{}])[0]
        data = sec.get("data") or []
        meta = sec.get("metadata") or {}
        h = max(h0, _header_depth_from_meta(meta))
        merged_rows = _append_block_strip_headers(merged_rows, data, h)

    out = deepcopy(first)
    out["table_id"] = base_id
    if not str(out.get("description") or "").strip():
        out["description"] = base_id
    sec_out = (out.get("sections") or [{}])[0]
    sec_out["data"] = merged_rows
    m = sec_out.setdefault("metadata", {})
    m["row_range"] = [0, max(len(merged_rows) - 1, 0)]
    m["f59_merged_suffix_parts"] = len(analyses)
    return out


def join_table_analyses(table_analyses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """G62 出力を配置後結合する（現状: 行分割サフィックス *_S1..n の縦統合）。"""
    if not table_analyses or len(table_analyses) < 2:
        return table_analyses

    logger.info(f"[G65] 配置後表結合: 入力 {len(table_analyses)} 件")

    groups: Dict[str, List[Tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for a in table_analyses:
        tid = str(a.get("table_id") or "")
        m = _SUFFIX_RE.match(tid)
        if not m:
            continue
        groups[m.group("base")].append((int(m.group("n")), a))

    merged_by_base: Dict[str, Dict[str, Any]] = {}
    merge_count = 0
    for base, numbered in groups.items():
        numbered.sort(key=lambda x: x[0])
        nums = [x[0] for x in numbered]
        if len(numbered) < 2 or nums != list(range(1, len(nums) + 1)):
            continue
        seq = [x[1] for x in numbered]
        if not _eligible_f58_row_split_sequence(seq, base):
            logger.info(
                f"[G65] スキップ: {base}_S1..S{len(seq)} は G62 行分割マーカー不備のため結合しない "
                f"（類似・同種のみでの結合は行わない）"
            )
            continue
        merged = _merge_same_width_suffix_sequence(seq, base)
        if merged:
            merged_by_base[base] = merged
            merge_count += len(seq) - 1
            logger.info(
                f"[G65] サフィックス統合: {base}_S1..S{len(seq)} → {base} "
                f"({len((seq[0].get('sections') or [{}])[0].get('data') or [])}行相当 ×{len(seq)} → "
                f"{len((merged.get('sections') or [{}])[0].get('data') or [])}行)"
            )

    if not merged_by_base:
        logger.info("[G65] 結合対象なし（そのまま出力）")
        return table_analyses

    out: List[Dict[str, Any]] = []
    emitted_bases: set[str] = set()
    for a in table_analyses:
        tid = str(a.get("table_id") or "")
        m = _SUFFIX_RE.match(tid)
        if m:
            base = m.group("base")
            if base in merged_by_base:
                n = int(m.group("n"))
                if n == 1 and base not in emitted_bases:
                    out.append(merged_by_base[base])
                    emitted_bases.add(base)
                continue
        out.append(a)

    logger.info(f"[G65] 完了: {len(table_analyses)} → {len(out)} 件（統合 {merge_count} ブロック）")
    return out
