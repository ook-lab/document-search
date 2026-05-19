"""後方互換: 実装は `g61_layout_bridge`（G61）。"""
from dms.pipeline.stage_g.g61_layout_bridge import (
    G61LayoutBridgeProcessor as F46TableLineSemanticsProcessor,
    _build_assigned_lines,
)

__all__ = ["F46TableLineSemanticsProcessor", "_build_assigned_lines"]
