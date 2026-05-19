"""後方互換: `run_g25_semantics` → G26。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from dms.pipeline.stage_g.g26_line_semantics import G26_LINE_SEMANTICS_CONTRACT, G26SemanticAIError
from dms.pipeline.stage_g.g26_semantic_estimator import G26SemanticEstimator
from dms.pipeline.stage_g.g45_d_line_orchestrator import run_g45_apply_split


def _structured_to_e14(structured_tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in structured_tables:
        if not isinstance(t, dict):
            continue
        headers = list(t.get("headers") or [])
        rows = [list(r) for r in (t.get("rows") or [])]
        data: List[List[Any]] = []
        if headers:
            data.append(headers)
        data.extend(rows)
        out.append(
            {
                "table_id": t.get("table_id", ""),
                "sub_tables": [{"sub_table_id": "", "data": data}],
            }
        )
    return out


def run_g25_semantics(
    chain_context: Optional[Dict[str, Any]],
    structured_tables: List[Dict[str, Any]],
    *,
    document_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    ctx = dict(chain_context or {})
    digest = dict(ctx.get("stage_d_line_digest") or {})
    if not digest.get("available"):
        logger.info("[G25] stage_d_line_digest なし → スキップ")
        return ctx, structured_tables

    semantic, _, _ = G26SemanticEstimator(document_id=document_id).infer_all(
        _structured_to_e14(structured_tables),
        chain_context={
            "stage_d_line_digest": digest,
            "structured_tables": structured_tables,
        },
    )
    lsa = semantic.get("line_semantics_ai")
    if not isinstance(lsa, dict) or lsa.get("line_semantics_contract") != G26_LINE_SEMANTICS_CONTRACT:
        raise G26SemanticAIError("g26_contract_missing")

    digest["line_semantics_ai"] = lsa
    ctx["stage_d_line_digest"] = digest
    ctx["line_semantics_ai"] = lsa
    return ctx, structured_tables


run_g25_apply_split = run_g45_apply_split


def run_g25_semantics_and_split(
    chain_context: Optional[Dict[str, Any]],
    structured_tables: List[Dict[str, Any]],
    *,
    document_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    ctx, tables = run_g25_semantics(
        chain_context, structured_tables, document_id=document_id
    )
    return run_g25_apply_split(ctx, tables)
