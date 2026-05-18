"""後方互換 → `g36_lr_vertical_orchestrator`（G36 直す）。"""
from dms.pipeline.stage_g.g36_lr_vertical_orchestrator import *  # noqa: F403
from dms.pipeline.stage_g.g36_lr_vertical_orchestrator import run_g36_on_structured_tables as run_g28_on_structured_tables
