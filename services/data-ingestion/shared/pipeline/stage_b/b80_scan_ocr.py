"""
B-80: スキャンOCRプロセッサ

対象:
  - has_selectable_text=False のページ（真正スキャン）

スキャン文書は選択可能テキストが存在しないため、
テキスト削除（purge）は不要。入力PDFをそのままコピーして
purged_pdf_path として Stage D に渡す。

テキスト抽出は Stage E（OCR）が担当する。
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class B80ScanOCRProcessor:
    """B-80: スキャンOCRプロセッサ"""

    def process(
        self,
        file_path: str | Path,
        masked_pages: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        file_path = Path(file_path)
        logger.info(f"[B-80] スキャン処理開始: {file_path.name}")

        if not file_path.exists():
            logger.error(f"[B-80] ファイルが存在しません: {file_path}")
            return self._error_result(f"ファイルが見つかりません: {file_path}")

        try:
            # スキャン文書は選択可能テキストがないため purge = コピーのみ
            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b80_{file_path.stem}_purged.pdf"

            shutil.copy2(str(file_path), str(purged_pdf_path))
            logger.info(f"[B-80] purged PDF 保存（コピー）: {purged_pdf_path.name}")

            _masked = set(masked_pages or [])
            if _masked:
                logger.debug(f"[B-80] masked_pages={sorted(_masked)}（スキャンのため影響なし）")

            return {
                'is_structured': True,
                'purged_pdf_path': str(purged_pdf_path),
                'logical_blocks': [],      # テキスト抽出は Stage E（OCR）が担当
                'structured_tables': [],   # 表検出は Stage D が担当
                'text_with_tags': '',
                'tags': {
                    'is_scan': True,
                    'processor': 'B80_SCAN_OCR',
                },
                'processor_name': 'B80_SCAN_OCR',
            }

        except Exception as e:
            logger.error(f"[B-80] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        return {
            'is_structured': False,
            'error': error_message,
            'purged_pdf_path': '',
            'logical_blocks': [],
            'structured_tables': [],
            'text_with_tags': '',
            'tags': {},
            'processor_name': 'B80_SCAN_OCR',
        }
