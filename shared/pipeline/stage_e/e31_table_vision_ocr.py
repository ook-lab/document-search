"""
E-31: Table Cell OCR（チェーン中継）

チェーン:
  E-30（Gemini image OCR: struct_result）
    → E-31（d10_table を付与して E-32 へ転送）
      → E-32（D10 cell_map に割当）
        → E-40（image SSOT 正本化）

E-31 の現役割:
  - d10_table（D10の cell_map 情報）を受け取り、チェーンに流す
  - 将来 Vision API word-level OCR を追加する場合もここに実装する
  - 現状は E-30（Gemini）がテキストを持つため、OCR 処理はスキップ
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
        struct_result: Dict[str, Any] = None,
        d10_table: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        E-30 のテキスト取得済みのため OCR スキップ。d10_table を付与して E-32 へ転送。

        Args:
            image_path: 表画像パス（将来の Vision API 用に保持）
            cells: E-30 の cells リスト（既にtextを含む）
            struct_result: E-30 の構造抽出結果（チェーン用）
            d10_table: D10 tables[] の1要素（cell_map を E-32 に渡すために必須）
                {origin_uid, canonical_id, bbox, cell_map: [{row, col, bbox}]}

        Returns:
            E-32 → E-40 チェーンの最終結果（image SSOT）
        """
        logger.info("=" * 80)
        logger.info("[E-31] OCRスキップ（E-30取得済み）→ E-32 へ d10_table 付きで転送")
        logger.info("=" * 80)

        # ★ガード: d10_table が None なら致命エラー
        if d10_table is None:
            logger.error("[E-31] ★致命エラー: d10_table が渡されていません。E-30 の extract_structure() に d10_table= を指定してください。")
            return {
                'success': False,
                'route': 'E31_MISSING_D10_TABLE',
                'error': 'd10_table が E31 に渡されていません。E30 の呼び出し元を確認してください。',
            }

        logger.info(f"[E-31] d10_table: {d10_table.get('origin_uid')} cells={len(d10_table.get('cell_map', []))}")

        # E-30 Gemini が取得したテキストを cell_texts 形式に変換
        struct_cells = (struct_result or {}).get('cells', []) if struct_result else []
        cell_texts = []
        for c in struct_cells:
            txt = (c.get('text') or '').strip()
            if txt:
                cell_texts.append({
                    'row': c.get('row'),
                    'col': c.get('col'),
                    'text': txt,
                    'confidence': 1.0,  # Gemini 由来
                })
        logger.info(f"[E-31] E30テキストを cell_texts に変換: {len(cell_texts)}セル")
        for row_idx, ct in enumerate(cell_texts):
            logger.info(f"[E-31]   行{row_idx}: {ct}")

        ocr_result = {
            'cell_texts': cell_texts,
            'ocr_engine': 'GEMINI_E30',
            'route': 'E31_PASS_THROUGH',
        }

        # チェーン: E-32.merge() を呼び出す
        if self.next_stage and struct_result:
            logger.info("[E-31] → 次のステージ（E-32.merge()）を呼び出します")
            return self.next_stage.merge(struct_result, ocr_result)

        # next_stage なし（単体テスト等）
        return {
            'success': True,
            'cell_texts': cell_texts,
            'ocr_engine': 'GEMINI_E30',
            'route': 'E31_PASS_THROUGH',
            'cells_processed': len(cell_texts),
        }

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
                    logger.info(f"[E-31] R{row}C{col}: bbox 不正({x0},{y0},{x1},{y1}) → 空")
                    cell_texts.append({'row': row, 'col': col, 'text': '', 'confidence': 0.0})
                    continue

                # セルを切り出し
                crop = img.crop((x0, y0, x1, y1))

                # PNG バイトに変換
                buf = io.BytesIO()
                crop.save(buf, format='PNG')
                content = buf.getvalue()

                # Vision API OCR
                logger.info(f"[E-31] R{row}C{col}: Vision API 呼び出し中... (bbox={x0},{y0},{x1},{y1})")
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
                logger.info(f"[E-31] R{row}C{col}: '{text}' (confidence={confidence})")

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
