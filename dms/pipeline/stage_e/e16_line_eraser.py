"""
E-16: Line Eraser（罫線除去・ノイズ除去）

E-21（Gemini OCR）に渡す前の画像前処理。
罫線・細線を文字と誤読する問題（"1"大量発生）を根本から防ぐ。

処理ステップ:
1. グレースケール化
2. 適応的二値化（文字=白、背景=黒）
3. モルフォロジーで水平線・垂直線を抽出→除去
4. 連結成分フィルタ（物理的に文字として成立しない極小成分を除去）
5. クリーニング済み画像をファイル保存して返す

根拠:
- 罫線は文字ではない（物理的に別成分）
- 3×2px の連結成分は文字のストロークとして成立しない
  （300dpiで6pt文字 ≈ 25px。それ以下は文字ではない）
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Optional
from loguru import logger


class E16LineEraser:
    """E-16: Line Eraser（罫線除去・ノイズ除去）"""

    def __init__(
        self,
        min_char_height_px: int = 10,
        min_area_px: int = 40,
    ):
        """
        初期化

        Args:
            min_char_height_px: 文字として認める最小高さ（px）
                               150dpi で 6pt ≈ 12px を下回るものは文字ではない
            min_area_px:        文字として認める最小面積（px²）
        """
        self.min_char_height_px = min_char_height_px
        self.min_area_px = min_area_px

    def erase(
        self,
        image_path: Path,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """
        画像から罫線・ノイズを除去してクリーニング済み画像を保存

        Args:
            image_path: 元画像パス
            output_dir: 保存先（省略時は元画像と同ディレクトリ）

        Returns:
            クリーニング済み画像パス（失敗時は元画像パスをフォールバック）
        """
        logger.info(f"[E-16] 罫線除去開始: {image_path.name}")

        output_dir = output_dir or image_path.parent
        output_path = output_dir / (image_path.stem + "_cleaned" + image_path.suffix)

        img = cv2.imread(str(image_path))
        if img is None:
            logger.error(f"[E-16] 画像読み込み失敗: {image_path} → 元画像で続行")
            return image_path

        h, w = img.shape[:2]
        logger.info(f"[E-16] 画像サイズ: {w}x{h}")

        # ─── Step 1: グレースケール → 二値化（文字=白、背景=黒）───
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        bin_img = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            25, 15,
        )

        # ─── Step 2: 罫線除去 ───────────────────────────────────────
        # カーネルサイズを画像サイズに比例させる（DPI差を吸収）
        h_kernel_w = max(40, w // 15)  # 水平線: 幅の1/15以上
        v_kernel_h = max(40, h // 15)  # 垂直線: 高さの1/15以上

        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel_w, 1))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_kernel_h))

        horizontal_lines = cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, h_kernel)
        vertical_lines   = cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, v_kernel)

        lines_mask = cv2.bitwise_or(horizontal_lines, vertical_lines)
        text_only  = cv2.subtract(bin_img, lines_mask)

        h_px = int(np.sum(horizontal_lines > 0))
        v_px = int(np.sum(vertical_lines > 0))
        logger.info(f"[E-16] 罫線除去: 水平={h_px}px, 垂直={v_px}px")

        # ─── Step 3: 極小連結成分除去 ───────────────────────────────
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            text_only, connectivity=8
        )

        cleaned_mask = np.zeros_like(text_only)
        kept = removed = 0

        for i in range(1, num_labels):  # 0 は背景
            _x, _y, cw, ch, area = stats[i]
            if ch >= self.min_char_height_px and area >= self.min_area_px:
                cleaned_mask[labels == i] = 255
                kept += 1
            else:
                removed += 1

        logger.info(f"[E-16] 連結成分: 保持={kept}個, 除去={removed}個")

        # ─── Step 4: 白背景・黒文字の画像として保存 ─────────────────
        result = np.full_like(img, 255)          # 白背景
        result[cleaned_mask > 0] = [0, 0, 0]    # 文字部分は黒

        cv2.imwrite(str(output_path), result)
        logger.info(f"[E-16] 保存完了: {output_path.name}")

        return output_path
