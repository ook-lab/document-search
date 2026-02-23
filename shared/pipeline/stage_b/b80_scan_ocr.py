"""
B-80: スキャンOCRプロセッサ（スタブ）

対象:
  - has_selectable_text=False のページ（真正スキャン）

TODO: 実装が必要。現在はスタブとして登録のみ。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger


class B80ScanOCRProcessor:
    """B-80: スキャンOCRプロセッサ（未実装スタブ）"""

    def process(
        self,
        file_path: str | Path,
        masked_pages: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        file_path = Path(file_path)
        logger.warning(f"[B-80] B80ScanOCRProcessor は未実装です: {file_path.name}")
        return {
            "is_structured": False,
            "error": "B80_NOT_IMPLEMENTED: スキャンOCRプロセッサは未実装です",
            "processor_name": "B80_SCAN_OCR",
        }
