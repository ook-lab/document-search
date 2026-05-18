"""結合セル（空プレースホルダ）の左方向展開と表示用 colspan メタデータ。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

MergeSpan = Dict[str, int]  # {"start": int, "colspan": int}
RowMergeMeta = Dict[str, Any]  # {"row_index": int, "spans": List[MergeSpan]}


def is_merge_placeholder(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return True
        if re.match(r"^列\d+$", s):
            return True
        if re.match(r"^Col\d+$", s, re.I):
            return True
    return False


def resolve_horizontal_merges_in_row(
    row: List[Any],
    *,
    start_col: int = 0,
) -> Tuple[List[Any], List[MergeSpan]]:
    """
    結合セル由来の空セルを左のアンカー値で埋め、colspan 用スパンを返す。

    start_col より左（行ラベル列など）はそのまま。
    """
    out = list(row)
    spans: List[MergeSpan] = []
    i = max(0, start_col)
    n = len(out)
    while i < n:
        if is_merge_placeholder(out[i]):
            i += 1
            continue
        val = out[i]
        j = i + 1
        while j < n and is_merge_placeholder(out[j]):
            j += 1
        span = j - i
        if span > 1:
            spans.append({"start": i, "colspan": span})
            for k in range(i + 1, j):
                out[k] = val
        i = j
    return out, spans


def _none_to_empty(val: Any) -> Any:
    return "" if val is None else val


def normalize_grid_cells(grid: List[List[Any]]) -> List[List[Any]]:
    """Stage B / pdfplumber の None を結合プレースホルダ '' に揃える。"""
    return [[_none_to_empty(c) for c in (row or [])] for row in grid]


def row_has_cell_text(row: Any) -> bool:
    if not isinstance(row, (list, tuple)):
        return False
    return any(str(c).strip() for c in row if c is not None and str(c).strip())


def _expand_one_body_row(row: List[Any]) -> List[List[Any]]:
    """1 extract 行内の改行セルを複数行に展開する。"""
    if not isinstance(row, (list, tuple)):
        return [list(row) if row is not None else []]
    split_cols: Dict[int, List[str]] = {}
    max_lines = 1
    for ci, cell in enumerate(row):
        s = "" if cell is None else str(cell)
        if "\n" not in s:
            continue
        parts = [p.strip() for p in s.split("\n")]
        parts = [p for p in parts if p]
        if len(parts) > 1:
            split_cols[ci] = parts
            max_lines = max(max_lines, len(parts))
    if max_lines <= 1:
        return [[_none_to_empty(c) for c in row]]
    width = max(len(row), max(split_cols.keys(), default=-1) + 1)
    out: List[List[Any]] = []
    for li in range(max_lines):
        new_row: List[Any] = []
        for ci in range(width):
            if ci in split_cols:
                parts = split_cols[ci]
                new_row.append(parts[li] if li < len(parts) else "")
            else:
                val = row[ci] if ci < len(row) else ""
                s = "" if val is None else str(val).strip()
                new_row.append(val if (li == 0 and s) else "")
        out.append(new_row)
    return out


_CIRCLED_NUM = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")
_AMOUNT_TOKEN = re.compile(r"\d[\d,]*")


def row_starts_with_circled_marker(row: Any) -> bool:
    """行先頭が丸数字項目か（見出し行ではなくデータ行）。"""
    if not isinstance(row, (list, tuple)) or not row:
        return False
    c0 = str(row[0] or "").strip()
    return bool(c0 and _CIRCLED_NUM.match(c0))


def unfold_numbered_two_column_list(grid: List[List[Any]]) -> List[List[Any]]:
    """
    2列表で①②…が1セルに潰れている行を、項目ごとの行に展開する（使途一覧など）。
    """
    if not grid:
        return grid
    ncols = max((len(r) for r in grid if isinstance(r, (list, tuple))), default=0)
    if ncols != 2:
        return grid

    dsr = 0
    head: List[List[Any]] = []
    if len(grid) >= 2 and not _CIRCLED_NUM.search(str((grid[0][0] if grid[0] else "") or "")):
        head = [list(grid[0])]
        dsr = 1

    out: List[List[Any]] = list(head)
    for row in grid[dsr:]:
        if not isinstance(row, (list, tuple)):
            out.append(list(row) if row is not None else [])
            continue
        c0 = str(row[0] or "").strip()
        c1 = str(row[1] or "").strip()
        marks = list(_CIRCLED_NUM.finditer(c0))
        if len(marks) < 2:
            out.append([_none_to_empty(c) for c in row])
            continue
        parts0: List[str] = []
        for i, m in enumerate(marks):
            start = m.start()
            end = marks[i + 1].start() if i + 1 < len(marks) else len(c0)
            parts0.append(c0[start:end].strip())
        parts1 = _split_numbered_companion_cell(c1, len(parts0))
        if len(parts1) != len(parts0):
            out.append([_none_to_empty(c) for c in row])
            continue
        for a, b in zip(parts0, parts1):
            out.append([a, b])
    return out


def _split_numbered_companion_cell(text: str, n: int) -> List[str]:
    if n <= 1:
        return [text.strip()]
    marks = list(_CIRCLED_NUM.finditer(text))
    if len(marks) >= n:
        parts: List[str] = []
        for i, m in enumerate(marks[:n]):
            start = m.start()
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            parts.append(text[start:end].strip())
        return parts
    words = text.split()
    if len(words) == n:
        return words
    return [text.strip()]


def unfold_left_label_amount_space_join(grid: List[List[Any]]) -> List[List[Any]]:
    """
    2列以上の表で col0・col1 が空白区切りで複数項目潰れている行を行分割する。

    ラベル分割は空白トークン数と金額トークン数の一致のみ（語彙・表種語は使わない）。
    LR 縦結合候補（G36）では呼ばないこと。
    """
    if len(grid) < 2:
        return grid
    ncols = max((len(r) for r in grid if isinstance(r, (list, tuple))), default=0)
    if ncols < 2:
        return grid
    out: List[List[Any]] = [list(grid[0])]
    for row in grid[1:]:
        if not isinstance(row, (list, tuple)):
            out.append(list(row) if row is not None else [])
            continue
        c0 = str(row[0] or "").strip()
        c1 = str(row[1] or "").strip()
        amounts = _AMOUNT_TOKEN.findall(c1)
        if len(amounts) < 2 or " " not in c0 or _CIRCLED_NUM.search(c0):
            out.append([_none_to_empty(c) for c in row])
            continue
        labels = _split_space_joined_labels(c0, len(amounts))
        if len(labels) != len(amounts):
            out.append([_none_to_empty(c) for c in row])
            continue
        rest = list(row[2:]) if len(row) > 2 else []
        for lab, amt in zip(labels, amounts):
            base = [lab, amt] + [""] * len(rest)
            out.append(base[:ncols] + [""] * max(0, ncols - len(base)))
    return out


_WEEKDAY_ONLY = re.compile(r"^（[月火水木金土日]）$")


def _grid_max_cols(grid: List[List[Any]]) -> int:
    return max((len(r) for r in grid if isinstance(r, (list, tuple))), default=0)


def _cell_str(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _is_full_width_span_row(row: Sequence[Any], ncols: int) -> bool:
    """col1 以降がすべて同一の非空文字（祝日など）。平日行＋折り返し漏れは除外。"""
    c0 = _cell_str(row[0] if row else "")
    if _WEEKDAY_ONLY.match(c0):
        return False
    vals: List[str] = []
    for c in range(1, min(ncols, len(row))):
        t = _cell_str(row[c])
        if t:
            vals.append(t)
    return len(vals) >= 3 and len(set(vals)) == 1


def _is_wrap_propagation_row(prev: List[Any], cur: List[Any], ncols: int) -> bool:
    """折り返し行で col1 の短文が他列にコピーされた extract 漏れ。"""
    c1 = _cell_str(cur[1] if len(cur) > 1 else "")
    if not c1:
        return False
    dup = 0
    for c in range(2, ncols):
        t = _cell_str(cur[c] if c < len(cur) else "")
        if t and t == c1:
            dup += 1
    return dup >= 2


def _should_merge_continuation(prev: List[Any], cur: List[Any], ncols: int) -> bool:
    if _is_full_width_span_row(cur, ncols):
        return False
    if _is_wrap_propagation_row(prev, cur, ncols):
        return True
    p0 = _cell_str(prev[0] if prev else "")
    c0 = _cell_str(cur[0] if cur else "")
    c1 = _cell_str(cur[1] if len(cur) > 1 else "")
    p1 = _cell_str(prev[1] if len(prev) > 1 else "")
    if c0 and _WEEKDAY_ONLY.match(c0) and p0 and re.match(r"^\d", p0):
        return True
    if not c0 and c1 and p1:
        rest = sum(1 for c in range(2, ncols) if _cell_str(cur[c] if c < len(cur) else ""))
        if rest == 0:
            return True
    return False


def _merge_row_into_prev(
    prev: List[Any],
    cur: List[Any],
    ncols: int,
    *,
    skip_propagated_cols: bool,
) -> None:
    for c in range(ncols):
        t = _cell_str(cur[c] if c < len(cur) else "")
        if not t:
            continue
        if skip_propagated_cols and c >= 2:
            c1 = _cell_str(cur[1] if len(cur) > 1 else "")
            if t == c1:
                continue
        while len(prev) <= c:
            prev.append("")
        p = _cell_str(prev[c])
        if not p:
            prev[c] = t
        elif t == p:
            continue
        elif len(t) > len(p) and t.startswith(p):
            prev[c] = t
        elif len(p) > len(t) and p.startswith(t):
            continue
        else:
            prev[c] = p + t
    c0 = _cell_str(cur[0] if cur else "")
    if c0 and _WEEKDAY_ONLY.match(c0):
        while len(prev) < 1:
            prev.append("")
        p0 = _cell_str(prev[0])
        if p0 and c0 not in p0:
            prev[0] = f"{p0}\n{c0}"


def coalesce_wrapped_extract_rows(
    grid: List[List[Any]],
    *,
    data_start_row: int = 1,
) -> List[List[Any]]:
    """
    5列以上の表: extract 折り返しで増えた行を列単位で前の行に結合する。

    全列に同じ語が並ぶ行は祝日などの横断セルとして残す。語彙ではなく列パターンのみ。
    """
    ncols = _grid_max_cols(grid)
    if ncols < 5 or not grid:
        return [list(r) for r in grid]
    dsr = max(0, min(int(data_start_row), len(grid)))
    out: List[List[Any]] = [list(grid[i]) for i in range(dsr)]
    i = dsr
    while i < len(grid):
        row = list(grid[i])
        if out and _should_merge_continuation(out[-1], row, ncols):
            skip = _is_wrap_propagation_row(out[-1], row, ncols)
            _merge_row_into_prev(out[-1], row, ncols, skip_propagated_cols=skip)
            i += 1
            continue
        out.append(row)
        i += 1
    return out


def _split_space_joined_labels(c0: str, n: int) -> List[str]:
    if n <= 1:
        return [c0.strip()]
    parts = c0.split()
    if len(parts) == n:
        return parts
    return []


def expand_multiline_cells_in_grid(
    grid: List[List[Any]],
    *,
    bd_grid_aligned: bool = False,
) -> List[List[Any]]:
    """
    pdfplumber 等で1 extract 行にまとまったセル内改行を、複数 extract 行に展開する。

    5列以上は折り返し行の結合のみ（時間割などで全列に改行展開しない）。
    G36 D セル行列で組み直した表は coalesce しない（列パターン結合は使わない）。
    4列以下はセル内改行を行分割（使途表など）。
    """
    if not grid:
        return []
    if bd_grid_aligned:
        return [list(r) for r in grid]
    ncols = _grid_max_cols(grid)
    if ncols >= 5:
        return coalesce_wrapped_extract_rows(grid)
    if len(grid) == 1:
        return _expand_one_body_row(list(grid[0]))
    out: List[List[Any]] = [list(grid[0])]
    for row in grid[1:]:
        if not isinstance(row, (list, tuple)):
            out.append(list(row) if row is not None else [])
            continue
        out.extend(_expand_one_body_row(list(row)))
    return out


def prune_blank_body_rows(
    grid: List[List[Any]],
    *,
    data_start_row: int = 1,
) -> List[List[Any]]:
    """データ部の全空行を除去（列分割後の orphan 行など）。"""
    if not grid:
        return []
    dsr = max(0, int(data_start_row))
    if dsr <= 0:
        return [list(r) for r in grid if row_has_cell_text(r)]
    head = [list(r) for r in grid[:dsr]]
    body = [list(r) for r in grid[dsr:] if row_has_cell_text(r)]
    return head + body


def apply_merged_cell_resolution(
    grid: List[List[Any]],
    *,
    data_start_row: int,
    row_label_col: Optional[int],
) -> Tuple[List[List[Any]], List[RowMergeMeta]]:
    """データ行に結合セル解決を適用。ヘッダー行はそのまま。"""
    if not grid:
        return [], []
    dsr = max(0, int(data_start_row))
    if row_label_col is not None:
        start_col = int(row_label_col) + 1
    else:
        start_col = 1 if grid and len(grid[0]) > 1 else 0

    out: List[List[Any]] = []
    merges: List[RowMergeMeta] = []
    for ri, row in enumerate(grid):
        if ri < dsr:
            out.append(list(row))
            continue
        filled, spans = resolve_horizontal_merges_in_row(list(row), start_col=start_col)
        out.append(filled)
        if spans:
            merges.append({"row_index": ri, "spans": spans})
    return out, merges
