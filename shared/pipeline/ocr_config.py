"""
OCRエンジン設定とキャッシング

PaddleOCRの設定、バージョン検出、結果キャッシング機能を提供
"""
import hashlib
import json
import pickle
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
import time


class OCRConfig:
    """OCRエンジンの設定管理"""

    # 信頼度閾値
    CONFIDENCE_THRESHOLD_LOW = 0.5      # これ以下は無視
    CONFIDENCE_THRESHOLD_MID = 0.7      # これ以下は再処理
    CONFIDENCE_THRESHOLD_HIGH = 0.85    # これ以上は高品質

    # 並列処理設定
    ENABLE_PARALLEL = True               # 並列処理を有効化
    MAX_WORKERS = 4                      # 最大ワーカー数

    # キャッシング設定
    ENABLE_CACHE = True                  # 結果キャッシングを有効化
    CACHE_DIR = Path("cache/ocr_results")  # キャッシュディレクトリ
    CACHE_TTL = 86400                    # キャッシュ有効期限（秒）

    # OCRエンジン優先順位
    OCR_ENGINE_PRIORITY = [
        'paddleocr',  # 1. PaddleOCR（日本語に強い）
        'surya',      # 2. Surya（レイアウト解析）
        'gemini'      # 3. Gemini Vision（フォールバック）
    ]

    # 画像前処理設定
    ENABLE_PREPROCESSING = True          # 画像前処理を有効化
    PREPROCESSING_QUALITY_THRESHOLD = 0.7  # この品質以下で前処理適用

    # Geminiフォールバック設定
    ENABLE_GEMINI_FALLBACK = True        # 低信頼度時Geminiフォールバック
    GEMINI_FALLBACK_THRESHOLD = 0.6      # この信頼度以下でフォールバック


class OCRResultCache:
    """OCR結果のキャッシング"""

    def __init__(self, cache_dir: Path = OCRConfig.CACHE_DIR, ttl: int = OCRConfig.CACHE_TTL):
        """
        Args:
            cache_dir: キャッシュディレクトリ
            ttl: キャッシュ有効期限（秒）
        """
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _get_cache_key(self, image_data: bytes, config: Dict[str, Any]) -> str:
        """
        画像とconfigからキャッシュキーを生成

        Args:
            image_data: 画像のバイトデータ
            config: OCR設定

        Returns:
            SHA256ハッシュキー
        """
        config_str = json.dumps(config, sort_keys=True)
        combined = image_data + config_str.encode('utf-8')
        return hashlib.sha256(combined).hexdigest()

    def get(self, image_data: bytes, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        キャッシュから結果を取得

        Args:
            image_data: 画像のバイトデータ
            config: OCR設定

        Returns:
            キャッシュされた結果、または None
        """
        if not OCRConfig.ENABLE_CACHE:
            return None

        cache_key = self._get_cache_key(image_data, config)
        cache_file = self.cache_dir / f"{cache_key}.pkl"

        if not cache_file.exists():
            self.misses += 1
            return None

        try:
            # キャッシュファイルの有効期限確認
            file_age = time.time() - cache_file.stat().st_mtime
            if file_age > self.ttl:
                cache_file.unlink()  # 期限切れ削除
                self.misses += 1
                return None

            # キャッシュ読み込み
            with open(cache_file, 'rb') as f:
                result = pickle.load(f)

            self.hits += 1
            logger.debug(f"[Cache] HIT: {cache_key[:8]}... (age: {file_age:.0f}s)")
            return result

        except Exception as e:
            logger.warning(f"[Cache] 読み込み失敗: {e}")
            self.misses += 1
            return None

    def set(self, image_data: bytes, config: Dict[str, Any], result: Dict[str, Any]):
        """
        結果をキャッシュに保存

        Args:
            image_data: 画像のバイトデータ
            config: OCR設定
            result: OCR結果
        """
        if not OCRConfig.ENABLE_CACHE:
            return

        cache_key = self._get_cache_key(image_data, config)
        cache_file = self.cache_dir / f"{cache_key}.pkl"

        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(result, f)
            logger.debug(f"[Cache] SAVE: {cache_key[:8]}...")
        except Exception as e:
            logger.warning(f"[Cache] 保存失敗: {e}")

    def clear(self):
        """キャッシュをクリア"""
        try:
            for cache_file in self.cache_dir.glob("*.pkl"):
                cache_file.unlink()
            logger.info(f"[Cache] クリア完了: {self.cache_dir}")
        except Exception as e:
            logger.warning(f"[Cache] クリア失敗: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """キャッシュ統計を取得"""
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0

        cache_files = list(self.cache_dir.glob("*.pkl"))
        total_size = sum(f.stat().st_size for f in cache_files)

        return {
            'hits': self.hits,
            'misses': self.misses,
            'total_requests': total,
            'hit_rate': hit_rate,
            'cache_files': len(cache_files),
            'total_size_mb': total_size / (1024 * 1024)
        }


class PaddleOCRVersionAdapter:
    """PaddleOCRバージョン互換性アダプター"""

    @staticmethod
    def detect_version() -> str:
        """PaddleOCRのバージョンを検出"""
        try:
            import paddleocr
            version = getattr(paddleocr, '__version__', 'unknown')
            logger.info(f"[PaddleOCR] Version detected: {version}")
            return version
        except Exception as e:
            logger.warning(f"[PaddleOCR] Version detection failed: {e}")
            return 'unknown'

    @staticmethod
    def extract_result(ocr_result: Any) -> tuple:
        """
        PaddleOCRの結果から統一的にテキストと信頼度を抽出

        Args:
            ocr_result: PaddleOCRの結果オブジェクト

        Returns:
            (texts: List[str], confidences: List[float])
        """
        texts = []
        confidences = []

        try:
            # PaddleOCR 3.x: 辞書ライクオブジェクト
            if isinstance(ocr_result, dict) or hasattr(ocr_result, '__getitem__'):
                rec_texts = ocr_result.get('rec_texts', []) if hasattr(ocr_result, 'get') else ocr_result.get('rec_texts', [])
                rec_scores = ocr_result.get('rec_scores', []) if hasattr(ocr_result, 'get') else ocr_result.get('rec_scores', [])

                if rec_texts:
                    texts = list(rec_texts)
                    confidences = list(rec_scores) if rec_scores else []

            # PaddleOCR 2.x: リスト形式
            elif isinstance(ocr_result, list):
                for line in ocr_result:
                    if line and len(line) >= 2 and line[1]:
                        texts.append(line[1][0])
                        confidences.append(line[1][1])

        except Exception as e:
            logger.warning(f"[PaddleOCR] Result extraction failed: {e}")

        return texts, confidences
