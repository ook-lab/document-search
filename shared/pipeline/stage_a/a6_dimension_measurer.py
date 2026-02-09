"""
A-6: Document Dimension Measurer（書類サイズ測定）

PDFの各ページの物理サイズ（pt/mm）を取得し、
全ページのサイズが同一か混在しているかを判定する。
"""

from pathlib import Path
from typing import Dict, Any, List
from loguru import logger


class A6DimensionMeasurer:
    """A-6: Document Dimension Measurer（書類サイズ測定）"""

    # pt → mm 変換係数（1 pt = 1/72 inch = 25.4/72 mm）
    PT_TO_MM = 25.4 / 72.0

    def measure(self, file_path: Path) -> Dict[str, Any]:
        """
        PDFのページサイズを測定

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'page_count': int,
                'dimensions': {
                    'width': float,   # 代表ページの幅（pt）
                    'height': float,  # 代表ページの高さ（pt）
                    'unit': 'pt'
                },
                'dimensions_mm': {
                    'width': float,   # 代表ページの幅（mm）
                    'height': float,  # 代表ページの高さ（mm）
                    'unit': 'mm'
                },
                'is_multi_size': bool,  # マルチサイズかどうか
                'page_sizes': [...]     # 各ページのサイズリスト
            }
        """
        logger.info(f"[A-6] サイズ測定開始: {file_path.name}")

        # ページサイズを取得
        page_sizes = self._extract_page_sizes(file_path)

        if not page_sizes:
            logger.error("[A-6] ページサイズが取得できませんでした")
            return self._empty_result()

        page_count = len(page_sizes)
        logger.info(f"[A-6] ページ数: {page_count}")

        # 代表ページサイズ（最初のページ）
        representative_size = page_sizes[0]
        width_pt = representative_size['width']
        height_pt = representative_size['height']

        # mm変換
        width_mm = width_pt * self.PT_TO_MM
        height_mm = height_pt * self.PT_TO_MM

        # マルチサイズ判定（サイズが異なるページがあるか）
        is_multi_size = self._check_multi_size(page_sizes)

        if is_multi_size:
            logger.warning(f"[A-6] マルチサイズ検出: {page_count}ページ中、複数のサイズが混在")
        else:
            logger.info(f"[A-6] 全ページ同一サイズ: {width_pt:.2f} x {height_pt:.2f} pt ({width_mm:.2f} x {height_mm:.2f} mm)")

        return {
            'page_count': page_count,
            'dimensions': {
                'width': width_pt,
                'height': height_pt,
                'unit': 'pt'
            },
            'dimensions_mm': {
                'width': width_mm,
                'height': height_mm,
                'unit': 'mm'
            },
            'is_multi_size': is_multi_size,
            'page_sizes': page_sizes
        }

    def _extract_page_sizes(self, file_path: Path) -> List[Dict[str, Any]]:
        """
        各ページのサイズを取得（pdfplumber優先、フォールバックでPyMuPDF）

        Args:
            file_path: PDFファイルパス

        Returns:
            [{
                'page': int,
                'width': float,   # pt
                'height': float,  # pt
            }, ...]
        """
        page_sizes = []

        # pdfplumberで取得を試行
        try:
            import pdfplumber
            with pdfplumber.open(str(file_path)) as pdf:
                for idx, page in enumerate(pdf.pages):
                    page_sizes.append({
                        'page': idx,
                        'width': float(page.width),
                        'height': float(page.height)
                    })
                logger.debug(f"[A-6] pdfplumberでサイズ取得: {len(page_sizes)}ページ")
                return page_sizes
        except Exception as e:
            logger.warning(f"[A-6] pdfplumberサイズ取得失敗: {e}")

        # PyMuPDFで取得を試行
        try:
            import fitz
            doc = fitz.open(str(file_path))
            for idx in range(len(doc)):
                page = doc[idx]
                rect = page.rect
                page_sizes.append({
                    'page': idx,
                    'width': float(rect.width),
                    'height': float(rect.height)
                })
            doc.close()
            logger.debug(f"[A-6] PyMuPDFでサイズ取得: {len(page_sizes)}ページ")
            return page_sizes
        except Exception as e:
            logger.warning(f"[A-6] PyMuPDFサイズ取得失敗: {e}")

        return []

    def _check_multi_size(self, page_sizes: List[Dict[str, Any]]) -> bool:
        """
        マルチサイズかどうかを判定

        Args:
            page_sizes: ページサイズリスト

        Returns:
            True: マルチサイズ, False: 同一サイズ
        """
        if not page_sizes:
            return False

        # 最初のページのサイズを基準とする
        base_width = page_sizes[0]['width']
        base_height = page_sizes[0]['height']

        # 許容誤差（0.1 pt = 約0.035 mm）
        tolerance = 0.1

        for size in page_sizes[1:]:
            if (abs(size['width'] - base_width) > tolerance or
                abs(size['height'] - base_height) > tolerance):
                return True

        return False

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'page_count': 0,
            'dimensions': {'width': 0, 'height': 0, 'unit': 'pt'},
            'dimensions_mm': {'width': 0, 'height': 0, 'unit': 'mm'},
            'is_multi_size': False,
            'page_sizes': []
        }
