"""後方互換 → `g61_layout_bridge`（G61）。"""
from dms.pipeline.stage_g.g61_layout_bridge import (  # noqa: F401
    G61LayoutBridgeProcessor,
    G61LayoutBridgeProcessor as G27TableLineSemanticsProcessor,
    _build_assigned_lines,
    _pick_d_digest_row,
)

__all__ = [
    "G61LayoutBridgeProcessor",
    "G27TableLineSemanticsProcessor",
    "_build_assigned_lines",
    "_pick_d_digest_row",
]
