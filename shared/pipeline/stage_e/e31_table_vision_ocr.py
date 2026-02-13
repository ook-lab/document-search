"""
E-31: Table Cell OCR（セル単位 Vision OCR）

E-30 が確定したセル bbox に基づき、表画像から
セルごとに切り出して Vision API OCR を実行する。

正しい依存順：
  E-30（構造：セルbbox確定）→ E-31（セルOCR）→ E-32（合成）

入力：
  image_path: 表画像
  cells: E-30 が返した cells リスト（row, col, x0, y0, x1, y1 を含む）

出力：
  cell_texts: [{row, col, text, confidence}]
  route: "E31_CELL_OCR"
"""

import io
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("[E-31] Pillow がインストールされていません（セルcropに必要）")

try:
    from google.cloud import vision as gcloud_vision
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    logger.warning("[E-31] google-cloud-vision がインストールされていません")


class E31TableVisionOcr:
    """E-31: Table Cell OCR（セル単位）"""

    # セル数がこれを超えたら Vision API 呼び出しをスキップ（コスト保護）
    MAX_CELLS_FOR_OCR = 200

    def extract_cells(
        self,
        image_path: Path,
        cells: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        各セルの bbox で画像をcropし、Vision API OCR を実行。

        Args:
            image_path: 表画像パス
            cells: E-30 の cells リスト
                   [{row, col, x0, y0, x1, y1, rowspan, colspan}, ...]

        Returns:
            {
                'success': bool,
                'cell_texts': [
                    {'row': int, 'col': int, 'text': str, 'confidence': float}
                ],
                'ocr_engine': 'VISION',
                'route': 'E31_CELL_OCR',
                'cells_processed': int
            }
        """
        if not cells:
            logger.info("[E-31] セルなし → スキップ")
            return self._empty_result()

        if not PIL_AVAILABLE:
            logger.warning("[E-31] Pillow 未インストール → 空テキストで続行")
            return self._fallback_result(cells)

        if not VISION_AVAILABLE:
            logger.warning("[E-31] google-cloud-vision 未インストール → 空テキストで続行")
            return self._fallback_result(cells)

        if len(cells) > self.MAX_CELLS_FOR_OCR:
            logger.warning(
                f"[E-31] セル数 {len(cells)} > {self.MAX_CELLS_FOR_OCR}（上限）"
                " → 空テキストで続行"
            )
            return self._fallback_result(cells)

        logger.info(f"[E-31] セル OCR 開始: {image_path.name}, {len(cells)}セル")

        try:
            img = PILImage.open(image_path)
            w, h = img.size
            client = gcloud_vision.ImageAnnotatorClient()

            cell_texts = []

            for cell in cells:
                row = cell.get('row', 0)
                col = cell.get('col', 0)

                # 正規化座標 → ピクセル座標（clamp to image bounds）
                x0 = max(0, int(cell.get('x0', 0.0) * w))
                y0 = max(0, int(cell.get('y0', 0.0) * h))
                x1 = min(w, int(cell.get('x1', 1.0) * w))
                y1 = min(h, int(cell.get('y1', 1.0) * h))

                # 有効なcropサイズか確認
                if x1 <= x0 or y1 <= y0:
                    logger.debug(f"[E-31] R{row}C{col}: bbox 不正({x0},{y0},{x1},{y1}) → 空")
                    cell_texts.append({'row': row, 'col': col, 'text': '', 'confidence': 0.0})
                    continue

                # セルを切り出し
                crop = img.crop((x0, y0, x1, y1))

                # PNG バイトに変換
                buf = io.BytesIO()
                crop.save(buf, format='PNG')
                content = buf.getvalue()

                # Vision API OCR
                vision_image = gcloud_vision.Image(content=content)
                response = client.document_text_detection(image=vision_image)

                if response.error.message:
                    logger.debug(
                        f"[E-31] R{row}C{col}: Vision エラー:"
                        f" {response.error.message}"
                    )
                    text = ''
                    confidence = 0.0
                else:
                    annotation = response.full_text_annotation
                    text = annotation.text.strip() if annotation else ''
                    confidence = 1.0 if text else 0.0

                cell_texts.append({'row': row, 'col': col, 'text': text, 'confidence': confidence})
                logger.debug(f"[E-31] R{row}C{col}: '{text[:30]}'")

            logger.info(f"[E-31] セル OCR 完了: {len(cell_texts)}セル")

            return {
                'success': True,
                'cell_texts': cell_texts,
                'ocr_engine': 'VISION',
                'route': 'E31_CELL_OCR',
                'cells_processed': len(cell_texts)
            }

        except Exception as e:
            logger.warning(f"[E-31] セル OCR 失敗 → 空テキストで続行: {e}")
            return self._fallback_result(cells)

    def _fallback_result(self, cells: List[Dict[str, Any]]) -> Dict[str, Any]:
        """失敗時 / ライブラリ未インストール時：全セルを空テキストで返す"""
        cell_texts = [
            {'row': c.get('row', 0), 'col': c.get('col', 0), 'text': '', 'confidence': 0.0}
            for c in cells
        ]
        return {
            'success': True,
            'cell_texts': cell_texts,
            'ocr_engine': 'NONE',
            'route': 'E31_CELL_OCR',
            'cells_processed': len(cell_texts)
        }

    def _empty_result(self) -> Dict[str, Any]:
        return {
            'success': True,
            'cell_texts': [],
            'ocr_engine': 'NONE',
            'route': 'E31_CELL_OCR',
            'cells_processed': 0
        }
