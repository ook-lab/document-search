"""表チェーン（F57 / F58）用の専用ログファイルパス解決。ログファイル名は後方互換のため f46/f47 のまま。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

F46_LOG_NAME = "f46_table_line_semantics.log"
F47_LOG_NAME = "f47_table_ai_processor.log"


def dedicated_f46_f47_log_paths(table_log_dir: Optional[Path]) -> Tuple[Optional[str], Optional[str]]:
    """
    同一ディレクトリに F57 / F58 専用ログ（別ファイル＝別ウィンドウ想定）を置く。

    Args:
        table_log_dir: 出力先ディレクトリ（None なら専用ファイルなし）

    Returns:
        (f46_log_path, f47_log_path) いずれも str または None
    """
    if not table_log_dir:
        return None, None
    d = Path(table_log_dir)
    return str(d / F46_LOG_NAME), str(d / F47_LOG_NAME)
