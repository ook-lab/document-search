"""左2列縦結合 + 右列行分割表の geometry 判定・再構成（Stage F51。LLM 不使用）。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from loguru import logger

ColBounds = Tuple[float, float]
VerticalMergeMode = Literal["row_aligned", "block_common"]

LR_MERGED_VERTICAL_CONTRACT = "lr_merged_vertical_v2"
G36_GEOMETRY_CONTRACT = "g36_lr_vertical_geometry_v1"
G36_MECHANICAL_CONTRACT = "g36_mechanical_unfold_v1"
G36_AI_JUDGE_CONTRACT = "g36_merged_cell_correspondence_v3"
G36_LR_REBUILD_JUDGES = frozenset(
    {
        G36_GEOMETRY_CONTRACT,
        G36_MECHANICAL_CONTRACT,
        G36_AI_JUDGE_CONTRACT,
        "g36_bd_d_hline_row_union_v1",
        "g36_d_cell_matrix_v1",
    }
)


class LRMergedVerticalRebuildError(Exception):
    """左右縦結合表として再構成を試みたが、判定または組み立てに失敗。"""


def _max_cols(grid: List[List[Any]]) -> int:
    return max((len(r) for r in grid if isinstance(r, (list, tuple))), default=0)


def _left_cell_present(cells: Sequence[Any]) -> bool:
    if len(cells) > 0 and cells[0]:
        return True
    if len(cells) > 1 and cells[1]:
        return True
    return False


def is_lr_merged_vertical_candidate(
    page: Any,
    table: Any,
    data: List[List[Any]],
    *,
    header_rows: int = 1,
) -> bool:
    """
    表種キーワードは使わない。表 geometry のみ:

    - 5列以上
    - 先頭データ行で左列0の bbox 高さが右アンカー列より大きい（左縦結合）
    - 以降に「左 bbox 無し・右アンカーあり」の行が1つ以上
    """
    if len(data) < header_rows + 2:
        return False
    if _max_cols(data) < 5:
        return False

    plumber_rows = getattr(table, "rows", None) or []
    if len(plumber_rows) < header_rows + 2:
        return False
    x_bounds = _column_x_bounds(plumber_rows)
    if len(x_bounds) < 5:
        return False
    if len(x_bounds) >= 7:
        return False
    bands = _collect_right_bands(plumber_rows, header_rows)
    if len(bands) < 2:
        return False
    if len(bands) >= 12:
        return False
    if _left_merged_bbox(plumber_rows, header_rows) is None:
        return False

    pr0 = plumber_rows[header_rows]
    cells0 = getattr(pr0, "cells", None) or []
    if len(cells0) < 3 or not cells0[0]:
        return False
    left_h = float(cells0[0][3]) - float(cells0[0][1])
    right_bb = _right_anchor_bbox(cells0)
    if not right_bb:
        return False
    right_h = float(right_bb[1]) - float(right_bb[0])
    if left_h < right_h * 1.12:
        return False

    for ri in range(header_rows + 1, len(plumber_rows)):
        pr = plumber_rows[ri]
        cells = getattr(pr, "cells", None) or []
        if not _left_cell_present(cells) and _right_anchor_bbox(cells) is not None:
            return True
    return False


def _cell_text(row: Sequence[Any], col: int) -> str:
    if col >= len(row):
        return ""
    v = row[col]
    if v is None:
        return ""
    return str(v).strip()


def _cell_text_one_line(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("\r", "").replace("\n", " ")


def _column_x_bounds(plumber_rows: Sequence[Any]) -> List[ColBounds]:
    for pr in plumber_rows:
        cells = getattr(pr, "cells", None) or []
        if len(cells) < 3 or not cells[0] or not cells[1] or not cells[2]:
            continue
        b0, b1, b2 = cells[0], cells[1], cells[2]
        x0_lo, x0_hi = float(b0[0]), float(b0[2])
        x1_lo, x1_hi = float(b1[0]), float(b1[2])
        x2_lo, x2_hi = float(b2[0]), float(b2[2])
        w23 = x2_hi - x2_lo
        x3_lo, x3_hi = x2_hi, x2_hi + w23 * 0.75
        x4_lo, x4_hi = x3_hi, x2_hi + w23 * 1.5
        if len(cells) > 3 and cells[3]:
            x3_lo, x3_hi = float(cells[3][0]), float(cells[3][2])
        if len(cells) > 4 and cells[4]:
            x4_lo, x4_hi = float(cells[4][0]), float(cells[4][2])
        return [(x0_lo, x0_hi), (x1_lo, x1_hi), (x2_lo, x2_hi), (x3_lo, x3_hi), (x4_lo, x4_hi)]
    return []


def _words_in_y_band(
    page: Any,
    table_bbox: Tuple[float, float, float, float],
    y0: float,
    y1: float,
    x0: float,
    x1: float,
) -> List[dict]:
    tx0, ty0, tx1, ty1 = table_bbox
    cropped = page.crop((tx0, ty0, tx1, ty1))
    words = cropped.extract_words(x_tolerance=3, y_tolerance=3) or []
    out: List[dict] = []
    pad = 1.5
    for w in words:
        cy = (float(w["top"]) + float(w["bottom"])) / 2.0
        if cy < y0 - pad or cy > y1 + pad:
            continue
        if float(w["x1"]) <= x0 or float(w["x0"]) >= x1:
            continue
        out.append(w)
    return sorted(out, key=lambda w: (float(w["top"]), float(w["x0"])))


def _join_words(words: List[dict]) -> str:
    return " ".join(str(w.get("text") or "").strip() for w in words if str(w.get("text") or "").strip())


def _right_anchor_bbox(cells: Sequence[Any]) -> Optional[Tuple[float, float]]:
    for ci in (2, 3, 4):
        if ci < len(cells) and cells[ci]:
            bb = cells[ci]
            return (float(bb[1]), float(bb[3]))
    return None


def _left_merged_bbox(plumber_rows: Sequence[Any], header_rows: int) -> Optional[Tuple[float, float]]:
    for ri in range(header_rows, len(plumber_rows)):
        cells = getattr(plumber_rows[ri], "cells", None) or []
        if cells and cells[0]:
            bb = cells[0]
            return (float(bb[1]), float(bb[3]))
    return None


def _collect_right_bands(
    plumber_rows: Sequence[Any],
    header_rows: int,
) -> List[Tuple[float, float, int]]:
    bands: List[Tuple[float, float, int]] = []
    for ri in range(header_rows, len(plumber_rows)):
        pr = plumber_rows[ri]
        cells = getattr(pr, "cells", None) or []
        y_band = _right_anchor_bbox(cells)
        if y_band is None and getattr(pr, "bbox", None):
            y_band = (float(pr.bbox[1]), float(pr.bbox[3]))
        if y_band:
            bands.append((y_band[0], y_band[1], ri))
    return bands


def _left_y_clusters(
    page: Any,
    table_bbox: Tuple[float, float, float, float],
    x_bounds: Sequence[ColBounds],
    y_body0: float,
    y_body1: float,
    *,
    y_tol: float = 5.0,
) -> List[Tuple[float, float, str, str]]:
    """左「収入の部」列の Y だけで行クラスタを切り、同帯の金額列語を紐づける。"""
    words_item = _words_in_y_band(
        page, table_bbox, y_body0, y_body1, x_bounds[0][0], x_bounds[0][1]
    )
    if not words_item:
        return []
    item_clusters: List[List[dict]] = []
    for w in sorted(words_item, key=lambda x: float(x["top"])):
        cy = (float(w["top"]) + float(w["bottom"])) / 2.0
        if not item_clusters:
            item_clusters.append([w])
            continue
        prev_cy = sum(
            (float(x["top"]) + float(x["bottom"])) / 2.0 for x in item_clusters[-1]
        ) / len(item_clusters[-1])
        if abs(cy - prev_cy) <= y_tol:
            item_clusters[-1].append(w)
        else:
            item_clusters.append([w])
    out: List[Tuple[float, float, str, str]] = []
    for cl in item_clusters:
        y0 = min(float(w["top"]) for w in cl)
        y1 = max(float(w["bottom"]) for w in cl)
        item = _join_words(cl)
        amt_words = _words_in_y_band(
            page, table_bbox, y0, y1, x_bounds[1][0], x_bounds[1][1]
        )
        amt = _join_words(amt_words)
        out.append((y0, y1, item, amt))
    return out


def _vertical_overlap_ratio(
    band: Tuple[float, float],
    cluster: Tuple[float, float],
) -> float:
    by0, by1 = band
    cy0, cy1 = cluster
    inter = max(0.0, min(by1, cy1) - max(by0, cy0))
    if inter <= 0:
        return 0.0
    union = max(by1, cy1) - min(by0, cy0)
    return inter / union if union > 0 else 0.0


def _pick_left_cluster_for_band(
    band: Tuple[float, float],
    clusters: Sequence[Tuple[float, float, str, str]],
    used: set[int],
) -> Optional[Tuple[int, Tuple[float, float, str, str]]]:
    best_i: Optional[int] = None
    best_score = 0.0
    by0, by1 = band
    bmid = (by0 + by1) / 2.0
    for ci, c in enumerate(clusters):
        if ci in used:
            continue
        score = _vertical_overlap_ratio(band, (c[0], c[1]))
        cmid = (c[0] + c[1]) / 2.0
        if by0 - 1.5 <= cmid <= by1 + 1.5:
            score = max(score, 0.35)
        if score > best_score:
            best_score = score
            best_i = ci
    if best_i is None or best_score < 0.12:
        return None
    return best_i, clusters[best_i]


def _band_overlaps_cluster(band: Tuple[float, float], cluster: Tuple[float, float]) -> bool:
    by0, by1 = band
    cy0, cy1 = cluster
    cmid = (cy0 + cy1) / 2.0
    return by0 - 1.5 <= cmid <= by1 + 1.5


def classify_vertical_merge_mode(
    page: Any,
    table: Any,
    *,
    header_rows: int = 1,
) -> Tuple[VerticalMergeMode, Dict[str, Any]]:
    """左 Y クラスタと右横段の対応から mode を決める（語彙・LLM 不使用）。"""
    plumber_rows = getattr(table, "rows", None) or []
    x_bounds = _column_x_bounds(plumber_rows)
    if len(x_bounds) < 5:
        raise LRMergedVerticalRebuildError("column_x_bounds_unavailable")

    bands = _collect_right_bands(plumber_rows, header_rows)
    if len(bands) < 2:
        raise LRMergedVerticalRebuildError(f"insufficient_right_bands: {len(bands)}")

    left_merge = _left_merged_bbox(plumber_rows, header_rows)
    if not left_merge:
        raise LRMergedVerticalRebuildError("left_merged_bbox_unavailable")

    body_y0, body_y1 = left_merge[0], left_merge[1]
    for _y0, y1, _ri in bands:
        body_y1 = max(body_y1, y1)
    body_y0 = min(body_y0, bands[0][0])

    clusters = _left_y_clusters(page, tuple(float(x) for x in table.bbox), x_bounds, body_y0, body_y1)
    if not clusters:
        raise LRMergedVerticalRebuildError("no_left_text_clusters")

    body_height = max(body_y1 - body_y0, 1.0)
    max_cluster_span = max((c[1] - c[0]) for c in clusters)

    aligned_pairs = 0
    for c in clusters:
        cy_mid = (c[0] + c[1]) / 2.0
        for by0, by1, _ri in bands:
            if _band_overlaps_cluster((by0, by1), (c[0], c[1])) or (by0 <= cy_mid <= by1):
                aligned_pairs += 1
                break

    evidence: Dict[str, Any] = {
        "right_band_count": len(bands),
        "left_cluster_count": len(clusters),
        "aligned_cluster_count": aligned_pairs,
        "body_height_pt": round(body_height, 2),
        "max_cluster_span_pt": round(max_cluster_span, 2),
    }

    if len(clusters) >= 2 and aligned_pairs >= 2:
        return "row_aligned", evidence

    if (
        len(clusters) == 1
        and len(bands) >= 3
        and max_cluster_span / body_height >= 0.45
        and aligned_pairs <= 1
    ):
        return "block_common", evidence

    if len(clusters) >= 2 and len(bands) >= 2:
        return "row_aligned", evidence

    if len(clusters) >= 1 and aligned_pairs >= 1:
        return "row_aligned", evidence

    raise LRMergedVerticalRebuildError(f"vertical_merge_ambiguous: {evidence}")


def _build_row_aligned_body(
    page: Any,
    table: Any,
    data: List[List[Any]],
    x_bounds: Sequence[ColBounds],
    header_rows: int,
) -> List[List[Any]]:
    """右支出行の Y 帯ごとに左収入クラスタを重なりで割当（転入≈③ など）。"""
    table_bbox = tuple(float(x) for x in table.bbox)
    plumber_rows = getattr(table, "rows", None) or []
    bands = _collect_right_bands(plumber_rows, header_rows)
    if len(bands) < 1:
        raise LRMergedVerticalRebuildError("row_aligned_no_right_bands")

    left_merge = _left_merged_bbox(plumber_rows, header_rows)
    body_y0 = bands[0][0]
    body_y1 = bands[-1][1]
    if left_merge:
        body_y0 = min(body_y0, left_merge[0])
        body_y1 = max(body_y1, left_merge[1])

    clusters = _left_y_clusters(page, table_bbox, x_bounds, body_y0, body_y1)
    used_clusters: set[int] = set()
    body: List[List[Any]] = []

    for by0, by1, ri in bands:
        row_extract = list(data[ri]) if ri < len(data) else []
        exp_item = _cell_text(row_extract, 2)
        exp_total = _cell_text(row_extract, 3)
        per_cap = _cell_text(row_extract, 4)

        picked = _pick_left_cluster_for_band((by0, by1), clusters, used_clusters)
        if picked:
            ci, c = picked
            used_clusters.add(ci)
            left_item, left_amt = c[2], c[3]
        else:
            left_item = _join_words(
                _words_in_y_band(
                    page, table_bbox, by0, by1, x_bounds[0][0], x_bounds[0][1]
                )
            )
            left_amt = _join_words(
                _words_in_y_band(
                    page, table_bbox, by0, by1, x_bounds[1][0], x_bounds[1][1]
                )
            )

        if not left_item and not left_amt and not exp_item:
            continue
        body.append([left_item, left_amt, exp_item, exp_total, per_cap])

    for ci, c in enumerate(clusters):
        if ci in used_clusters:
            continue
        if not (c[2] or c[3]):
            continue
        insert_at = len(body)
        for bi, (by0, by1, _ri) in enumerate(bands):
            cmid = (c[0] + c[1]) / 2.0
            if by0 <= cmid <= by1:
                insert_at = bi
                break
        row = [c[2], c[3], "", "", ""]
        body.insert(min(insert_at, len(body)), row)

    if not body:
        raise LRMergedVerticalRebuildError("row_aligned_body_empty")
    return body


def _build_block_common_body(
    page: Any,
    table: Any,
    data: List[List[Any]],
    x_bounds: Sequence[ColBounds],
    header_rows: int,
) -> List[List[Any]]:
    """左が1縦塊でも右 Y 帯へクラスタを割当（先頭行に全左文を載せない）。"""
    return _build_row_aligned_body(page, table, data, x_bounds, header_rows)


def rebuild_lr_merged_vertical_table(
    page: Any,
    table: Any,
    *,
    mode: VerticalMergeMode,
    judge_meta: Dict[str, Any],
    header_rows: int = 1,
) -> Tuple[List[List[Any]], Dict[str, Any]]:
    """G36 geometry 判定済み mode で格子を機械再構成する。"""
    data = table.extract() or []
    if not is_lr_merged_vertical_candidate(page, table, data, header_rows=header_rows):
        raise LRMergedVerticalRebuildError("not_lr_merged_vertical_candidate")

    plumber_rows = getattr(table, "rows", None) or []
    x_bounds = _column_x_bounds(plumber_rows)
    if len(x_bounds) < 5:
        raise LRMergedVerticalRebuildError("column_x_bounds_unavailable")

    if mode == "row_aligned":
        body = _build_row_aligned_body(page, table, data, x_bounds, header_rows)
    else:
        body = _build_block_common_body(page, table, data, x_bounds, header_rows)

    header = [list(data[0])]
    out = header + body
    header_cells = [
        "" if c is None else str(c).strip() for c in (header[0] if header else [])
    ]
    meta = {
        "lr_merged_vertical_contract": LR_MERGED_VERTICAL_CONTRACT,
        "vertical_merge_judge": judge_meta.get("vertical_merge_judge"),
        "vertical_merge_mode": mode,
        "geometry_evidence": judge_meta.get("geometry_evidence"),
        "lr_rebuilt": True,
        "source_rows": len(data),
        "output_rows": len(out),
        "header_rows": [0],
        "data_start_row": 1,
        "column_headers": header_cells,
    }
    judge = meta.get("vertical_merge_judge")
    if judge not in G36_LR_REBUILD_JUDGES:
        raise LRMergedVerticalRebuildError("g36_judge_contract_missing")
    logger.info(f"[G36] lr_merged_vertical: mode={mode} {len(data)}→{len(out)}行")
    return out, meta


def grid_has_unprocessed_left_stack(
    grid: List[List[Any]],
    *,
    data_start_row: int = 1,
) -> bool:
    """G36 未適用の左列改行スタック（5列以上・geometry 痕跡）。語は見ない。"""
    if _max_cols(grid) < 5:
        return False
    dsr = max(0, int(data_start_row))
    for ri in range(dsr, min(dsr + 4, len(grid))):
        row = grid[ri]
        if not isinstance(row, (list, tuple)):
            continue
        for c in (0, 1):
            if c < len(row) and "\n" in str(row[c] or ""):
                return True
    return False
