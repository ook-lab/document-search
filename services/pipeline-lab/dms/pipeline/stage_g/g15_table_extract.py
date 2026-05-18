"""
G15: F17 の consolidated_tables を表処理入力に取り出す（bbox 付与・地の文バンドルは別）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


def extract_tables_from_stage_f(stage_f_result: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, float]]:
    tables = list(stage_f_result.get("consolidated_tables") or [])
    bbox_by_id: Dict[str, float] = {}
    for t in tables:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("table_id") or "")
        bb = t.get("bbox")
        if tid and bb is not None:
            bbox_by_id[tid] = bb
    return tables, bbox_by_id


def attach_bbox_to_ui_tables(
    ui_tables: List[Dict[str, Any]],
    bbox_by_id: Dict[str, Any],
) -> None:
    for ut in ui_tables:
        if not isinstance(ut, dict):
            continue
        tid = str(ut.get("table_id") or "")
        base = re.sub(r"_(?:[SF]\d+|S\d+)$", "", tid)
        bb = bbox_by_id.get(tid) or bbox_by_id.get(base)
        if bb is not None:
            meta = dict(ut.get("metadata") or {})
            meta.setdefault("bbox", bb)
            ut["metadata"] = meta


__all__ = ["extract_tables_from_stage_f", "attach_bbox_to_ui_tables"]
