"""後方互換: 表理解 LLM は `G26SemanticEstimator` のみ。"""

from dms.pipeline.stage_g.g26_semantic_estimator import (  # noqa: F401
    G26SemanticEstimator as F46SemanticEstimator,
    G26_TABLE_UNDERSTANDING_CONTRACT,
    _f13_layout_hint_section,
    build_g41_detection_from_entry,
    propagate_semantics_to_sub_tables,
)

__all__ = [
    "F46SemanticEstimator",
    "G26_TABLE_UNDERSTANDING_CONTRACT",
    "_f13_layout_hint_section",
    "build_g41_detection_from_entry",
    "propagate_semantics_to_sub_tables",
]
