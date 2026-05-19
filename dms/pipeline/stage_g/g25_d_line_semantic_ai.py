"""後方互換: 実装は `g26_line_semantics` / `g26_semantic_estimator`。"""

from dms.pipeline.stage_g.g26_line_semantics import (  # noqa: F401
    F50DLineSemanticAIError,
    F50_D_LINE_CONTRACT,
    G25DLineSemanticAIError,
    G25_D_LINE_CONTRACT,
    VALID_LINE_ROLES as VALID_ROLES,
    _lines_preview,
    _parse_lines,
    _parse_table_layout_plans,
    _structured_tables_preview,
    plan_to_f56_detection,
    plan_to_g44_detection,
)

__all__ = [
    "G25DLineSemanticAIError",
    "G25_D_LINE_CONTRACT",
    "F50DLineSemanticAIError",
    "F50_D_LINE_CONTRACT",
    "VALID_ROLES",
    "_lines_preview",
    "_structured_tables_preview",
    "_parse_lines",
    "_parse_table_layout_plans",
    "plan_to_f56_detection",
    "plan_to_g44_detection",
]


from dms.pipeline.stage_g.g26_line_semantics import G26SemanticAIError  # noqa: F401


def infer_d_line_semantics_ai(*_args, **_kwargs):
    raise G26SemanticAIError(
        "infer_d_line_semantics_ai removed; use G26SemanticEstimator.infer_all via G11"
    )
