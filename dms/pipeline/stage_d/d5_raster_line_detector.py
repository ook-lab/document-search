"""
D-5: Raster Line Detector（ラスター罫線検出） - Robust Edition

目的（本格改修版）:
1) 背景ムラ（紙テクスチャ・黄ばみ・網点）で線が爆発するのを構造的に防止
2) 罫線抽出の主軸は「方向別モルフォロジー（罫線専用）」
3) HoughLinesP は「救済」扱いで、必ず「罫線候補マスク内」に限定適用
4) 線統合は端点近似ではなく「同一直線上の区間マージ」
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
        min_line_length: int = 50,     # 最小線長（ピクセル）
        max_line_gap: int = 10,        # 線の最大ギャップ（ピクセル）
        angle_tolerance: float = 5.0,  # 水平・垂直判定の角度許容誤差（度）
        debug_dump: bool = False       # デバッグ画像を保存したい場合 True
    ):
        self.min_line_length = min_line_length
        self.max_line_gap = max_line_gap
        self.angle_tolerance = angle_tolerance
        self.debug_dump = debug_dump

        if not CV2_AVAILABLE:
            logger.error("[D-5] OpenCV が必要です")

    def detect(self, image_path: Path) -> Dict[str, Any]:
        """
        画像ファイルから罫線を検出

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
            with open(image_path, "rb") as f:
                image_bytes = np.frombuffer(f.read(), np.uint8)
                image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)

            if image is None:
                logger.error(f"[D-5] 画像読み込み失敗: {image_path}")
                return self._empty_result()

            height, width = image.shape[:2]
            logger.info(f"[D-5] 画像サイズ: {width}x{height}")

            # グレースケール
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # ------------------------------------------------------------
            # A) 背景除去（低周波除去）: 紙ムラ・黄ばみ・テクスチャを抑制
            # ------------------------------------------------------------
            flat = self._normalize_background(gray, width, height)
            logger.info("[D-5] 背景正規化（フラット化）完了")

            # ------------------------------------------------------------
            # B) 罫線候補の二値化（背景点を黒化しない方針）
            #    adaptiveThreshold(INV)+dilate は爆発源なので禁止
            # ------------------------------------------------------------
            binary = self._binarize_for_lines(flat)
            black_ratio = float(np.count_nonzero(binary)) / float(width * height)
            logger.info(f"[D-5] 二値化完了: fg_ratio={black_ratio:.4f} (白画素比; 罫線/文字候補)")

            # ------------------------------------------------------------
            # C) 方向別モルフォロジーで罫線マスク生成（主系統）
            # ------------------------------------------------------------
            h_mask = self._extract_horizontal_mask(binary, width, height)
            v_mask = self._extract_vertical_mask(binary, width, height)

            # 罫線候補マスク（OR）
            line_mask = cv2.bitwise_or(h_mask, v_mask)

            # ギャップを少しだけ埋める（強すぎるdilateは禁止。closeで限定）
            line_mask = self._light_close(line_mask)

            # マスクから線分化（輪郭→bbox）
            horizontal_lines = self._mask_to_lines(h_mask, width, height, orientation="h")
            vertical_lines = self._mask_to_lines(v_mask, width, height, orientation="v")
            logger.info(f"[D-5] モルフォロジー検出: 水平={len(horizontal_lines)}本 / 垂直={len(vertical_lines)}本")

            # ------------------------------------------------------------
            # D) Hough救済（Secondary）: 必ずマスク内限定
            # ------------------------------------------------------------
            hough_lines = self._detect_hough_lines_masked(line_mask, width, height)

            # 爆発ゲート（最後の保険）
            if len(hough_lines) > self._hough_explosion_threshold(width, height):
                logger.warning(f"[D-5] Hough爆発検知: {len(hough_lines)}本 → Hough結果を破棄（安全側）")
                hough_lines = []

            logger.info(f"[D-5] Hough救済（マスク内限定）: {len(hough_lines)}本")

            # ------------------------------------------------------------
            # E) 波線フィルタ（水平装飾線の群れ対策）
            # ------------------------------------------------------------
            all_detected = horizontal_lines + vertical_lines + hough_lines
            before_filter = len(all_detected)
            filtered_lines = self._filter_wavy_lines(all_detected, width, height)
            logger.info(f"[D-5] 波線フィルタ: {before_filter}本 → {len(filtered_lines)}本")

            # ------------------------------------------------------------
            # F) 線統合（本格）: スナップ + 同一直線上の区間マージ
            # ------------------------------------------------------------
            merged_lines = self._merge_lines_by_intervals(filtered_lines, width, height)
            logger.info(f"[D-5] 区間マージ統合: {len(filtered_lines)}本 → {len(merged_lines)}本")

            # 水平・垂直に再分類
            horizontal_final = [ln for ln in merged_lines if self._is_horizontal_normalized(ln)]
            vertical_final = [ln for ln in merged_lines if self._is_vertical_normalized(ln)]

            logger.info("[D-5] 検出完了:")
            logger.info(f"  ├─ 水平線: {len(horizontal_final)}本")
            logger.info(f"  ├─ 垂直線: {len(vertical_final)}本")
            logger.info(f"  └─ 合計: {len(merged_lines)}本")

            return {
                "horizontal_lines": horizontal_final,
                "vertical_lines": vertical_final,
                "all_lines": merged_lines,
                "image_size": (width, height),
            }

        except Exception as e:
            logger.error(f"[D-5] 検出エラー: {e}", exc_info=True)
            return self._empty_result()

    # ============================================================
    # A) 背景正規化（フラット化）
    # ============================================================
    def _normalize_background(self, gray: np.ndarray, width: int, height: int) -> np.ndarray:
        """
        背景（低周波）を推定し、divideでフラット化する。
        黄色紙・和紙ムラ・スキャン筋がある場合に特に効く。

        flat = gray / blur(gray, big)
        """
        # 画像サイズに応じた大きめカーネル（奇数に丸める）
        k = max(31, (min(width, height) // 20) | 1)  # 例: 1200pxなら ~61
        bg = cv2.GaussianBlur(gray, (k, k), 0)

        # divideで明るさムラをキャンセル（scale=255 で0-255に戻す）
        flat = cv2.divide(gray, bg, scale=255)

        # 仕上げに軽くノイズを落とす（文字の縁を潰しすぎない）
        flat = cv2.medianBlur(flat, 3)
        return flat

    # ============================================================
    # B) 罫線向け二値化（爆発しない方針）
    # ============================================================
    def _binarize_for_lines(self, flat: np.ndarray) -> np.ndarray:
        """
        Otsu + INV を基本とし、背景ムラ起因の黒点化を最小化する。
        出力は「罫線/文字候補が白(255)」のバイナリを想定。
        """
        # ぼかしで微細な紙ノイズを抑える（強すぎ禁止）
        blur = cv2.GaussianBlur(flat, (3, 3), 0)

        # Otsu（INVで暗いものが白になる）
        _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # 小さすぎる点ノイズは opening で落とす（dilateではなくopen）
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)

        return binary

    # ============================================================
    # C) 方向別罫線マスク（主系統）
    # ============================================================
    def _extract_horizontal_mask(self, binary: np.ndarray, width: int, height: int) -> np.ndarray:
        # 罫線は長いので、横長カーネルで抽出
        klen = max(self.min_line_length, width // 25)
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (klen, 1))
        detected = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel, iterations=1)
        return detected

    def _extract_vertical_mask(self, binary: np.ndarray, width: int, height: int) -> np.ndarray:
        klen = max(self.min_line_length, height // 25)
        vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, klen))
        detected = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
        return detected

    def _light_close(self, mask: np.ndarray) -> np.ndarray:
        """
        罫線の微小な欠けだけを埋める。dilateのような"連結増殖"は禁止。
        """
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        return closed

    def _mask_to_lines(self, mask: np.ndarray, width: int, height: int, orientation: str) -> List[Dict[str, Any]]:
        """
        マスク（白=線）から輪郭→bboxで線分を生成。
        orientation: "h" or "v"
        """
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        lines: List[Dict[str, Any]] = []

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)

            if orientation == "h":
                if w < self.min_line_length:
                    continue
                y_mid = y + h // 2
                lines.append({
                    "x0": x / width,
                    "y0": y_mid / height,
                    "x1": (x + w) / width,
                    "y1": y_mid / height,
                    "type": "horizontal_morph",
                })
            else:
                if h < self.min_line_length:
                    continue
                x_mid = x + w // 2
                lines.append({
                    "x0": x_mid / width,
                    "y0": y / height,
                    "x1": x_mid / width,
                    "y1": (y + h) / height,
                    "type": "vertical_morph",
                })

        return lines

    # ============================================================
    # D) Hough救済（マスク内限定）
    # ============================================================
    def _detect_hough_lines_masked(self, line_mask: np.ndarray, width: int, height: int) -> List[Dict[str, Any]]:
        """
        罫線候補マスク内だけでCanny→HoughLinesP。
        これにより文字や背景ムラを直線として拾う経路を遮断する。
        """
        if line_mask is None or line_mask.size == 0:
            return []

        # エッジ（入力はマスク＝白い線候補のみ）
        edges = cv2.Canny(line_mask, 50, 150, apertureSize=3)

        # HoughLinesP
        lines_raw = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=80,
            minLineLength=self.min_line_length,
            maxLineGap=self.max_line_gap,
        )

        if lines_raw is None:
            return []

        # 角度で水平/垂直のみを残す（斜め装飾を排除）
        out: List[Dict[str, Any]] = []
        for ln in lines_raw:
            x1, y1, x2, y2 = ln[0]
            if not self._is_axis_aligned_pixels(x1, y1, x2, y2, self.angle_tolerance):
                continue

            out.append({
                "x0": x1 / width,
                "y0": y1 / height,
                "x1": x2 / width,
                "y1": y2 / height,
                "type": "hough_masked",
            })

        return out

    def _hough_explosion_threshold(self, width: int, height: int) -> int:
        """
        これを超えたらHoughは"異常"として破棄。
        """
        base = int((width + height) * 1.5)
        return max(800, min(base, 4000))

    def _is_axis_aligned_pixels(self, x1: int, y1: int, x2: int, y2: int, angle_tol_deg: float) -> bool:
        """
        ピクセル座標の線分が水平/垂直に近いか。
        """
        dx = float(x2 - x1)
        dy = float(y2 - y1)
        if dx == 0 and dy == 0:
            return False
        angle = abs(np.degrees(np.arctan2(dy, dx)))
        if angle <= angle_tol_deg or abs(angle - 180.0) <= angle_tol_deg:
            return True
        if abs(angle - 90.0) <= angle_tol_deg:
            return True
        return False

    # ============================================================
    # E) 波線（水平装飾線）フィルタ
    # ============================================================
    def _filter_wavy_lines(
        self,
        lines: List[Dict[str, Any]],
        width: int,
        height: int,
        y_tolerance: float = 0.005
    ) -> List[Dict[str, Any]]:
        """
        同一Y帯に短い線が多数並ぶパターン（波線装飾）を除外。
        """
        if not lines:
            return []

        horizontal_groups: Dict[float, List[Dict[str, Any]]] = {}
        vertical_and_others: List[Dict[str, Any]] = []

        for line in lines:
            y_diff = abs(line["y0"] - line["y1"])
            if y_diff < 0.01:
                y_avg = (line["y0"] + line["y1"]) / 2
                found = False
                for gy in list(horizontal_groups.keys()):
                    if abs(y_avg - gy) < y_tolerance:
                        horizontal_groups[gy].append(line)
                        found = True
                        break
                if not found:
                    horizontal_groups[y_avg] = [line]
            else:
                vertical_and_others.append(line)

        WAVY_LINE_THRESHOLD = 5
        filtered_horizontal: List[Dict[str, Any]] = []
        wavy_count = 0

        for gy, glist in horizontal_groups.items():
            if len(glist) >= WAVY_LINE_THRESHOLD:
                wavy_count += len(glist)
            else:
                filtered_horizontal.extend(glist)

        logger.info(f"[D-5] 波線除外詳細: {wavy_count}本除外, {len(filtered_horizontal)}本保持")
        return filtered_horizontal + vertical_and_others

    # ============================================================
    # F) 本格マージ：スナップ + 区間マージ
    # ============================================================
    def _merge_lines_by_intervals(self, lines: List[Dict[str, Any]], width: int, height: int) -> List[Dict[str, Any]]:
        """
        端点一致の重複除去ではなく、同一直線上の線分を連結して1本にする。
        """
        if not lines:
            return []

        # 正規化→ピクセルへ
        pix = []
        for ln in lines:
            x1 = int(round(ln["x0"] * width))
            y1 = int(round(ln["y0"] * height))
            x2 = int(round(ln["x1"] * width))
            y2 = int(round(ln["y1"] * height))
            pix.append((x1, y1, x2, y2, ln.get("type", "unknown")))

        angle_tol = self.angle_tolerance
        tan_tol = np.tan(np.radians(angle_tol))

        horiz = []
        vert = []
        for x1, y1, x2, y2, tp in pix:
            if not self._is_axis_aligned_pixels(x1, y1, x2, y2, angle_tol):
                continue
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            if dy <= dx * tan_tol:
                # 水平
                y = int(round((y1 + y2) / 2))
                xa, xb = sorted([x1, x2])
                horiz.append((xa, y, xb, y, tp))
            elif dx <= dy * tan_tol:
                # 垂直
                x = int(round((x1 + x2) / 2))
                ya, yb = sorted([y1, y2])
                vert.append((x, ya, x, yb, tp))

        y_tol = max(2, int(round(height * 0.003)))
        x_tol = max(2, int(round(width * 0.003)))
        gap = max(2, self.max_line_gap)

        merged_h = self._merge_horizontal_intervals(horiz, y_tol=y_tol, gap=gap)
        merged_v = self._merge_vertical_intervals(vert, x_tol=x_tol, gap=gap)

        out: List[Dict[str, Any]] = []
        for xa, y, xb in merged_h:
            if (xb - xa) < self.min_line_length:
                continue
            out.append({
                "x0": xa / width,
                "y0": y / height,
                "x1": xb / width,
                "y1": y / height,
                "type": "merged_horizontal",
            })

        for x, ya, yb in merged_v:
            if (yb - ya) < self.min_line_length:
                continue
            out.append({
                "x0": x / width,
                "y0": ya / height,
                "x1": x / width,
                "y1": yb / height,
                "type": "merged_vertical",
            })

        return out

    def _merge_horizontal_intervals(
        self,
        horiz: List[Tuple[int, int, int, int, str]],
        y_tol: int,
        gap: int
    ) -> List[Tuple[int, int, int]]:
        """(x0,y,x1,y) の水平線分をy帯でグループ化し、x区間を連結して1本化。"""
        if not horiz:
            return []

        horiz_sorted = sorted(horiz, key=lambda t: t[1])
        groups: List[List[Tuple[int, int, int, int, str]]] = []
        cur: List[Tuple[int, int, int, int, str]] = [horiz_sorted[0]]
        cur_y = horiz_sorted[0][1]

        for seg in horiz_sorted[1:]:
            y = seg[1]
            if abs(y - cur_y) <= y_tol:
                cur.append(seg)
                cur_y = int(round((cur_y * (len(cur) - 1) + y) / len(cur)))
            else:
                groups.append(cur)
                cur = [seg]
                cur_y = y
        groups.append(cur)

        merged: List[Tuple[int, int, int]] = []
        for g in groups:
            y = int(round(sum(s[1] for s in g) / len(g)))
            intervals = sorted([(s[0], s[2]) for s in g], key=lambda t: t[0])
            x0, x1 = intervals[0]
            for a, b in intervals[1:]:
                if a <= x1 + gap:
                    x1 = max(x1, b)
                else:
                    merged.append((x0, y, x1))
                    x0, x1 = a, b
            merged.append((x0, y, x1))

        return merged

    def _merge_vertical_intervals(
        self,
        vert: List[Tuple[int, int, int, int, str]],
        x_tol: int,
        gap: int
    ) -> List[Tuple[int, int, int]]:
        """(x,y0,x,y1) の垂直線分をx帯でグループ化し、y区間を連結して1本化。"""
        if not vert:
            return []

        vert_sorted = sorted(vert, key=lambda t: t[0])
        groups: List[List[Tuple[int, int, int, int, str]]] = []
        cur: List[Tuple[int, int, int, int, str]] = [vert_sorted[0]]
        cur_x = vert_sorted[0][0]

        for seg in vert_sorted[1:]:
            x = seg[0]
            if abs(x - cur_x) <= x_tol:
                cur.append(seg)
                cur_x = int(round((cur_x * (len(cur) - 1) + x) / len(cur)))
            else:
                groups.append(cur)
                cur = [seg]
                cur_x = x
        groups.append(cur)

        merged: List[Tuple[int, int, int]] = []
        for g in groups:
            x = int(round(sum(s[0] for s in g) / len(g)))
            intervals = sorted([(s[1], s[3]) for s in g], key=lambda t: t[0])
            y0, y1 = intervals[0]
            for a, b in intervals[1:]:
                if a <= y1 + gap:
                    y1 = max(y1, b)
                else:
                    merged.append((x, y0, y1))
                    y0, y1 = a, b
            merged.append((x, y0, y1))

        return merged

    # ============================================================
    # 判定系
    # ============================================================
    def _is_horizontal_normalized(self, line: Dict[str, Any], tolerance: float = 0.02) -> bool:
        return abs(line["y1"] - line["y0"]) < tolerance

    def _is_vertical_normalized(self, line: Dict[str, Any], tolerance: float = 0.02) -> bool:
        return abs(line["x1"] - line["x0"]) < tolerance

    # ============================================================
    # 空結果
    # ============================================================
    def _empty_result(self) -> Dict[str, Any]:
        return {
            "horizontal_lines": [],
            "vertical_lines": [],
            "all_lines": [],
            "image_size": (0, 0),
        }
