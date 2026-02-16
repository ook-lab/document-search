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
        logger.info(f"[A-3 DimensionMeasurer] サイズ測定開始: {file_path.name}")

        # ページサイズを取得
        page_sizes = self._extract_page_sizes(file_path)

        if not page_sizes:
            logger.error("[A-3 DimensionMeasurer] ページサイズが取得できませんでした")
            return self._empty_result()

        page_count = len(page_sizes)
        logger.info(f"[A-3 DimensionMeasurer] ページ数: {page_count}")

        # 代表ページサイズ（最初のページ）
        representative_size = page_sizes[0]
        width_pt = representative_size['width']
        height_pt = representative_size['height']

        # mm変換
        width_mm = width_pt * self.PT_TO_MM
        height_mm = height_pt * self.PT_TO_MM

        logger.info("[A-3 DimensionMeasurer] 代表ページサイズ（Page 0）:")
        logger.info(f"  ├─ {width_pt:.2f} x {height_pt:.2f} pt")
        logger.info(f"  └─ {width_mm:.2f} x {height_mm:.2f} mm")

        # マルチサイズ判定（サイズが異なるページがあるか）
        is_multi_size = self._check_multi_size(page_sizes)

        if is_multi_size:
            logger.warning(f"[A-3 DimensionMeasurer] マルチサイズ検出: {page_count}ページ中、複数のサイズが混在")
            # マルチサイズの詳細をログ出力
            logger.info("[A-3 DimensionMeasurer] ページ別サイズ詳細:")
            sample_pages = page_sizes[:5] if len(page_sizes) > 5 else page_sizes
            for ps in sample_pages:
                w_mm = ps['width'] * self.PT_TO_MM
                h_mm = ps['height'] * self.PT_TO_MM
                logger.info(
                    f"  ├─ Page {ps['page']}: "
                    f"{ps['width']:.2f} x {ps['height']:.2f} pt "
                    f"({w_mm:.2f} x {h_mm:.2f} mm)"
                )
            if len(page_sizes) > 5:
                logger.info(f"  └─ ... ({len(page_sizes) - 5} ページ省略)")
        else:
            logger.info(f"[A-3 DimensionMeasurer] 全ページ同一サイズ: {width_pt:.2f} x {height_pt:.2f} pt ({width_mm:.2f} x {height_mm:.2f} mm)")

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
            logger.info("[A-3 DimensionMeasurer] pdfplumberでページサイズ取得を試行...")
            with pdfplumber.open(str(file_path)) as pdf:
                for idx, page in enumerate(pdf.pages):
                    page_sizes.append({
                        'page': idx,
                        'width': float(page.width),
                        'height': float(page.height)
                    })
                logger.info(f"[A-3 DimensionMeasurer] pdfplumberでサイズ取得成功: {len(page_sizes)}ページ")
                return page_sizes
        except Exception as e:
            logger.warning(f"[A-3 DimensionMeasurer] pdfplumberサイズ取得失敗: {e}")

        # PyMuPDFで取得を試行
        try:
            import fitz
            logger.info("[A-3 DimensionMeasurer] PyMuPDFでページサイズ取得を試行...")
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
            logger.info(f"[A-3 DimensionMeasurer] PyMuPDFでサイズ取得成功: {len(page_sizes)}ページ")
            return page_sizes
        except Exception as e:
            logger.warning(f"[A-3 DimensionMeasurer] PyMuPDFサイズ取得失敗: {e}", exc_info=True)

        logger.error("[A-3 DimensionMeasurer] すべてのページサイズ取得方法が失敗しました")
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

        logger.info("[A-3 DimensionMeasurer] マルチサイズ判定:")
        logger.info(f"  ├─ 基準サイズ（Page 0）: {base_width:.2f} x {base_height:.2f} pt")
        logger.info(f"  ├─ 許容誤差: {tolerance} pt")

        for size in page_sizes[1:]:
            width_diff = abs(size['width'] - base_width)
            height_diff = abs(size['height'] - base_height)
            if width_diff > tolerance or height_diff > tolerance:
                logger.info(f"  ✓ マルチサイズ検出: Page {size['page']} が基準と異なる")
                logger.info(f"      ({size['width']:.2f} x {size['height']:.2f} pt, 差分: w={width_diff:.2f}, h={height_diff:.2f})")
                return True

        logger.info("  ✓ 全ページ同一サイズ")
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
