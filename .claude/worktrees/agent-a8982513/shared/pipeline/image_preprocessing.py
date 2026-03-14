"""
画像前処理ユーティリティ

PaddleOCRの認識精度向上のための画像前処理機能を提供
"""
import cv2
import numpy as np
from typing import Tuple
from loguru import logger


def preprocess_image_for_ocr(
    image: np.ndarray,
    apply_clahe: bool = True,
    apply_denoise: bool = True,
    apply_sharpen: bool = True,
    apply_binarize: bool = False
) -> Tuple[np.ndarray, dict]:
    """
    OCR認識精度向上のための画像前処理

    Args:
        image: 入力画像（numpy array、RGB or Grayscale）
        apply_clahe: CLAHEによるコントラスト調整を適用
        apply_denoise: ノイズ除去を適用
        apply_sharpen: シャープ化を適用
        apply_binarize: 二値化を適用（低品質画像向け）

    Returns:
        (processed_image, stats): 前処理済み画像と統計情報
    """
    stats = {
        'original_shape': image.shape,
        'applied_operations': []
    }

    # RGB → Grayscale 変換
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    else:
        gray = image.copy()

    processed = gray.copy()

    # 1. CLAHE（コントラスト制限適応ヒストグラム均等化）
    if apply_clahe:
        try:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            processed = clahe.apply(processed)
            stats['applied_operations'].append('CLAHE')
        except Exception as e:
            logger.warning(f"CLAHE処理失敗: {e}")

    # 2. ノイズ除去（Non-local Means Denoising）
    if apply_denoise:
        try:
            processed = cv2.fastNlMeansDenoising(processed, None, h=10, templateWindowSize=7, searchWindowSize=21)
            stats['applied_operations'].append('Denoise')
        except Exception as e:
            logger.warning(f"ノイズ除去処理失敗: {e}")

    # 3. シャープ化（Unsharp Masking）
    if apply_sharpen:
        try:
            # ガウシアンブラーでぼかし画像を作成
            blurred = cv2.GaussianBlur(processed, (0, 0), 3)
            # オリジナル - ぼかし = シャープマスク
            processed = cv2.addWeighted(processed, 1.5, blurred, -0.5, 0)
            stats['applied_operations'].append('Sharpen')
        except Exception as e:
            logger.warning(f"シャープ化処理失敗: {e}")

    # 4. 二値化（Otsuの閾値処理） - オプション
    if apply_binarize:
        try:
            _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            stats['applied_operations'].append('Binarize')
        except Exception as e:
            logger.warning(f"二値化処理失敗: {e}")

    # 5. PaddleOCR用にRGB形式に変換
    # PaddleOCRはRGB/カラー画像を期待するため、グレースケールから戻す
    if len(processed.shape) == 2:
        processed = cv2.cvtColor(processed, cv2.COLOR_GRAY2RGB)
        stats['applied_operations'].append('GrayToRGB')

    stats['final_shape'] = processed.shape
    stats['operations_count'] = len(stats['applied_operations'])

    return processed, stats


def adaptive_preprocess(
    image: np.ndarray,
    confidence_threshold: float = 0.7
) -> np.ndarray:
    """
    低信頼度領域に対する適応的前処理

    通常の前処理で認識精度が低い場合に、より強力な前処理を適用

    Args:
        image: 入力画像
        confidence_threshold: この閾値以下の場合、強力な前処理を適用

    Returns:
        前処理済み画像
    """
    # より強力な前処理: 二値化 + ノイズ除去 + シャープ化
    processed, _ = preprocess_image_for_ocr(
        image,
        apply_clahe=True,
        apply_denoise=True,
        apply_sharpen=True,
        apply_binarize=True  # 二値化を有効化
    )

    return processed


def preprocess_for_ppstructure(
    image: np.ndarray,
    enhance_contrast: bool = True,
    sharpen: bool = True
) -> Tuple[np.ndarray, dict]:
    """
    P2-1: PPStructure表検出用の画像前処理

    表罫線を強調し、PPStructureの検出精度を向上させる

    Args:
        image: 入力画像（numpy array、RGB）
        enhance_contrast: コントラスト強調を適用
        sharpen: シャープ化を適用

    Returns:
        (processed_image, stats): 前処理済み画像と統計情報
    """
    stats = {
        'original_shape': image.shape,
        'applied_operations': []
    }

    processed = image.copy()

    # 1. コントラスト強調（LABカラースペースで輝度のみ調整）
    if enhance_contrast:
        try:
            # RGB → LAB
            lab = cv2.cvtColor(processed, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)

            # CLAHE を L チャンネルに適用
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            l = clahe.apply(l)

            # LAB → RGB
            lab = cv2.merge([l, a, b])
            processed = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
            stats['applied_operations'].append('CLAHE_LAB')
        except Exception as e:
            logger.warning(f"[P2-1] コントラスト強調失敗: {e}")

    # 2. シャープ化（表罫線を強調）
    if sharpen:
        try:
            # Unsharp Masking
            blurred = cv2.GaussianBlur(processed, (0, 0), 2)
            processed = cv2.addWeighted(processed, 1.8, blurred, -0.8, 0)
            stats['applied_operations'].append('Sharpen')
        except Exception as e:
            logger.warning(f"[P2-1] シャープ化失敗: {e}")

    stats['final_shape'] = processed.shape
    stats['operations_count'] = len(stats['applied_operations'])
    stats['preproc'] = 'on' if stats['operations_count'] > 0 else 'off'

    return processed, stats


def calculate_image_quality_score(image: np.ndarray) -> float:
    """
    画像品質スコアを計算（0.0～1.0）

    ぼやけ具合やコントラストを評価し、前処理の必要性を判定

    Args:
        image: 入力画像

    Returns:
        品質スコア（高いほど高品質）
    """
    try:
        # グレースケール変換
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image

        # 1. ぼやけ検出（Laplacian分散）
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        blur_score = min(laplacian_var / 100.0, 1.0)  # 正規化

        # 2. コントラスト評価（標準偏差）
        contrast_score = min(gray.std() / 50.0, 1.0)

        # 総合スコア
        quality_score = (blur_score * 0.6 + contrast_score * 0.4)

        return quality_score

    except Exception as e:
        logger.warning(f"画像品質評価失敗: {e}")
        return 0.5  # デフォルト値
