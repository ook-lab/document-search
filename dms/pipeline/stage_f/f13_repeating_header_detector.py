"""後方互換: 実装は `stage_g.g41_repeating_header_detector`（G41）。"""
from dms.pipeline.stage_g.g41_repeating_header_detector import G41RepeatingHeaderDetector as F13RepeatingHeaderDetector
from dms.pipeline.stage_g.g41_repeating_header_detector import G41RepeatingHeaderDetector as F55RepeatingHeaderDetector

__all__ = ["F13RepeatingHeaderDetector", "F55RepeatingHeaderDetector"]
