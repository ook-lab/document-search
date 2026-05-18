"""
Stage G サブステージ ID。

番号帯: G20=理解 / G30=直す / G40=切る / G60=配置（G61→G62）
"""

from __future__ import annotations

G11 = "G11"
G15 = "G15"
G22 = "G22"
G24 = "G24"

# G20 理解
G25 = "G25"  # 後方互換 shim のみ（実行チェーン外）
G26 = "G26"
G27 = "G27"  # 後方互換 shim → G61

# G30 直す
G36 = "G36"

# G40 切る
G41 = "G41"
G44 = "G44"
G45 = "G45"

# G60 配置
G61 = "G61"
G62 = "G62"
G65 = "G65"

STAGE_G_SUBSTAGES: tuple[str, ...] = (G11,)

TABLE_MICRO_CHAIN_EXECUTION: tuple[str, ...] = (
    G15,
    G22,
    G24,
    G26,
    G36,
    G41,
    G44,
    G45,
    G61,
    G62,
    G65,
)

__all__ = [
    "G11",
    "G15",
    "G22",
    "G24",
    "G25",
    "G26",
    "G27",
    "G36",
    "G41",
    "G44",
    "G45",
    "G61",
    "G62",
    "G65",
    "STAGE_G_SUBSTAGES",
    "TABLE_MICRO_CHAIN_EXECUTION",
]
