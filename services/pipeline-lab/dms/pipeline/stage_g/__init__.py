"""
Stage G: レビュー UI 組立（表チェーン + 地の文載せ替え）。

トップレベル: A → B → D → E → F（F11→F13→F17）→ **G（G11）**
"""

from dms.pipeline.stage_g.g11_controller import F60UIDeliveryController, G11Controller
from dms.pipeline.stage_g.substage_ids import STAGE_G_SUBSTAGES, TABLE_MICRO_CHAIN_EXECUTION

__all__ = [
    "G11Controller",
    "F60UIDeliveryController",
    "STAGE_G_SUBSTAGES",
    "TABLE_MICRO_CHAIN_EXECUTION",
]
