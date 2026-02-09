"""
E-1: OCR Scouter（文字数測定）

軽量OCRエンジンを使用して画像内の文字数を測定し、
後続処理のルーティング判定に使用する。

目的:
1. 文字密度を測定（高密度 vs 低密度）
2. 処理スキップ判定（ノイズのみの画像を除外）
3. APIコスト最適化のための事前スカウティング
"""

from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
import numpy as np

try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("[E-1] pytesseract/PIL がインストールされていません")


class E1OcrScouter:
    """E-1: OCR Scouter（文字数測定）"""

    def __init__(
        self,
        low_density_threshold: int = 100,   # 低密度判定の閾値（文字数）
        high_density_threshold: int = 500,  # 高密度判定の閾値（文字数）
        min_char_threshold: int = 10        # 最小文字数（これ以下は処理スキップ）
    ):
        """
        OCR Scouter 初期化

        Args:
            low_density_threshold: 低密度判定の閾値
            high_density_threshold: 高密度判定の閾値
            min_char_threshold: 最小文字数（これ以下は処理スキップ）
        """
        self.low_density_threshold = low_density_threshold
        self.high_density_threshold = high_density_threshold
        self.min_char_threshold = min_char_threshold

        if not TESSERACT_AVAILABLE:
            logger.error("[E-1] pytesseract/PIL が必要です")

    def scout(
        self,
        image_path: Path,
        lang: str = 'jpn+eng'
    ) -> Dict[str, Any]:
        """
        画像内の文字数を測定

        Args:
            image_path: 画像ファイルパス
            lang: OCR言語設定

        Returns:
            {
                'char_count': int,           # 推定文字数
                'density_level': str,        # 'none', 'low', 'medium', 'high'
                'should_skip': bool,         # 処理スキップすべきか
                'extracted_text': str,       # 抽出されたテキスト
                'confidence': float          # OCR信頼度（0.0-1.0）
            }
        """
        if not TESSERACT_AVAILABLE:
            logger.error("[E-1] pytesseract/PIL が利用できません")
            return self._empty_result()

        logger.info(f"[E-1] 文字数測定開始: {image_path.name}")

        try:
            # 画像を読み込み
            image = Image.open(str(image_path))

            # Tesseract で OCR
            text = pytesseract.image_to_string(image, lang=lang)

            # 文字数をカウント（空白・改行を除く）
            char_count = len(text.replace(' ', '').replace('\n', '').replace('\t', ''))

            # 密度レベルを判定
            density_level = self._classify_density(char_count)

            # スキップ判定
            should_skip = char_count < self.min_char_threshold

            # 信頼度を簡易的に計算（文字数ベース）
            confidence = min(1.0, char_count / self.high_density_threshold)

            logger.info(f"[E-1] 測定完了:")
            logger.info(f"  ├─ 文字数: {char_count}")
            logger.info(f"  ├─ 密度: {density_level}")
            logger.info(f"  └─ スキップ: {should_skip}")

            return {
                'char_count': char_count,
                'density_level': density_level,
                'should_skip': should_skip,
                'extracted_text': text,
                'confidence': confidence
            }

        except Exception as e:
            logger.error(f"[E-1] 測定エラー: {e}", exc_info=True)
            return self._empty_result()

    def _classify_density(self, char_count: int) -> str:
        """
        文字数から密度レベルを分類

        Args:
            char_count: 文字数

        Returns:
            'none', 'low', 'medium', 'high'
        """
        if char_count < self.min_char_threshold:
            return 'none'
        elif char_count < self.low_density_threshold:
            return 'low'
        elif char_count < self.high_density_threshold:
            return 'medium'
        else:
            return 'high'

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'char_count': 0,
            'density_level': 'none',
            'should_skip': True,
            'extracted_text': '',
            'confidence': 0.0
        }
