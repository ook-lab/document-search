"""G61 / G62 用の専用ログファイルパス。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

G61_LOG_NAME = "g61_layout_bridge.log"
G62_LOG_NAME = "g62_table_ai_processor.log"


def dedicated_g61_chain_log_paths(table_log_dir: Optional[Path]) -> Tuple[Optional[str], Optional[str]]:
    """(G61 ブリッジログ, G62 配置ログ) のパス。"""
    if not table_log_dir:
        return None, None
    d = Path(table_log_dir)
    return str(d / G61_LOG_NAME), str(d / G62_LOG_NAME)
