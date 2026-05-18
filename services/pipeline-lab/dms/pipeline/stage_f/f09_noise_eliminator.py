"""後方互換: 実装は `stage_g.g19_ui_assembly`（G19）。"""
from dms.pipeline.stage_g.g19_ui_assembly import G19UIAssembly as F09NoiseEliminator
from dms.pipeline.stage_g.g19_ui_assembly import G19UIAssembly as F43NoiseEliminator

__all__ = ["F09NoiseEliminator", "F43NoiseEliminator"]
