"""
E-20: Non-Table Vision OCR（非表領域 Vision API OCR）

非表領域画像から Google Cloud Vision API でテキストを抽出する。
文字が多い場合のみ実行（条件付き）。
抽出テキストは E-21 のプロンプトに注入して精度補助として使う。

責務:
- 非表専用（表は E-31 が担当）
- 補助情報の提供（正本は画像、OCRは参考）
- 失敗しても E-21 の実行を止めない
- 座標付きブロック抽出（E27で位置順マージに使用）
"""

from pathlib import Path
from typing import Dict, Any, List
from loguru import logger


try:
    from google.cloud import vision as gcloud_vision
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    logger.warning("[E-20] google-cloud-vision がインストールされていません（E-21 は画像のみで動作）")


class E20NonTableVisionOcr:
    """E-20: 非表領域 Vision OCR（条件付き実行）"""

    def extract_text(self, image_path: Path) -> str:
        """
        非表領域画像から OCR テキストを抽出

        Args:
            image_path: 非表領域の画像パス

        Returns:
            抽出テキスト。失敗時・未インストール時は "" を返す（例外を外に出さない）
        """
        if not VISION_AVAILABLE:
            logger.warning("[E-20] google-cloud-vision 未インストール → 空文字で続行")
            return ""

        logger.info("=" * 80)
        logger.info(f"[E-20] 非表 Vision OCR 開始: {image_path.name}")
        logger.info("=" * 80)

        try:
            client = gcloud_vision.ImageAnnotatorClient()

            with open(image_path, "rb") as f:
                content = f.read()

            logger.info(f"[E-20] 画像サイズ: {len(content)} bytes")

            image = gcloud_vision.Image(content=content)

            logger.info("[E-20] Vision API 呼び出し開始...")
            response = client.document_text_detection(image=image)
            logger.info("[E-20] Vision API 呼び出し完了")

            if response.error.message:
                logger.warning(f"[E-20] Vision API エラー: {response.error.message} → 空文字で続行")
                return ""

            text = response.full_text_annotation.text if response.full_text_annotation else ""

            logger.info(f"[E-20] OCR 完了: {len(text)}文字")
            logger.info("[E-20] ===== Vision OCR 抽出テキスト全文 =====")
            logger.info(text)
            logger.info("[E-20] ===== テキスト終了 =====")

            # 統計情報
            lines = text.split('\n') if text else []
            logger.info(f"[E-20] 統計:")
            logger.info(f"  ├─ 文字数: {len(text)}")
            logger.info(f"  ├─ 行数: {len(lines)}")
            logger.info(f"  └─ 平均行長: {len(text) / max(len(lines), 1):.1f}文字")

            logger.info("=" * 80)

            return text

        except Exception as e:
            logger.warning(f"[E-20] 非表 Vision OCR 失敗（E-21 は画像のみで続行）: {e}", exc_info=True)
            return ""

    def extract_with_coordinates(
        self,
        image_path: Path,
        page: int = 0
    ) -> Dict[str, Any]:
        """
        非表領域画像から座標付きブロックを抽出

        Args:
            image_path: 非表領域の画像パス
            page: ページ番号

        Returns:
            {
                'success': bool,
                'blocks': [
                    {
                        'page': int,
                        'type': 'paragraph',
                        'text': str,
                        'bbox': [x0, y0, x1, y1]
                    }
                ],
                'text': str  # 全テキスト（後方互換性）
            }
        """
        if not VISION_AVAILABLE:
            logger.warning("[E-20] google-cloud-vision 未インストール → 空結果で続行")
            return {
                'success': False,
                'blocks': [],
                'text': ''
            }

        logger.info("=" * 80)
        logger.info(f"[E-20] 座標付きVision OCR 開始: {image_path.name}")
        logger.info("=" * 80)

        try:
            client = gcloud_vision.ImageAnnotatorClient()

            with open(image_path, "rb") as f:
                content = f.read()

            logger.info(f"[E-20] 画像サイズ: {len(content)} bytes")

            image = gcloud_vision.Image(content=content)

            logger.info("[E-20] Vision API 呼び出し開始...")
            response = client.document_text_detection(image=image)
            logger.info("[E-20] Vision API 呼び出し完了")

            if response.error.message:
                logger.warning(f"[E-20] Vision API エラー: {response.error.message}")
                return {
                    'success': False,
                    'blocks': [],
                    'text': ''
                }

            if not response.full_text_annotation:
                logger.warning("[E-20] full_text_annotation が空です")
                return {
                    'success': True,
                    'blocks': [],
                    'text': ''
                }

            # 全テキスト
            full_text = response.full_text_annotation.text

            # 段落レベルでブロックを抽出
            blocks = []
            for page_obj in response.full_text_annotation.pages:
                for block in page_obj.blocks:
                    for paragraph in block.paragraphs:
                        # 段落のテキストを構築
                        para_text = ""
                        for word in paragraph.words:
                            word_text = "".join([symbol.text for symbol in word.symbols])
                            para_text += word_text + " "
                        para_text = para_text.strip()

                        if not para_text:
                            continue

                        # bboxを計算（正規化座標 → ピクセル座標）
                        vertices = paragraph.bounding_box.vertices
                        x_coords = [v.x for v in vertices]
                        y_coords = [v.y for v in vertices]

                        x0 = min(x_coords)
                        y0 = min(y_coords)
                        x1 = max(x_coords)
                        y1 = max(y_coords)

                        blocks.append({
                            'page': page,
                            'type': 'paragraph',
                            'text': para_text,
                            'bbox': [x0, y0, x1, y1]
                        })

            logger.info(f"[E-20] 抽出完了: {len(blocks)}段落, {len(full_text)}文字")
            logger.info(f"[E-20] ===== 抽出された段落 =====")
            for idx, block in enumerate(blocks[:10], 1):  # 最初の10段落のみ表示
                logger.info(f"  段落{idx}: {block['text'][:50]}...")
            if len(blocks) > 10:
                logger.info(f"  ... ({len(blocks) - 10}段落省略)")
            logger.info("=" * 80)

            return {
                'success': True,
                'blocks': blocks,
                'text': full_text
            }

        except Exception as e:
            logger.error(f"[E-20] 座標付きVision OCR 失敗: {e}", exc_info=True)
            return {
                'success': False,
                'blocks': [],
                'text': ''
            }
