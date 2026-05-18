"""
G45: G26 導出の table_layout_plans に従い表を物理分割（G44 互換）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from dms.pipeline.stage_g.g44_table_reconstructor import G44TableReconstructor
from dms.pipeline.stage_g.g26_line_semantics import (
    G26SemanticAIError,
    G26_LINE_SEMANTICS_CONTRACT,
    plan_to_g44_detection,
)

G45_D_LINE_SPLIT_CONTRACT = "g26_ai_layout_plan_v1"


def _table_full_grid(table_rec: Dict[str, Any]) -> List[List[Any]]:
    headers = table_rec.get("headers") or []
    rows = table_rec.get("rows") or []
    full: List[List[Any]] = []
    if headers:
        full.append(list(headers))
    full.extend(list(r) for r in rows)
    return full


def _plan_for_index(
    plans: List[Dict[str, Any]], index: int, table_id: str
) -> Optional[Dict[str, Any]]:
    for p in plans:
        if not isinstance(p, dict):
            continue
        ti = p.get("table_index")
        if ti is not None:
            try:
                if int(ti) == index:
                    return p
            except (TypeError, ValueError):
                pass
        tid = str(p.get("table_id") or "")
        if tid and (tid == table_id or tid in table_id or table_id in tid):
            return p
    return None


def apply_d_line_split_structured_tables(
    structured_tables: List[Dict[str, Any]],
    digest: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    ai = digest.get("line_semantics_ai") or {}
    if ai.get("line_semantics_contract") != G26_LINE_SEMANTICS_CONTRACT:
        if digest.get("lines"):
            raise G26SemanticAIError("g26_split_requires_ai")
        return structured_tables, {"applied": False, "contract": None}

    plans = ai.get("table_layout_plans") or []
    if not plans:
        logger.info("[G45] 分割方針なし → 表はそのまま")
        return structured_tables, {"applied": False, "contract": None}

    recon = G44TableReconstructor()
    out_tables: List[Dict[str, Any]] = []
    split_count = 0

    for idx, table_rec in enumerate(structured_tables):
        plan = _plan_for_index(plans, idx, str(table_rec.get("table_id") or ""))
        if not plan or plan.get("split_axis") == "none":
            out_tables.append(table_rec)
            continue

        if plan.get("split_axis") == "col":
            logger.info(
                f"[G45] {table_rec.get('table_id')}: 列分割は G41 のみ（物理分割しない）"
            )
            out_tables.append(table_rec)
            continue

        full = _table_full_grid(table_rec)
        detection = plan_to_g44_detection(plan)
        subs = recon.reconstruct(full, detection)
        if len(subs) <= 1 and subs and subs[0].get("split_axis") == "none":
            out_tables.append(table_rec)
            continue

        split_count += 1
        parent_meta = dict(table_rec.get("metadata") or {})
        parent_meta["g26_split_axis"] = plan.get("split_axis")
        parent_meta["g26_split_reason"] = plan.get("reason")
        for si, sub in enumerate(subs, 1):
            data = sub.get("data") or []
            headers = list(data[0]) if data else list(table_rec.get("headers") or [])
            rows = [list(r) for r in data[1:]] if len(data) > 1 else []
            out_tables.append(
                {
                    "headers": headers,
                    "rows": rows,
                    "table_id": f"{table_rec.get('table_id', 'T')}_F{si}",
                    "source_page": table_rec.get("source_page"),
                    "metadata": {
                        **parent_meta,
                        "split_source": G45_D_LINE_SPLIT_CONTRACT,
                        "split_axis": sub.get("split_axis"),
                        "d_line_split_group": sub.get("group_name"),
                    },
                }
            )
        logger.info(
            f"[G45] {table_rec.get('table_id')}: {plan.get('split_axis')} "
            f"→ {len(subs)} 表 ({plan.get('reason', '')[:50]!r})"
        )

    meta = {
        "applied": split_count > 0,
        "contract": G45_D_LINE_SPLIT_CONTRACT if split_count > 0 else None,
        "tables_in": len(structured_tables),
        "tables_out": len(out_tables),
        "split_parents": split_count,
    }
    if split_count:
        logger.info(f"[G45] 完了: {meta['tables_in']}→{meta['tables_out']}表")
    return out_tables, meta
