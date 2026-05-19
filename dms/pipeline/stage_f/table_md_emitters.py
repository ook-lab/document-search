"""MD 埋め込み用: 月見出しの colspan と YAML 木（月ブロック × データ行）。"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List

import yaml

from dms.pipeline.stage_f.merged_cell_grid import is_merge_placeholder, resolve_horizontal_merges_in_row

# 「4 月」「４月」「4月」等
_MONTH_HEADER = re.compile(r"(?:^|\s)([0-9０-９]{1,2})\s*月")


def _norm_header_rows(raw: Any) -> List[int]:
    if not raw:
        return []
    out: List[int] = []
    for x in raw:
        if isinstance(x, int):
            out.append(x)
        elif isinstance(x, str) and x.strip().lstrip("-").isdigit():
            out.append(int(x))
    return sorted(set(out))


def _cell_text(cell: Any) -> str:
    return "" if cell is None else str(cell).strip()


def _cell_text_flat(cell: Any) -> str:
    """YAML / 検索向け: セル内改行・連続空白を1スペースに畳む。"""
    t = _cell_text(cell)
    if not t:
        return ""
    t = re.sub(r"[\r\n\t\v\f]+", " ", t)
    t = re.sub(r" {2,}", " ", t)
    return t.strip()


def _sanitize_header_rows(hr: List[int], num_rows: int) -> List[int]:
    """
    メタデータの header_rows が表全体を覆い「データ行ゼロ」になるのを防ぐ。
    典型: 名簿などで全行が header 扱い → YAML data_rows が空・HTML が全 th。
    """
    if num_rows <= 0:
        return []
    hr = sorted({int(x) for x in hr if isinstance(x, int) and 0 <= x < num_rows})
    if not hr:
        return []
    if max(hr) >= num_rows - 1:
        if num_rows == 1:
            return []
        return [0]
    return hr


def _is_month_label(cell: Any) -> bool:
    t = _cell_text(cell)
    return bool(t) and bool(_MONTH_HEADER.search(t))


def infer_month_column_groups(header_row: List[Any]) -> List[Dict[str, Any]]:
    """
    第1ヘッダー行から月ラベル列ブロックを推定。
    - 月セルの直後に空/None が続く場合はその幅を colspan に含める。
    - 密な「月,日,曜,...」でも次の月手前までを1ブロックとする。
    """
    if not header_row:
        return []
    groups: List[Dict[str, Any]] = []
    n = len(header_row)
    i = 0
    while i < n:
        if not _is_month_label(header_row[i]):
            i += 1
            continue
        label = _cell_text(header_row[i])
        j = i + 1
        while j < n:
            cj = header_row[j]
            if cj is None or _cell_text(cj) == "":
                j += 1
                continue
            if _is_month_label(cj):
                break
            j += 1
        span = max(1, j - i)
        groups.append({"start": i, "colspan": span, "label": label})
        i = j
    return groups


def _esc(s: str) -> str:
    return html.escape(s).replace("\n", "<br />\n")


def _horizontal_merges_by_row(meta: Dict[str, Any]) -> Dict[int, List[Dict[str, int]]]:
    out: Dict[int, List[Dict[str, int]]] = {}
    for item in meta.get("horizontal_merges") or []:
        if not isinstance(item, dict):
            continue
        try:
            ri = int(item.get("row_index"))
        except (TypeError, ValueError):
            continue
        spans = item.get("spans")
        if isinstance(spans, list) and spans:
            out[ri] = [s for s in spans if isinstance(s, dict)]
    return out


def _tbody(
    rows: List[List[Any]],
    body_start: int,
    *,
    horizontal_merges: Optional[Dict[int, List[Dict[str, int]]]] = None,
) -> str:
    out = ["<tbody>"]
    merges = horizontal_merges or {}
    for r in range(body_start, len(rows)):
        row = list(rows[r] or [])
        spans = merges.get(r) or []
        if spans:
            out.append(_tr_with_colspans(row, spans))
        else:
            filled, inferred = resolve_horizontal_merges_in_row(row, start_col=1)
            if inferred:
                out.append(_tr_with_colspans(filled, inferred))
            else:
                out.append("<tr>")
                for cell in row:
                    out.append(f"<td>{_esc(_cell_text_flat(cell))}</td>")
                out.append("</tr>")
    out.append("</tbody>")
    return "".join(out)


def _tr_with_colspans(row: List[Any], spans: List[Dict[str, int]]) -> str:
    span_at: Dict[int, int] = {}
    skip: set[int] = set()
    for sp in spans:
        try:
            start = int(sp["start"])
            colspan = int(sp["colspan"])
        except (KeyError, TypeError, ValueError):
            continue
        if colspan < 2:
            continue
        span_at[start] = colspan
        for k in range(start + 1, start + colspan):
            skip.add(k)
    parts = ["<tr>"]
    for i, cell in enumerate(row):
        if i in skip:
            continue
        if i in span_at:
            parts.append(
                f'<td colspan="{span_at[i]}">{_esc(_cell_text_flat(cell))}</td>'
            )
        else:
            parts.append(f"<td>{_esc(_cell_text_flat(cell))}</td>")
    parts.append("</tr>")
    return "".join(parts)


def _thead_row_from_labels(labels: List[str]) -> str:
    parts = ["<thead><tr>"]
    for lab in labels:
        parts.append(f"<th>{_esc(str(lab or ''))}</th>")
    parts.append("</tr></thead>")
    return "".join(parts)


def build_table_html_for_md(ui_table: Dict[str, Any]) -> str:
    """単一 ui_data.tables 要素向け `<table>`（可能なら月行に colspan）。"""
    rows = ui_table.get("rows") or []
    if not rows:
        return ""
    meta = ui_table.get("metadata") or {}
    row_merges = _horizontal_merges_by_row(meta)
    col_labels = ui_table.get("headers")
    if not (isinstance(col_labels, list) and any(str(x).strip() for x in col_labels)):
        col_labels = resolve_ui_column_labels(ui_table)

    if len(rows) == 1:
        parts = ['<table class="md-embed-table">', "<tbody>"]
        parts.append(_tr_with_colspans(rows[0] or [], row_merges.get(0) or []))
        parts.append("</tbody></table>")
        return "".join(parts)

    raw_hr = meta.get("header_rows")
    hr = _sanitize_header_rows(_norm_header_rows(raw_hr), len(rows))
    if not hr:
        dsr0 = meta.get("data_start_row")
        parts = ['<table class="md-embed-table">']
        if isinstance(dsr0, int) and dsr0 == 0:
            labels = col_labels if isinstance(col_labels, list) else []
            widths = [len(r) for r in rows if isinstance(r, (list, tuple))]
            max_c = max(widths) if widths else len(labels)
            while len(labels) < max_c:
                labels.append(f"列{len(labels) + 1}")
            parts.append(_thead_row_from_labels(labels[:max_c]))
            parts.append(_tbody(rows, 0, horizontal_merges=row_merges))
        else:
            parts.append("<thead><tr>")
            for cell in rows[0] or []:
                parts.append(f"<th>{_esc(_cell_text_flat(cell))}</th>")
            parts.append("</tr></thead>")
            parts.append(_tbody(rows, 1, horizontal_merges=row_merges))
        parts.append("</table>")
        return "".join(parts)

    r0 = max(0, min(hr[0], len(rows) - 1))
    groups = infer_month_column_groups(rows[r0])

    if len(hr) >= 2 and groups:
        r1 = max(0, min(hr[1], len(rows) - 1))
        if r1 != r0:
            hr1 = list(rows[r1] or [])
            need = max((g["start"] + g["colspan"]) for g in groups)
            if len(hr1) < need:
                hr1.extend([""] * (need - len(hr1)))
            parts = ['<table class="md-embed-table">', "<thead>"]
            parts.append("<tr>")
            for g in groups:
                parts.append(f'<th colspan="{g["colspan"]}">{_esc(_cell_text_flat(g["label"]))}</th>')
            parts.append("</tr><tr>")
            for g in groups:
                s, sp = g["start"], g["colspan"]
                slice1 = hr1[s : s + sp]
                for k in range(sp):
                    v = slice1[k] if k < len(slice1) else ""
                    parts.append(f"<th>{_esc(_cell_text_flat(v))}</th>")
            parts.append("</tr></thead>")
            dsr_m = meta.get("data_start_row")
            if isinstance(dsr_m, int) and 0 < dsr_m <= len(rows):
                body_start = dsr_m
            else:
                body_start = max(hr) + 1
            parts.append(_tbody(rows, body_start, horizontal_merges=row_merges))
            parts.append("</table>")
            return "".join(parts)

    if groups:
        parts = ['<table class="md-embed-table">', "<thead><tr>"]
        for g in groups:
            parts.append(f'<th colspan="{g["colspan"]}">{_esc(_cell_text_flat(g["label"]))}</th>')
        parts.append("</tr></thead>")
        dsr_g = meta.get("data_start_row")
        if isinstance(dsr_g, int) and 0 < dsr_g <= len(rows):
            body_s = dsr_g
        else:
            body_s = r0 + 1
        parts.append(_tbody(rows, body_s, horizontal_merges=row_merges))
        parts.append("</table>")
        return "".join(parts)

    dsr_html = meta.get("data_start_row")
    widths = [len(r) for r in rows if isinstance(r, (list, tuple))]
    max_c = max(widths) if widths else 0
    if isinstance(dsr_html, int) and 0 < dsr_html <= len(rows):
        parts = ['<table class="md-embed-table">']
        if col_labels and meta.get("lr_rebuilt"):
            parts.append(_thead_row_from_labels(col_labels))
        else:
            parts.append("<thead>")
            for ri in range(dsr_html):
                parts.append("<tr>")
                for cell in rows[ri] or []:
                    parts.append(f"<th>{_esc(_cell_text_flat(cell))}</th>")
                parts.append("</tr>")
            parts.append("</thead>")
        parts.append(_tbody(rows, dsr_html, horizontal_merges=row_merges))
        parts.append("</table>")
        return "".join(parts)

    parts = ['<table class="md-embed-table">']
    th_rows = set(hr)
    for ri, row in enumerate(rows):
        parts.append("<tr>")
        for cell in row or []:
            tag = "th" if ri in th_rows else "td"
            parts.append(f"<{tag}>{_esc(_cell_text_flat(cell))}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _data_rows_by_month_blocks(
    rows: List[List[Any]], body_start: int, groups: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ri in range(body_start, len(rows)):
        row = rows[ri] or []
        blocks: List[List[str]] = []
        for g in groups:
            s, sp = int(g["start"]), int(g["colspan"])
            chunk = [_cell_text_flat(row[j]) if j < len(row) else "" for j in range(s, s + sp)]
            blocks.append(chunk)
        out.append({"sheet_row": ri, "by_month_block": blocks})
    return out


def _labels_from_col_analysis(meta: Dict[str, Any], ncols: int) -> List[str]:
    """F57 col_analysis の category 名を列ラベル候補にする。"""
    ca_raw = meta.get("col_analysis")
    if not isinstance(ca_raw, list):
        return []
    out: List[str] = []
    for c in range(ncols):
        label = ""
        for ca in ca_raw:
            if not isinstance(ca, dict):
                continue
            try:
                ci = int(ca.get("col_index"))
            except (TypeError, ValueError):
                continue
            if ci != c:
                continue
            ct = _cell_text_flat(ca.get("common_type"))
            if not ct:
                continue
            if ca.get("abstraction_level") == "category_name":
                label = ct
                break
            if not label and ca.get("abstraction_level") == "concrete_value":
                label = ct
        out.append(label)
    return out


def _labels_for_section_header(
    rows: List[List[Any]], meta: Dict[str, Any], max_c: int
) -> List[str]:
    hr = _sanitize_header_rows(_norm_header_rows(meta.get("header_rows")), len(rows))
    h0 = hr[0] if hr else 0
    header = rows[h0] if rows and h0 < len(rows) else []
    ca_labels = _labels_from_col_analysis(meta, max_c)
    labels: List[str] = []
    for c in range(max_c):
        t = _cell_text(header[c]) if c < len(header) else ""
        if c == 0 and "\n" in t:
            t = t.split("\n", 1)[0].strip()
        else:
            t = _cell_text_flat(header[c]) if c < len(header) else ""
        if not t and c < len(ca_labels):
            t = ca_labels[c]
        if not t and c == 0 and header:
            t0 = _cell_text(header[0])
            if "\n" in t0:
                t = t0.split("\n", 1)[0].strip()
        labels.append(t if t else f"列{c + 1}")
    return labels


def resolve_ui_column_labels(ui_table: Dict[str, Any]) -> List[str]:
    """UI / MD 用列見出し（F51 正本・col_map・F57 を順に参照）。"""
    rows = ui_table.get("rows") or []
    if not rows:
        return []
    meta = ui_table.get("metadata") or {}
    widths = [len(r) for r in rows if isinstance(r, (list, tuple))]
    max_c = max(widths) if widths else 0
    if max_c < 1:
        return []

    if meta.get("f56_split_axis") == "col" or meta.get("split_from"):
        return _labels_for_section_header(rows, meta, max_c)

    display = meta.get("display_column_labels")
    if isinstance(display, list) and len(display) >= max_c:
        return [_cell_text_flat(x) or f"列{i + 1}" for i, x in enumerate(display[:max_c])]

    ch = meta.get("column_headers")
    if isinstance(ch, list) and ch and meta.get("lr_rebuilt"):
        col_map = meta.get("col_map") or {}
        ca_labels = _labels_from_col_analysis(meta, max(len(ch), max_c))
        labels: List[str] = []
        for c in range(max_c):
            t = _cell_text_flat(ch[c]) if c < len(ch) else ""
            if not t and c < len(ca_labels):
                t = ca_labels[c]
            if not t:
                coord = col_map.get(c) if c in col_map else col_map.get(str(c))
                if isinstance(coord, dict):
                    for v in coord.values():
                        tv = _cell_text_flat(v)
                        if tv:
                            t = tv
                            break
            labels.append(t if t else f"列{c + 1}")
        return labels

    return infer_column_labels(ui_table)


def infer_column_labels(ui_table: Dict[str, Any]) -> List[str]:
    """
    MD / プレビュー用の列見出し。

    ``metadata.data_start_row`` があれば、列ラベルは **その行より前** だけを縦にマージする
    （F58 が付けたデータ開始行。header_rows が過剰でもデータ行を列見出しに混ぜない）。
    data_start_row が無いときのみ ``header_rows`` の範囲でマージする。
    """
    rows = ui_table.get("rows") or []
    if not rows:
        return []
    meta = ui_table.get("metadata") or {}
    hr = _sanitize_header_rows(_norm_header_rows(meta.get("header_rows")), len(rows))
    widths = [len(r) for r in rows if isinstance(r, (list, tuple))]
    max_c = max(widths) if widths else 0
    if max_c < 1:
        return []
    if hr:
        dsr = meta.get("data_start_row")
        h_end_raw = max(hr) + 1
        if isinstance(dsr, int) and 0 < dsr <= len(rows):
            h_end = min(dsr, h_end_raw, len(rows))
        else:
            h_end = min(h_end_raw, len(rows))
        labels: List[str] = []
        for c in range(max_c):
            parts: List[str] = []
            for ri in range(h_end):
                if ri >= len(rows):
                    break
                row = rows[ri]
                if not isinstance(row, (list, tuple)) or c >= len(row):
                    continue
                t = _cell_text_flat(row[c])
                if t:
                    parts.append(t)
            labels.append(" / ".join(parts) if parts else f"列{c + 1}")
        return labels
    row0 = rows[0]
    if isinstance(row0, (list, tuple)):
        return [_cell_text_flat(x) or f"列{i + 1}" for i, x in enumerate(row0)]
    return [f"列{i + 1}" for i in range(max_c)]


def table_yaml_record(ui_table: Dict[str, Any]) -> Dict[str, Any]:
    rows = ui_table.get("rows") or []
    meta = ui_table.get("metadata") or {}
    ts = meta.get("table_semantics") if isinstance(meta.get("table_semantics"), dict) else {}
    raw_hr = meta.get("header_rows")
    hr = _sanitize_header_rows(_norm_header_rows(raw_hr), len(rows))
    r0 = max(0, min(hr[0], len(rows) - 1)) if hr and rows else 0
    groups = infer_month_column_groups(rows[r0]) if rows else []
    dsr = meta.get("data_start_row")
    if isinstance(dsr, int) and 0 < dsr <= len(rows):
        body_start = dsr
        header_row_indices: List[int] = list(range(dsr))
    else:
        body_start = (max(hr) + 1) if hr else 0
        header_row_indices = hr
    rec: Dict[str, Any] = {
        "table_id": ui_table.get("table_id"),
        "description": ui_table.get("description"),
        "table_semantics": ts,
        "header_row_indices": header_row_indices,
        "month_blocks": [
            {"label": g["label"], "start_col": g["start"], "colspan": g["colspan"]} for g in groups
        ],
    }
    if groups and body_start < len(rows):
        rec["data_rows"] = _data_rows_by_month_blocks(rows, body_start, groups)
    else:
        data_rows: List[Dict[str, Any]] = []
        for i, r in enumerate(rows[body_start:], start=body_start):
            row = list(r or [])
            filled, _ = resolve_horizontal_merges_in_row(row, start_col=1)
            data_rows.append(
                {"sheet_row": i, "cells": [_cell_text_flat(x) for x in filled]}
            )
        rec["data_rows"] = data_rows
    return rec


def build_tables_markdown_embed(ui_tables: List[Dict[str, Any]]) -> str:
    """複数表をまとめた MD 断片（YAML + raw HTML）。"""
    if not ui_tables:
        return ""
    doc = {"tables": [table_yaml_record(t) for t in ui_tables]}
    parts: List[str] = ["<!-- dms:tables-md-embed v1 -->\n"]
    parts.append("### `tables`（YAML・検索・LLM 向け）\n\n```yaml\n")
    parts.append(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False))
    parts.append("```\n\n### 表 HTML（MD に埋め込み可）\n\n")
    for t in ui_tables:
        tid = str(t.get("table_id") or "")
        parts.append(f"<!-- table:{html.escape(tid)} -->\n")
        parts.append(build_table_html_for_md(t) + "\n\n")
    return "".join(parts).rstrip()
