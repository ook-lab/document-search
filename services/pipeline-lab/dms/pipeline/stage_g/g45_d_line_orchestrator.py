"""G45: D 罫線 digest の物理分割（切る）。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from loguru import logger

from dms.pipeline.stage_g.g45_d_line_split import apply_d_line_split_structured_tables


def run_g45_apply_split(
    chain_context: Dict[str, Any],
    structured_tables: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """G25 意味付与済み digest を structured_tables に物理適用。"""
    ctx = dict(chain_context or {})
    digest = dict(ctx.get("stage_d_line_digest") or {})
    if not digest.get("line_semantics_ai"):
        return ctx, structured_tables

    logger.info("[G45] D 罫線物理分割")
    new_tables, split_meta = apply_d_line_split_structured_tables(structured_tables, digest)
    ctx["d_line_split"] = split_meta
    if split_meta.get("contract"):
        ctx["d_line_split_contract"] = split_meta["contract"]
    return ctx, new_tables
