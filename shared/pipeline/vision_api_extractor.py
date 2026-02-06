"""
F7: Vision API OCR Extractor
【Ver 7.0】検出OCR版 - LLMによるOCRを完全排除

設計原則:
- OCRは「検出器」で行う（Vision API）
- LLMは使わない（推測・生成を構造的に封じる）
- 座標はプログラムで検証して落とす
"""
import io
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger

try:
    from google.cloud import vision
    VISION_API_AVAILABLE = True
except ImportError:
    VISION_API_AVAILABLE = False
    logger.warning("[F7] google-cloud-vision not installed")

from PIL import Image


class VisionAPIExtractor:
    """
    F7: Vision API による検出OCR

    出力契約:
    {
        "ocr_provider": "vision_api",
        "page_size": {"w": int, "h": int},
        "tokens": [{"text": str, "bbox": [x0,y0,x1,y1], "conf": float}],
        "tokens_low_conf": [...],
        "stats": {...}
    }
    """

    # 閾値設定
    CONF_THRESHOLD = 0.5          # これ未満は low_conf へ
    MIN_BBOX_SIZE = 5             # 幅/高さがこれ未満は除外

    def __init__(self):
        """Vision API クライアントを初期化"""
        if not VISION_API_AVAILABLE:
            raise ImportError("google-cloud-vision is not installed. Run: pip install google-cloud-vision")

        self.client = vision.ImageAnnotatorClient()
        logger.info("[F7] Vision API Extractor initialized")

    def extract(
        self,
        image_path: Path,
        image_width: Optional[int] = None,
        image_height: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        F7: 画像からテキスト + bbox を検出

        Args:
            image_path: 画像ファイルパス
            image_width: 画像幅（省略時は自動取得）
            image_height: 画像高さ（省略時は自動取得）

        Returns:
            出力契約に従ったdict
        """
        f7_start = time.time()
        logger.info(f"[F7] Vision API OCR開始: {image_path.name}")

        # 画像サイズ取得
        if image_width is None or image_height is None:
            with Image.open(image_path) as img:
                image_width, image_height = img.size

        logger.info(f"[F7] 画像サイズ: {image_width}x{image_height}")

        # Vision API 呼び出し
        with open(image_path, 'rb') as f:
            content = f.read()

        image = vision.Image(content=content)

        # TEXT_DETECTION（word単位）を使用
        # DOCUMENT_TEXT_DETECTION だと paragraph/block 単位になる
        response = self.client.text_detection(image=image)

        if response.error.message:
            raise Exception(f"Vision API error: {response.error.message}")

        # word階層だけ採用してトークン化
        tokens, tokens_low_conf, stats = self._parse_response(
            response,
            image_width,
            image_height
        )

        elapsed = time.time() - f7_start
        logger.info(f"[F7完了] {len(tokens)}tokens, {len(tokens_low_conf)}low_conf, {elapsed:.2f}秒")

        return {
            "ocr_provider": "vision_api",
            "page_size": {"w": image_width, "h": image_height},
            "tokens": tokens,
            "tokens_low_conf": tokens_low_conf,
            "stats": stats
        }

    def extract_from_bytes(
        self,
        image_bytes: bytes,
        image_width: int,
        image_height: int
    ) -> Dict[str, Any]:
        """
        F7: バイトデータからテキスト + bbox を検出

        Args:
            image_bytes: 画像バイトデータ
            image_width: 画像幅
            image_height: 画像高さ

        Returns:
            出力契約に従ったdict
        """
        f7_start = time.time()
        logger.info(f"[F7] Vision API OCR開始 (bytes): {len(image_bytes)}bytes")

        image = vision.Image(content=image_bytes)
        response = self.client.text_detection(image=image)

        if response.error.message:
            raise Exception(f"Vision API error: {response.error.message}")

        tokens, tokens_low_conf, stats = self._parse_response(
            response,
            image_width,
            image_height
        )

        elapsed = time.time() - f7_start
        logger.info(f"[F7完了] {len(tokens)}tokens, {len(tokens_low_conf)}low_conf, {elapsed:.2f}秒")

        return {
            "ocr_provider": "vision_api",
            "page_size": {"w": image_width, "h": image_height},
            "tokens": tokens,
            "tokens_low_conf": tokens_low_conf,
            "stats": stats
        }

    def _parse_response(
        self,
        response,
        image_width: int,
        image_height: int
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        """
        Vision API レスポンスをパースしてトークン化

        - word階層のみ採用（symbol/1文字は不採用）
        - プログラムで異常を除外
        """
        tokens = []
        tokens_low_conf = []

        # 統計用カウンタ
        stats = {
            "token_count": 0,
            "low_conf_count": 0,
            "dropped": {
                "out_of_bounds": 0,
                "too_small": 0,
                "duplicate_bbox": 0,
                "empty_text": 0
            }
        }

        seen_bboxes = set()  # 重複bbox検出用

        # text_annotations[0] は全文テキスト、[1:]が個別word
        annotations = response.text_annotations
        if not annotations:
            logger.warning("[F7] No text detected")
            return tokens, tokens_low_conf, stats

        # [1:] から word を取得
        for annotation in annotations[1:]:
            text = annotation.description.strip()

            # 空テキストは除外
            if not text:
                stats["dropped"]["empty_text"] += 1
                continue

            # bounding poly から bbox を計算
            vertices = annotation.bounding_poly.vertices
            if len(vertices) < 4:
                continue

            x0 = min(v.x for v in vertices)
            y0 = min(v.y for v in vertices)
            x1 = max(v.x for v in vertices)
            y1 = max(v.y for v in vertices)

            # 範囲外チェック
            if x0 < 0 or y0 < 0 or x1 > image_width or y1 > image_height:
                stats["dropped"]["out_of_bounds"] += 1
                continue

            # 極小bboxチェック
            width = x1 - x0
            height = y1 - y0
            if width < self.MIN_BBOX_SIZE or height < self.MIN_BBOX_SIZE:
                stats["dropped"]["too_small"] += 1
                continue

            # 重複bboxチェック
            bbox_key = (x0, y0, x1, y1)
            if bbox_key in seen_bboxes:
                stats["dropped"]["duplicate_bbox"] += 1
                continue
            seen_bboxes.add(bbox_key)

            # confidence は text_annotations では取得できない
            # document_text_detection を使う場合は取得可能
            # ここでは仮に 1.0 とする（Vision API の word は高信頼）
            conf = 1.0

            token = {
                "text": text,
                "bbox": [x0, y0, x1, y1],
                "conf": conf
            }

            if conf < self.CONF_THRESHOLD:
                tokens_low_conf.append(token)
                stats["low_conf_count"] += 1
            else:
                tokens.append(token)
                stats["token_count"] += 1

        return tokens, tokens_low_conf, stats

    def extract_with_document_detection(
        self,
        image_path: Path,
        image_width: Optional[int] = None,
        image_height: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        F7: DOCUMENT_TEXT_DETECTION を使用（より詳細な構造情報）

        こちらは confidence が取得可能
        """
        f7_start = time.time()
        logger.info(f"[F7] Vision API Document Detection開始: {image_path.name}")

        # 画像サイズ取得
        if image_width is None or image_height is None:
            with Image.open(image_path) as img:
                image_width, image_height = img.size

        with open(image_path, 'rb') as f:
            content = f.read()

        image = vision.Image(content=content)
        response = self.client.document_text_detection(image=image)

        if response.error.message:
            raise Exception(f"Vision API error: {response.error.message}")

        tokens, tokens_low_conf, stats = self._parse_document_response(
            response,
            image_width,
            image_height
        )

        elapsed = time.time() - f7_start
        logger.info(f"[F7完了] {len(tokens)}tokens, {len(tokens_low_conf)}low_conf, {elapsed:.2f}秒")

        return {
            "ocr_provider": "vision_api_document",
            "page_size": {"w": image_width, "h": image_height},
            "tokens": tokens,
            "tokens_low_conf": tokens_low_conf,
            "stats": stats
        }

    def _parse_document_response(
        self,
        response,
        image_width: int,
        image_height: int
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        """
        DOCUMENT_TEXT_DETECTION レスポンスをパース

        word階層でconfidence取得可能
        """
        tokens = []
        tokens_low_conf = []

        stats = {
            "token_count": 0,
            "low_conf_count": 0,
            "dropped": {
                "out_of_bounds": 0,
                "too_small": 0,
                "duplicate_bbox": 0,
                "empty_text": 0
            }
        }

        seen_bboxes = set()

        document = response.full_text_annotation
        if not document:
            logger.warning("[F7] No document detected")
            return tokens, tokens_low_conf, stats

        # page -> block -> paragraph -> word 階層をたどる
        for page in document.pages:
            for block in page.blocks:
                for paragraph in block.paragraphs:
                    for word in paragraph.words:
                        # word.symbols を結合してテキスト取得
                        text = ''.join([
                            symbol.text for symbol in word.symbols
                        ]).strip()

                        if not text:
                            stats["dropped"]["empty_text"] += 1
                            continue

                        # bounding box
                        vertices = word.bounding_box.vertices
                        if len(vertices) < 4:
                            continue

                        x0 = min(v.x for v in vertices)
                        y0 = min(v.y for v in vertices)
                        x1 = max(v.x for v in vertices)
                        y1 = max(v.y for v in vertices)

                        # 範囲外チェック
                        if x0 < 0 or y0 < 0 or x1 > image_width or y1 > image_height:
                            stats["dropped"]["out_of_bounds"] += 1
                            continue

                        # 極小bboxチェック
                        width = x1 - x0
                        height = y1 - y0
                        if width < self.MIN_BBOX_SIZE or height < self.MIN_BBOX_SIZE:
                            stats["dropped"]["too_small"] += 1
                            continue

                        # 重複bboxチェック
                        bbox_key = (x0, y0, x1, y1)
                        if bbox_key in seen_bboxes:
                            stats["dropped"]["duplicate_bbox"] += 1
                            continue
                        seen_bboxes.add(bbox_key)

                        # confidence（word単位）
                        conf = word.confidence if hasattr(word, 'confidence') else 1.0

                        token = {
                            "text": text,
                            "bbox": [x0, y0, x1, y1],
                            "conf": round(conf, 3)
                        }

                        if conf < self.CONF_THRESHOLD:
                            tokens_low_conf.append(token)
                            stats["low_conf_count"] += 1
                        else:
                            tokens.append(token)
                            stats["token_count"] += 1

        return tokens, tokens_low_conf, stats
