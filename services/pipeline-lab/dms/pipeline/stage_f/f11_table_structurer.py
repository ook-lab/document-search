"""後方互換: 実装は `stage_g.g24_table_structurer`（G24）。"""
from dms.pipeline.stage_g.g24_table_structurer import G24TableStructurer as F11TableStructurer
from dms.pipeline.stage_g.g24_table_structurer import G24TableStructurer as F54TableStructurer

__all__ = ["F11TableStructurer", "F54TableStructurer"]
