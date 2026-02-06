"""
E-6: Vision OCR

Google Cloud Vision API (DOCUMENT_TEXT_DETECTION) を使用して
画像からテキストとbboxを抽出する。

出力: vision_tokens = [{text, bbox}, ...]
"""

from pathlib import Path
from typing import Dict, Any, List
from loguru import logger

from ..vision_api_extractor import VisionAPIExtractor


class E6VisionOCR:
    """E-6: Vision API OCR"""

    def __init__(self):
        self.extractor = VisionAPIExtractor()

    def extract(
        self,
        image_path: Path,
        image_width: int,
        image_height: int
    ) -> Dict[str, Any]:
        """
        Vision API で画像からテキストを抽出

        Args:
            image_path: 画像ファイルパス
            image_width: 画像幅
            image_height: 画像高さ

        Returns:
            {
                'vision_tokens': [{text, bbox}, ...],
                'full_text': str,
                'stats': {...}
            }
        """
        logger.info(f"[E-6] Vision OCR 開始: {image_path.name}")

        try:
            result = self.extractor.extract_with_document_detection(
                image_path, image_width, image_height
            )

            tokens = result.get('tokens', [])
            full_text = result.get('full_text', '')
            stats = result.get('stats', {})

            # 【全文字ログ出力】
            logger.info(f"[E-6] ===== 生成物ログ開始 =====")
            logger.info(f"[E-6] tokens数: {len(tokens)}")
            logger.info(f"[E-6] full_text長: {len(full_text)}文字")
            for i, token in enumerate(tokens):
                text = token.get('text', '')
                bbox = token.get('bbox', [])
                logger.info(f"[E-6]   [{i+1}] bbox={bbox}, text='{text}'")
            logger.info(f"[E-6] ===== 生成物ログ終了 =====")

            return {
                'success': True,
                'vision_tokens': tokens,
                'full_text': full_text,
                'stats': stats,
                'page_size': result.get('page_size', {'w': image_width, 'h': image_height})
            }

        except Exception as e:
            logger.error(f"[E-6] Vision OCR エラー: {e}")
            return {
                'success': False,
                'vision_tokens': [],
                'full_text': '',
                'error': str(e)
            }
