"""
E-21: Non-Table Vision OCR（非表領域 Vision API OCR）

非表領域画像から Google Cloud Vision API でテキストを抽出する。
文字が多い場合のみ実行（条件付き）。
抽出テキストは E-22 のプロンプトに注入して精度補助として使う。

責務:
- 非表専用（表は E-31 が担当）
- 補助情報の提供（正本は画像、OCRは参考）
- 失敗しても E-22 の実行を止めない
"""

from pathlib import Path
from loguru import logger


try:
    from google.cloud import vision as gcloud_vision
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    logger.warning("[E-21] google-cloud-vision がインストールされていません（E-22 は画像のみで動作）")


class E21NonTableVisionOcr:
    """E-21: 非表領域 Vision OCR（条件付き実行）"""

    def extract_text(self, image_path: Path) -> str:
        """
        非表領域画像から OCR テキストを抽出

        Args:
            image_path: 非表領域の画像パス

        Returns:
            抽出テキスト。失敗時・未インストール時は "" を返す（例外を外に出さない）
        """
        if not VISION_AVAILABLE:
            logger.warning("[E-21] google-cloud-vision 未インストール → 空文字で続行")
            return ""

        logger.info(f"[E-21] 非表 Vision OCR 開始: {image_path.name}")

        try:
            client = gcloud_vision.ImageAnnotatorClient()

            with open(image_path, "rb") as f:
                content = f.read()

            image = gcloud_vision.Image(content=content)
            response = client.document_text_detection(image=image)

            if response.error.message:
                logger.warning(f"[E-21] Vision API エラー: {response.error.message} → 空文字で続行")
                return ""

            text = response.full_text_annotation.text if response.full_text_annotation else ""
            logger.info(f"[E-21] OCR 完了: {len(text)}文字")
            return text

        except Exception as e:
            logger.warning(f"[E-21] 非表 Vision OCR 失敗（E-22 は画像のみで続行）: {e}")
            return ""
