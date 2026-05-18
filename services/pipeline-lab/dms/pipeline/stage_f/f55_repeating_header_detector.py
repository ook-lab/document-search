"""後方互換: 実装は `stage_g.g41_repeating_header_detector`（G41）。"""
from dms.pipeline.stage_g.g41_ai_layout_splitter import suggest_ai_table_split
from dms.pipeline.stage_g.g41_repeating_header_detector import G41RepeatingHeaderDetector as F55RepeatingHeaderDetector

__all__ = ["F55RepeatingHeaderDetector", "suggest_ai_table_split"]
