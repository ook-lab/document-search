"""後方互換: 物理分割は `g45_d_line_split`（G26 導出 plans）。"""

from dms.pipeline.stage_g.g45_d_line_split import (  # noqa: F401
    G45_D_LINE_SPLIT_CONTRACT as F50_D_LINE_SPLIT_CONTRACT,
    apply_d_line_split_structured_tables,
)
