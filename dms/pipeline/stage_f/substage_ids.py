"""

Stage F サブステージ ID（数値＝実行順）。



F11–F17 … データ統合・正規化・出口（Worker / debug のトップレベル）

表 UI チェーンは Stage G — `dms.pipeline.stage_g.substage_ids`

"""



from __future__ import annotations



F11 = "F11"

F13 = "F13"

F17 = "F17"



STAGE_F_SUBSTAGES: tuple[str, ...] = (F11, F13, F17)





def log_tag(substage_id: str) -> str:

    return f"[{substage_id}]"





__all__ = [

    "F11",

    "F13",

    "F17",

    "STAGE_F_SUBSTAGES",

    "log_tag",

]

