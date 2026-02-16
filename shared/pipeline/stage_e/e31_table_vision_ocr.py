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

    def __init__(self, next_stage=None):
        """
        E-31 初期化（チェーンパターン）

        Args:
            next_stage: 次のステージ（E-32）のインスタンス
        """
        self.next_stage = next_stage

    def extract_cells(
        self,
        image_path: Path,
        cells: List[Dict[str, Any]],
        struct_result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        ★修正: E-30で既にテキストを取得しているため、セルごとのVision API呼び出しをスキップ

        Args:
            image_path: 表画像パス
            cells: E-30 の cells リスト（既にtextを含む）
            struct_result: E-30の構造抽出結果（チェーン用）

        Returns:
            E-32の結果（チェーン経由）またはE-31の結果
        """
        logger.info("=" * 80)
        logger.info("[E-31] ★セルOCRスキップ（E-30で既にテキスト取得済み）")
        logger.info("=" * 80)

        # E-30で既にテキストが取得されているため、空のocr_resultを返す
        # （E-32がE-30のテキストをそのまま使用）
        ocr_result = {
            'success': True,
            'cell_texts': [],  # 空（E-30の結果を使用）
            'ocr_engine': 'GEMINI_E30',
            'route': 'E31_SKIP_ALREADY_EXTRACTED',
            'cells_processed': 0
        }

        logger.info("[E-31] E-30で取得済みのテキストをそのまま使用します")

        # ★チェーン: E-32を呼び出す
        if self.next_stage and struct_result:
            logger.info("[E-31] → 次のステージ（E-32）を呼び出します")
            return self.next_stage.merge(struct_result, ocr_result)

        return ocr_result

    def extract_cells_OLD_DESIGN(
        self,
        image_path: Path,
        cells: List[Dict[str, Any]],
        struct_result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        ★旧設計: セルごとにVision APIを呼ぶ（使用禁止）
        """
        if not cells:
            logger.info("[E-31] セルなし → スキップ")
            return self._empty_result(struct_result)

        if not PIL_AVAILABLE:
            logger.warning("[E-31] Pillow 未インストール → 空テキストで続行")
            return self._fallback_result(cells, struct_result)

        if not VISION_AVAILABLE:
            logger.warning("[E-31] google-cloud-vision 未インストール → 空テキストで続行")
            return self._fallback_result(cells, struct_result)

        if len(cells) > self.MAX_CELLS_FOR_OCR:
            logger.warning(
                f"[E-31] セル数 {len(cells)} > {self.MAX_CELLS_FOR_OCR}（上限）"
                " → 空テキストで続行"
            )
            return self._fallback_result(cells, struct_result)

        logger.info("=" * 80)
        logger.info(f"[E-31] セル OCR 開始: {image_path.name}, {len(cells)}セル")
        logger.info("=" * 80)

        try:
            img = PILImage.open(image_path)
            w, h = img.size
            logger.info(f"[E-31] 表画像サイズ: {w}x{h} pixels")

            client = gcloud_vision.ImageAnnotatorClient()

            cell_texts = []
            total_chars = 0
            non_empty_cells = 0

            for idx, cell in enumerate(cells):
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
                logger.debug(f"[E-31] R{row}C{col}: Vision API 呼び出し中... (bbox={x0},{y0},{x1},{y1})")
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

                    if text:
                        non_empty_cells += 1
                        total_chars += len(text)

                cell_texts.append({'row': row, 'col': col, 'text': text, 'confidence': confidence})
                logger.debug(f"[E-31] R{row}C{col}: '{text[:50]}' (confidence={confidence})")

                # 進捗表示（10セルごと）
                if (idx + 1) % 10 == 0:
                    logger.info(f"[E-31] 進捗: {idx + 1}/{len(cells)} セル処理完了")

            logger.info("=" * 80)
            logger.info(f"[E-31] セル OCR 完了")
            logger.info(f"  ├─ 総セル数: {len(cell_texts)}")
            logger.info(f"  ├─ 非空セル数: {non_empty_cells}")
            logger.info(f"  ├─ 総文字数: {total_chars}")
            logger.info(f"  ├─ 平均文字数/セル: {total_chars / max(non_empty_cells, 1):.1f}")
            logger.info(f"  └─ OCR engine: VISION")
            logger.info("=" * 80)

            # 全セルテキストのサンプル出力
            logger.info("[E-31] ===== セルテキスト一覧 =====")
            for cell_text in cell_texts:
                if cell_text.get('text'):
                    logger.info(f"R{cell_text['row']}C{cell_text['col']}: {cell_text['text']}")
            logger.info("[E-31] ===== セルテキスト終了 =====")

            ocr_result = {
                'success': True,
                'cell_texts': cell_texts,
                'ocr_engine': 'VISION',
                'route': 'E31_CELL_OCR',
                'cells_processed': len(cell_texts)
            }

            # ★チェーン: 次のステージ（E-32）を呼び出す
            if self.next_stage and struct_result:
                logger.info("[E-31] → 次のステージ（E-32）を呼び出します")
                return self.next_stage.merge(struct_result, ocr_result)

            return ocr_result

        except Exception as e:
            logger.error(f"[E-31] セル OCR 失敗 → 空テキストで続行: {e}", exc_info=True)
            fallback = self._fallback_result(cells)

            # ★チェーン: エラー時もE-32を呼ぶ
            if self.next_stage and struct_result:
                logger.info("[E-31] → エラー後もE-32を呼び出します")
                return self.next_stage.merge(struct_result, fallback)

            return fallback

    def _fallback_result(
        self,
        cells: List[Dict[str, Any]],
        struct_result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """失敗時 / ライブラリ未インストール時：全セルを空テキストで返す"""
        cell_texts = [
            {'row': c.get('row', 0), 'col': c.get('col', 0), 'text': '', 'confidence': 0.0}
            for c in cells
        ]
        ocr_result = {
            'success': True,
            'cell_texts': cell_texts,
            'ocr_engine': 'NONE',
            'route': 'E31_CELL_OCR',
            'cells_processed': len(cell_texts)
        }

        # ★チェーン: フォールバック時もE-32を呼ぶ
        if self.next_stage and struct_result:
            logger.info("[E-31] → フォールバック後もE-32を呼び出します")
            return self.next_stage.merge(struct_result, ocr_result)

        return ocr_result

    def _empty_result(self, struct_result: Dict[str, Any] = None) -> Dict[str, Any]:
        ocr_result = {
            'success': True,
            'cell_texts': [],
            'ocr_engine': 'NONE',
            'route': 'E31_CELL_OCR',
            'cells_processed': 0
        }

        # ★チェーン: 空結果でもE-32を呼ぶ
        if self.next_stage and struct_result:
            logger.info("[E-31] → 空結果でもE-32を呼び出します")
            return self.next_stage.merge(struct_result, ocr_result)

        return ocr_result
