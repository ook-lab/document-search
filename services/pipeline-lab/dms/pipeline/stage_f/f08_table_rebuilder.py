"""後方互換: 実装は `stage_g.g22_table_rebuilder`（G22）。"""
from dms.pipeline.stage_g.g22_table_rebuilder import G22TableRebuilder as F08TableRebuilder
from dms.pipeline.stage_g.g22_table_rebuilder import G22TableRebuilder as F52TableRebuilder

__all__ = ["F08TableRebuilder", "F52TableRebuilder"]
