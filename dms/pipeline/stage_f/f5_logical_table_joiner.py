"""後方互換: F17 / F5LogicalTableJoiner → F17StageFFinalize。"""

from .f17_stage_f_finalize import F17StageFFinalize

F5LogicalTableJoiner = F17StageFFinalize

__all__ = ["F5LogicalTableJoiner", "F17StageFFinalize"]
