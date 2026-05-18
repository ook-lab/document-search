"""
F17: 地の文ブロックと表を座標順に1本の reading_stream に並べる（Stage F データ平面の読み順正本）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _bbox_top_norm(bbox: Any) -> Optional[float]:
    if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        vals = [float(x) for x in bbox[:4]]
    except (TypeError, ValueError):
        return None
    if any(v > 2.5 or v < -0.5 for v in vals):
        return None
    return vals[1]


def _bbox_top_for_sort_y(bbox: Any, page_height_pt: Optional[float]) -> Optional[float]:
    y_norm = _bbox_top_norm(bbox)
    if y_norm is not None:
        return y_norm
    if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    if page_height_pt is None or page_height_pt <= 0:
        return None
    try:
        y0 = float(bbox[1])
    except (TypeError, ValueError, IndexError):
        return None
    return max(0.0, min(1.0, y0 / page_height_pt))


def _infer_page_height_pt(document_info: Optional[Dict[str, Any]]) -> Optional[float]:
    if not document_info:
        return None
    for key in ("page_height_pt", "page_height"):
        ph = document_info.get(key)
        if isinstance(ph, (int, float)) and float(ph) > 10.0:
            return float(ph)
    return None


def _normalize_block_sort_y(y0: float, page_height_pt: Optional[float]) -> Optional[float]:
    try:
        y = float(y0)
    except (TypeError, ValueError):
        return None
    if -0.5 <= y <= 1.6:
        return max(0.0, min(1.0, y))
    if page_height_pt is None or page_height_pt <= 0:
        return None
    return max(0.0, min(1.0, y / page_height_pt))


def _interleave_prose_and_tables(
    prose_rows: List[Dict[str, Any]],
    table_entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    pieces: List[Dict[str, Any]] = []
    for i, row in enumerate(prose_rows):
        sy = row.get("sort_y")
        pieces.append(
            {
                "k": "p",
                "y": sy,
                "text": row["text"],
                "source": row.get("source"),
                "page": row.get("page"),
                "x0": row.get("x0"),
                "order": i,
                "missing": sy is None,
            }
        )
    for j, ent in enumerate(table_entries):
        sy = ent.get("sort_y")
        pieces.append({"k": "t", "y": sy, "entry": ent, "order": j, "missing": sy is None})

    if not pieces:
        return []

    if all(p["missing"] for p in pieces):
        pieces.sort(key=lambda z: z["order"])
    else:
        missing = [p for p in pieces if p["missing"]]
        positioned = [p for p in pieces if not p["missing"]]
        positioned.sort(key=lambda z: (float(z["y"]), z["order"]))
        pieces = positioned + sorted(missing, key=lambda z: z["order"])

    out: List[Dict[str, Any]] = []
    tie = 0
    for z in pieces:
        if z["k"] == "p":
            row: Dict[str, Any] = {
                "kind": "non_table_paragraph",
                "tie": tie,
                "text": z["text"],
                "source": z.get("source") or "f20_block",
            }
            if z.get("page") is not None:
                row["page"] = z["page"]
            if z.get("x0") is not None:
                row["x0"] = z["x0"]
            if not z["missing"]:
                row["sort_y"] = z["y"]
            else:
                row["position_contract"] = "missing_y0"
            out.append(row)
        else:
            ent = dict(z["entry"])
            ent["tie"] = tie
            if not z["missing"]:
                ent["sort_y"] = z["y"]
            out.append(ent)
        tie += 1
    return out


def _table_bbox(table: Dict[str, Any]) -> Any:
    bb = table.get("bbox")
    if bb is not None:
        return bb
    meta = table.get("metadata")
    if isinstance(meta, dict):
        return meta.get("bbox")
    return None


def build_f17_reading_stream(
    *,
    prose_blocks: List[Dict[str, Any]],
    tables: List[Dict[str, Any]],
    document_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    F11 の地の文ブロックと表リストを座標順に混在させる（読み順の正本）。

    表エントリは ``table_ref``（行データは未加工の consolidated_tables を参照）。
    """
    page_h = _infer_page_height_pt(document_info)

    prose_rows: List[Dict[str, Any]] = []
    for i, ob in enumerate(prose_blocks or []):
        if not isinstance(ob, dict):
            continue
        text = str(ob.get("text") or "").strip()
        if not text:
            continue
        sy = _normalize_block_sort_y(float(ob.get("y0", 0) or 0.0), page_h)
        prose_rows.append(
            {
                "text": text,
                "sort_y": sy,
                "source": ob.get("source"),
                "page": ob.get("page"),
                "x0": ob.get("x0"),
                "order": i,
            }
        )

    table_entries: List[Dict[str, Any]] = []
    for j, tbl in enumerate(tables or []):
        if not isinstance(tbl, dict):
            continue
        tid = str(tbl.get("table_id") or "").strip()
        if not tid:
            continue
        sy = _bbox_top_for_sort_y(_table_bbox(tbl), page_h)
        ent: Dict[str, Any] = {
            "kind": "table_ref",
            "table_id": tid,
            "source": tbl.get("source"),
            "order": j,
        }
        if sy is not None:
            ent["sort_y"] = sy
        else:
            ent["position_contract"] = "missing_table_bbox"
        table_entries.append(ent)

    stream = _interleave_prose_and_tables(prose_rows, table_entries)
    stream.sort(
        key=lambda e: (
            float(e["sort_y"]) if e.get("sort_y") is not None else 1e9,
            int(e.get("tie", 0)),
        )
    )
    return stream


__all__ = ["build_f17_reading_stream"]
