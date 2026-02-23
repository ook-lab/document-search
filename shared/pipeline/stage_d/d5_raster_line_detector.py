"""
D-5: Raster Line Detector（ラスター罫線検出）

OpenCV を使用して、スキャン画像やラスタライズされたPDF画像から
罫線を検出する。

目的:
1. B-90で生成されたテキスト消去済み画像から罫線を検出
2. 水平・垂直な直線をピクセルレベルで抽出
3. スキャン時の微妙な傾き（Skew）を考慮した近似処理
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("[D-5] OpenCV がインストールされていません")


class D5RasterLineDetector:
    """D-5: Raster Line Detector（ラスター罫線検出）"""

    def __init__(
        self,
        min_line_length: int = 50,  # 最小線長（ピクセル）
        max_line_gap: int = 10,     # 線の最大ギャップ（ピクセル）
        angle_tolerance: float = 5.0  # 水平・垂直判定の角度許容誤差（度）
    ):
        """
        Raster Line Detector 初期化

        Args:
            min_line_length: 罫線として認識する最小の長さ（ピクセル）
            max_line_gap: 線の途切れを許容する最大ギャップ（ピクセル）
            angle_tolerance: 水平・垂直判定の角度許容誤差（度）
        """
        self.min_line_length = min_line_length
        self.max_line_gap = max_line_gap
        self.angle_tolerance = angle_tolerance

        if not CV2_AVAILABLE:
            logger.error("[D-5] OpenCV が必要です")

    def detect(
        self,
        image_path: Path,
    ) -> Dict[str, Any]:
        """
        画像ファイルから罫線を検出

        Args:
            image_path: 画像ファイルパス（B-90生成のpurged_image）

        Returns:
            {
                'horizontal_lines': [...],  # 水平線リスト（正規化座標）
                'vertical_lines': [...],    # 垂直線リスト（正規化座標）
                'all_lines': [...],         # 全線リスト（正規化座標）
                'image_size': (width, height)
            }
        """
        if not CV2_AVAILABLE:
            logger.error("[D-5] OpenCV が利用できません")
            return self._empty_result()

        logger.info(f"[D-5] ラスター罫線検出開始: {image_path.name}")

        try:
            # 画像を読み込み（日本語パス対応）
            import numpy as np
            with open(image_path, 'rb') as f:
                image_bytes = np.frombuffer(f.read(), np.uint8)
                image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)

            if image is None:
                logger.error(f"[D-5] 画像読み込み失敗: {image_path}")
                return self._empty_result()

            height, width = image.shape[:2]
            logger.info(f"[D-5] 画像サイズ: {width}x{height}")

            # グレースケール変換
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # 二値化（Adaptive Thresholding）
            binary = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV,
                11,
                2
            )

            # ダイレーション（膨張処理）: 線を太らせて微妙なズレを吸収
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            binary = cv2.dilate(binary, kernel, iterations=1)
            logger.info("[D-5] ダイレーション処理完了（±3px相当の隙間を吸収）")

            # 水平線検出
            horizontal_lines = self._detect_horizontal_lines(binary, width, height)
            logger.info(f"[D-5] モルフォロジー水平線検出: {len(horizontal_lines)}本")
            if horizontal_lines:
                logger.info(f"[D-5] 水平線 全件:")
                for i, line in enumerate(horizontal_lines, 1):
                    logger.info(f"  {i}. x0={line['x0']:.3f}, y0={line['y0']:.3f}, x1={line['x1']:.3f}, y1={line['y1']:.3f}, type={line.get('type')}")

            # 垂直線検出
            vertical_lines = self._detect_vertical_lines(binary, width, height)
            logger.info(f"[D-5] モルフォロジー垂直線検出: {len(vertical_lines)}本")
            if vertical_lines:
                logger.info(f"[D-5] 垂直線 全件:")
                for i, line in enumerate(vertical_lines, 1):
                    logger.info(f"  {i}. x0={line['x0']:.3f}, y0={line['y0']:.3f}, x1={line['x1']:.3f}, y1={line['y1']:.3f}, type={line.get('type')}")

            # HoughLinesP で補完的に検出
            hough_lines = self._detect_hough_lines(binary, width, height)
            logger.info(f"[D-5] Hough Lines検出: {len(hough_lines)}本")
            if hough_lines:
                logger.info(f"[D-5] Hough線 全件:")
                for i, line in enumerate(hough_lines, 1):
                    logger.info(f"  {i}. x0={line['x0']:.3f}, y0={line['y0']:.3f}, x1={line['x1']:.3f}, y1={line['y1']:.3f}, type={line.get('type')}")

            # 波線フィルタ（装飾線を除外）
            all_detected = horizontal_lines + vertical_lines + hough_lines
            before_filter = len(all_detected)
            filtered_lines = self._filter_wavy_lines(all_detected, width, height)
            logger.info(f"[D-5] 波線フィルタ: {before_filter}本 → {len(filtered_lines)}本（波線除外）")

            # 全線を統合
            all_lines = self._merge_lines(filtered_lines, width, height)
            logger.info(f"[D-5] 線マージ: {len(filtered_lines)}本 → {len(all_lines)}本（重複除去）")

            # 水平・垂直に再分類
            horizontal_final = [
                line for line in all_lines
                if self._is_horizontal_normalized(line)
            ]
            vertical_final = [
                line for line in all_lines
                if self._is_vertical_normalized(line)
            ]

            logger.info(f"[D-5] 検出完了:")
            logger.info(f"  ├─ 水平線: {len(horizontal_final)}本")
            logger.info(f"  ├─ 垂直線: {len(vertical_final)}本")
            logger.info(f"  └─ 合計: {len(all_lines)}本")

            return {
                'horizontal_lines': horizontal_final,
                'vertical_lines': vertical_final,
                'all_lines': all_lines,
                'image_size': (width, height)
            }

        except Exception as e:
            logger.error(f"[D-5] 検出エラー: {e}", exc_info=True)
            return self._empty_result()

    def _detect_horizontal_lines(
        self,
        binary: np.ndarray,
        width: int,
        height: int
    ) -> List[Dict[str, Any]]:
        """
        モルフォロジー変換で水平線を検出

        Args:
            binary: 二値化画像
            width: 画像幅
            height: 画像高さ

        Returns:
            正規化座標の水平線リスト
        """
        # 水平方向のカーネル
        horizontal_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (width // 30, 1)  # 幅の1/30程度の長さを持つ水平カーネル
        )

        # モルフォロジー変換
        detected = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            horizontal_kernel,
            iterations=2
        )

        # 輪郭抽出
        contours, _ = cv2.findContours(
            detected,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        lines = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)

            # 十分な長さを持つ線のみ
            if w < self.min_line_length:
                continue

            # 正規化座標に変換
            lines.append({
                'x0': x / width,
                'y0': (y + h // 2) / height,
                'x1': (x + w) / width,
                'y1': (y + h // 2) / height,
                'type': 'horizontal_morph'
            })

        return lines

    def _detect_vertical_lines(
        self,
        binary: np.ndarray,
        width: int,
        height: int
    ) -> List[Dict[str, Any]]:
        """
        モルフォロジー変換で垂直線を検出

        Args:
            binary: 二値化画像
            width: 画像幅
            height: 画像高さ

        Returns:
            正規化座標の垂直線リスト
        """
        # 垂直方向のカーネル
        vertical_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (1, height // 30)  # 高さの1/30程度の長さを持つ垂直カーネル
        )

        # モルフォロジー変換
        detected = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            vertical_kernel,
            iterations=2
        )

        # 輪郭抽出
        contours, _ = cv2.findContours(
            detected,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        lines = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)

            # 十分な長さを持つ線のみ
            if h < self.min_line_length:
                continue

            # 正規化座標に変換
            lines.append({
                'x0': (x + w // 2) / width,
                'y0': y / height,
                'x1': (x + w // 2) / width,
                'y1': (y + h) / height,
                'type': 'vertical_morph'
            })

        return lines

    def _detect_hough_lines(
        self,
        binary: np.ndarray,
        width: int,
        height: int
    ) -> List[Dict[str, Any]]:
        """
        HoughLinesP で直線を検出

        Args:
            binary: 二値化画像
            width: 画像幅
            height: 画像高さ

        Returns:
            正規化座標の線リスト
        """
        # エッジ検出
        edges = cv2.Canny(binary, 50, 150, apertureSize=3)

        # HoughLinesP
        lines_raw = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=100,
            minLineLength=self.min_line_length,
            maxLineGap=self.max_line_gap
        )

        if lines_raw is None:
            return []

        lines = []
        for line in lines_raw:
            x1, y1, x2, y2 = line[0]

            # 正規化座標に変換
            lines.append({
                'x0': x1 / width,
                'y0': y1 / height,
                'x1': x2 / width,
                'y1': y2 / height,
                'type': 'hough'
            })

        return lines

    def _filter_wavy_lines(
        self,
        lines: List[Dict[str, Any]],
        width: int,
        height: int,
        y_tolerance: float = 0.005  # Y座標の許容誤差（正規化座標）
    ) -> List[Dict[str, Any]]:
        """
        波線（装飾線）を除外する

        波線は複数の短い直線が同じY座標に並んでいる特徴がある。
        同じY座標に5本以上の線がある場合、それらを波線と判定して除外する。

        Args:
            lines: 線リスト
            width: 画像幅
            height: 画像高さ
            y_tolerance: Y座標の許容誤差

        Returns:
            波線を除外した線リスト
        """
        if not lines:
            return []

        # Y座標でグループ化（水平線のみ）
        horizontal_groups = {}
        vertical_and_others = []

        for line in lines:
            # 水平線かチェック
            y_diff = abs(line['y0'] - line['y1'])
            if y_diff < 0.01:  # ほぼ水平
                # Y座標の平均
                y_avg = (line['y0'] + line['y1']) / 2

                # 既存のグループに追加
                found_group = False
                for group_y, group_lines in horizontal_groups.items():
                    if abs(y_avg - group_y) < y_tolerance:
                        group_lines.append(line)
                        found_group = True
                        break

                if not found_group:
                    horizontal_groups[y_avg] = [line]
            else:
                # 垂直線またはその他
                vertical_and_others.append(line)

        # 波線判定（同じY座標に5本以上の線 → 波線）
        WAVY_LINE_THRESHOLD = 5
        filtered_horizontal = []
        wavy_count = 0

        for group_y, group_lines in horizontal_groups.items():
            if len(group_lines) >= WAVY_LINE_THRESHOLD:
                # 波線と判定して除外
                logger.debug(f"[D-5] 波線検出: y={group_y:.3f}, {len(group_lines)}本の線 → 除外")
                wavy_count += len(group_lines)
            else:
                # 通常の罫線として保持
                filtered_horizontal.extend(group_lines)

        logger.info(f"[D-5] 波線除外詳細: {wavy_count}本除外, {len(filtered_horizontal)}本保持")

        return filtered_horizontal + vertical_and_others

    def _merge_lines(
        self,
        lines: List[Dict[str, Any]],
        width: int,
        height: int,
        tolerance: float = 0.01  # 正規化座標での許容誤差
    ) -> List[Dict[str, Any]]:
        """
        重複する線を統合

        Args:
            lines: 線リスト
            width: 画像幅
            height: 画像高さ
            tolerance: 重複判定の許容誤差

        Returns:
            統合済み線リスト
        """
        if not lines:
            return []

        # 簡易的な重複除去
        unique_lines = []
        for line in lines:
            is_duplicate = False
            for existing in unique_lines:
                if self._is_similar_line(line, existing, tolerance):
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique_lines.append(line)

        return unique_lines

    def _is_similar_line(
        self,
        line1: Dict[str, Any],
        line2: Dict[str, Any],
        tolerance: float
    ) -> bool:
        """
        2つの線が類似しているかを判定

        Args:
            line1: 線1
            line2: 線2
            tolerance: 許容誤差

        Returns:
            類似していればTrue
        """
        return (
            abs(line1['x0'] - line2['x0']) < tolerance and
            abs(line1['y0'] - line2['y0']) < tolerance and
            abs(line1['x1'] - line2['x1']) < tolerance and
            abs(line1['y1'] - line2['y1']) < tolerance
        )

    def _is_horizontal_normalized(
        self,
        line: Dict[str, Any],
        tolerance: float = 0.02
    ) -> bool:
        """
        正規化座標の線が水平かどうかを判定

        Args:
            line: 線
            tolerance: 許容誤差

        Returns:
            水平ならTrue
        """
        return abs(line['y1'] - line['y0']) < tolerance

    def _is_vertical_normalized(
        self,
        line: Dict[str, Any],
        tolerance: float = 0.02
    ) -> bool:
        """
        正規化座標の線が垂直かどうかを判定

        Args:
            line: 線
            tolerance: 許容誤差

        Returns:
            垂直ならTrue
        """
        return abs(line['x1'] - line['x0']) < tolerance

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'horizontal_lines': [],
            'vertical_lines': [],
            'all_lines': [],
            'image_size': (0, 0)
        }
