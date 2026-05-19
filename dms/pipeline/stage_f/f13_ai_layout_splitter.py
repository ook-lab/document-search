"""後方互換: 実装は `stage_g.g41_ai_layout_splitter`（G41）。"""
from dms.pipeline.stage_g.g41_ai_layout_splitter import (
    G41LayoutAIRequiredError as F55LayoutAIRequiredError,
    G41_LAYOUT_AI_CONTRACT as F55_LAYOUT_AI_CONTRACT,
    _normalize_detection,
    suggest_ai_table_split,
)

__all__ = [
    "F55LayoutAIRequiredError",
    "F55_LAYOUT_AI_CONTRACT",
    "_normalize_detection",
    "suggest_ai_table_split",
]
