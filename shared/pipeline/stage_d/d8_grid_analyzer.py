"""
D-8: Grid Intersection Analyzer（格子交点解析）

D-3とD-5で抽出した罫線を統合し、交点を計算して表構造を理解する。

目的:
1. ベクトル線とラスター線を統合
2. 縦線と横線の交点を全て算出
3. 交点から表の外郭（bbox）を特定
4. 表領域を個別に認識
"""

from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path
from loguru import logger
import numpy as np

try:
    import fitz  # PyMuPDF
    from PIL import Image
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("[D-8] PyMuPDF/PIL が必要です（不可視線フィルタ用）")


class D8GridAnalyzer:
    """D-8: Grid Intersection Analyzer（格子交点解析）"""

    def __init__(
        self,
        intersection_tolerance: float = 0.01,  # 交点判定の許容誤差（正規化座標）
        min_table_size: float = 0.05,  # 表として認識する最小サイズ（正規化座標）
        exclude_page_edges: bool = True,  # ページ四辺の枠線を除外
        page_edge_tolerance: float = 0.02,  # ページ端からの許容誤差
        filter_invisible_lines: bool = True,  # 不可視線を除外
        render_dpi: int = 150,  # レンダリング解像度
        visibility_threshold: float = 0.1  # 可視性閾値（非白ピクセル比率）
    ):
        """
        Grid Analyzer 初期化

        Args:
            intersection_tolerance: 交点判定の許容誤差（正規化座標）
            min_table_size: 表として認識する最小サイズ（正規化座標）
            exclude_page_edges: ページ四辺の枠線を除外するか
            page_edge_tolerance: ページ端からの許容誤差（正規化座標）
            filter_invisible_lines: 不可視線を除外するか
            render_dpi: レンダリング解像度（DPI）
            visibility_threshold: 可視性閾値（非白ピクセル比率）
        """
        self.intersection_tolerance = intersection_tolerance
        self.min_table_size = min_table_size
        self.exclude_page_edges = exclude_page_edges
        self.page_edge_tolerance = page_edge_tolerance
        self.filter_invisible_lines = filter_invisible_lines
        self.render_dpi = render_dpi
        self.visibility_threshold = visibility_threshold

    def analyze(
        self,
        vector_result: Dict[str, Any],
        raster_result: Optional[Dict[str, Any]] = None,
        pdf_path: Optional[str] = None,
        page_index: int = 0
    ) -> Dict[str, Any]:
        """
        罫線から格子構造を解析

        Args:
            vector_result: D-3の結果（ベクトル線）
            raster_result: D-5の結果（ラスター線、オプション）
            pdf_path: PDF ファイルパス（不可視線フィルタ用）
            page_index: ページ番号（0-indexed）

        Returns:
            {
                'intersections': [...],  # 交点リスト
                'table_regions': [...],  # 表領域リスト
                'unified_lines': {
                    'horizontal': [...],
                    'vertical': [...]
                }
            }
        """
        logger.info("[D-8] 格子解析開始")

        # 線を統合
        h_lines, v_lines = self._unify_lines(vector_result, raster_result)

        logger.info(f"[D-8] 統合線（before フィルタ）:")
        logger.info(f"  ├─ 水平線: {len(h_lines)}本")
        logger.info(f"  └─ 垂直線: {len(v_lines)}本")

        # ページ四辺の枠線を除外（表判定入力のクリーン化）
        if self.exclude_page_edges:
            h_lines_before = len(h_lines)
            v_lines_before = len(v_lines)

            h_lines, v_lines = self._filter_page_edges(h_lines, v_lines)

            logger.info(f"[D-8] ページ外枠除外:")
            logger.info(f"  ├─ 水平線: {h_lines_before} → {len(h_lines)} ({h_lines_before - len(h_lines)} 除外)")
            logger.info(f"  └─ 垂直線: {v_lines_before} → {len(v_lines)} ({v_lines_before - len(v_lines)} 除外)")

        # 不可視線を除外（レンダリングベース）
        if self.filter_invisible_lines and pdf_path and PYMUPDF_AVAILABLE:
            h_lines_before = len(h_lines)
            v_lines_before = len(v_lines)

            h_lines, v_lines = self._filter_invisible_lines(
                h_lines, v_lines, pdf_path, page_index
            )

            logger.info(f"[D-8] 不可視線除外:")
            logger.info(f"  ├─ 水平線: {h_lines_before} → {len(h_lines)} ({h_lines_before - len(h_lines)} 除外)")
            logger.info(f"  └─ 垂直線: {v_lines_before} → {len(v_lines)} ({v_lines_before - len(v_lines)} 除外)")
        elif self.filter_invisible_lines and not PYMUPDF_AVAILABLE:
            logger.warning("[D-8] 不可視線フィルタスキップ: PyMuPDF が利用できません")

        # 交点を計算
        intersections = self._find_intersections(h_lines, v_lines)
        logger.info(f"[D-8] 交点: {len(intersections)}個")

        # 表領域を特定
        table_regions = self._identify_table_regions(
            h_lines,
            v_lines,
            intersections
        )
        logger.info(f"[D-8] 表領域: {len(table_regions)}個")

        return {
            'intersections': intersections,
            'table_regions': table_regions,
            'unified_lines': {
                'horizontal': h_lines,
                'vertical': v_lines
            }
        }

    def _unify_lines(
        self,
        vector_result: Dict[str, Any],
        raster_result: Optional[Dict[str, Any]]
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        ベクトル線とラスター線を統合

        Args:
            vector_result: D-3の結果
            raster_result: D-5の結果

        Returns:
            (水平線リスト, 垂直線リスト)
        """
        # ベクトル線
        h_lines = vector_result.get('horizontal_lines', []).copy()
        v_lines = vector_result.get('vertical_lines', []).copy()

        # ラスター線を追加（存在する場合）
        if raster_result:
            h_lines.extend(raster_result.get('horizontal_lines', []))
            v_lines.extend(raster_result.get('vertical_lines', []))

        # 重複除去
        h_lines = self._deduplicate_lines(h_lines)
        v_lines = self._deduplicate_lines(v_lines)

        return h_lines, v_lines

    def _deduplicate_lines(
        self,
        lines: List[Dict[str, Any]],
        tolerance: float = 0.005
    ) -> List[Dict[str, Any]]:
        """
        重複する線を除去

        Args:
            lines: 線リスト
            tolerance: 重複判定の許容誤差

        Returns:
            重複除去済み線リスト
        """
        if not lines:
            return []

        unique = []
        for line in lines:
            is_duplicate = False
            for existing in unique:
                if self._is_same_line(line, existing, tolerance):
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique.append(line)

        return unique

    def _is_same_line(
        self,
        line1: Dict[str, Any],
        line2: Dict[str, Any],
        tolerance: float
    ) -> bool:
        """
        2つの線が同一かどうかを判定

        Args:
            line1: 線1
            line2: 線2
            tolerance: 許容誤差

        Returns:
            同一ならTrue
        """
        return (
            abs(line1['x0'] - line2['x0']) < tolerance and
            abs(line1['y0'] - line2['y0']) < tolerance and
            abs(line1['x1'] - line2['x1']) < tolerance and
            abs(line1['y1'] - line2['y1']) < tolerance
        )

    def _find_intersections(
        self,
        h_lines: List[Dict[str, Any]],
        v_lines: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        水平線と垂直線の交点を全て算出

        Args:
            h_lines: 水平線リスト
            v_lines: 垂直線リスト

        Returns:
            交点リスト [{'x': float, 'y': float}, ...]
        """
        intersections = []

        for h_line in h_lines:
            for v_line in v_lines:
                intersection = self._line_intersection(h_line, v_line)
                if intersection:
                    intersections.append(intersection)

        # 重複除去
        intersections = self._deduplicate_intersections(intersections)

        return intersections

    def _line_intersection(
        self,
        h_line: Dict[str, Any],
        v_line: Dict[str, Any]
    ) -> Optional[Dict[str, float]]:
        """
        水平線と垂直線の交点を計算

        Args:
            h_line: 水平線
            v_line: 垂直線

        Returns:
            交点 {'x': float, 'y': float} または None
        """
        # 水平線のY座標
        h_y = (h_line['y0'] + h_line['y1']) / 2
        # 垂直線のX座標
        v_x = (v_line['x0'] + v_line['x1']) / 2

        # 水平線のX範囲
        h_x_min = min(h_line['x0'], h_line['x1'])
        h_x_max = max(h_line['x0'], h_line['x1'])

        # 垂直線のY範囲
        v_y_min = min(v_line['y0'], v_line['y1'])
        v_y_max = max(v_line['y0'], v_line['y1'])

        # 交点が両方の線上にあるか確認
        tol = self.intersection_tolerance
        if (h_x_min - tol <= v_x <= h_x_max + tol and
            v_y_min - tol <= h_y <= v_y_max + tol):
            return {'x': v_x, 'y': h_y}

        return None

    def _deduplicate_intersections(
        self,
        intersections: List[Dict[str, float]],
        tolerance: float = 0.005
    ) -> List[Dict[str, float]]:
        """
        重複する交点を除去

        Args:
            intersections: 交点リスト
            tolerance: 重複判定の許容誤差

        Returns:
            重複除去済み交点リスト
        """
        if not intersections:
            return []

        unique = []
        for point in intersections:
            is_duplicate = False
            for existing in unique:
                if (abs(point['x'] - existing['x']) < tolerance and
                    abs(point['y'] - existing['y']) < tolerance):
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique.append(point)

        return unique

    def _identify_table_regions(
        self,
        h_lines: List[Dict[str, Any]],
        v_lines: List[Dict[str, Any]],
        intersections: List[Dict[str, float]]
    ) -> List[Dict[str, Any]]:
        """
        交点から表領域を特定

        Args:
            h_lines: 水平線リスト
            v_lines: 垂直線リスト
            intersections: 交点リスト

        Returns:
            表領域リスト
        """
        if len(intersections) < 4:
            # 最低4つの交点がないと矩形を作れない
            return []

        # 交点の外郭を計算
        x_coords = [p['x'] for p in intersections]
        y_coords = [p['y'] for p in intersections]

        x_min = min(x_coords)
        x_max = max(x_coords)
        y_min = min(y_coords)
        y_max = max(y_coords)

        # サイズチェック
        width = x_max - x_min
        height = y_max - y_min

        if width < self.min_table_size or height < self.min_table_size:
            logger.warning(f"[D-8] 表が小さすぎます: {width:.3f}x{height:.3f}")
            return []

        # 単一の表領域として返す（複雑な場合は連結成分解析が必要）
        table_region = {
            'table_id': 'T1',
            'bbox': [x_min, y_min, x_max, y_max],
            'intersection_count': len(intersections),
            'h_line_count': len(h_lines),
            'v_line_count': len(v_lines)
        }

        logger.info(f"[D-8] 表領域 T1:")
        logger.info(f"  ├─ Bbox: [{x_min:.3f}, {y_min:.3f}, {x_max:.3f}, {y_max:.3f}]")
        logger.info(f"  ├─ サイズ: {width:.3f}x{height:.3f}")
        logger.info(f"  └─ 交点数: {len(intersections)}")

        return [table_region]

    def _filter_page_edges(
        self,
        h_lines: List[Dict[str, Any]],
        v_lines: List[Dict[str, Any]]
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        ページ四辺の枠線を除外（表判定入力のクリーン化）

        Args:
            h_lines: 水平線リスト
            v_lines: 垂直線リスト

        Returns:
            フィルタリング後の (h_lines, v_lines)
        """
        tol = self.page_edge_tolerance

        # 水平線をフィルタ: y==0 or y==1 の線を除外
        filtered_h_lines = []
        for line in h_lines:
            y = (line['y0'] + line['y1']) / 2
            if tol < y < (1.0 - tol):  # ページ上端・下端から一定距離以上離れている
                filtered_h_lines.append(line)
            else:
                logger.debug(f"[D-8] 除外（ページ上下端）: y={y:.3f}")

        # 垂直線をフィルタ: x==0 or x==1 の線を除外
        filtered_v_lines = []
        for line in v_lines:
            x = (line['x0'] + line['x1']) / 2
            if tol < x < (1.0 - tol):  # ページ左端・右端から一定距離以上離れている
                filtered_v_lines.append(line)
            else:
                logger.debug(f"[D-8] 除外（ページ左右端）: x={x:.3f}")

        return filtered_h_lines, filtered_v_lines

    def _filter_invisible_lines(
        self,
        h_lines: List[Dict[str, Any]],
        v_lines: List[Dict[str, Any]],
        pdf_path: str,
        page_index: int
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        不可視線を除外（レンダリングベース）

        Args:
            h_lines: 水平線リスト
            v_lines: 垂直線リスト
            pdf_path: PDF ファイルパス
            page_index: ページ番号

        Returns:
            フィルタリング後の (h_lines, v_lines)
        """
        try:
            # PDF をレンダリング
            doc = fitz.open(pdf_path)
            page = doc.load_page(page_index)
            pix = page.get_pixmap(dpi=self.render_dpi)

            # PIL Image に変換
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_array = np.array(img.convert('L'))  # グレースケール化

            page_width = pix.width
            page_height = pix.height
            doc.close()

            # 水平線をフィルタ
            filtered_h_lines = []
            for line in h_lines:
                if self._is_line_visible(line, img_array, page_width, page_height, is_horizontal=True):
                    filtered_h_lines.append(line)
                else:
                    y = (line['y0'] + line['y1']) / 2
                    logger.debug(f"[D-8] 除外（不可視水平線）: y={y:.3f}")

            # 垂直線をフィルタ
            filtered_v_lines = []
            for line in v_lines:
                if self._is_line_visible(line, img_array, page_width, page_height, is_horizontal=False):
                    filtered_v_lines.append(line)
                else:
                    x = (line['x0'] + line['x1']) / 2
                    logger.debug(f"[D-8] 除外（不可視垂直線）: x={x:.3f}")

            return filtered_h_lines, filtered_v_lines

        except Exception as e:
            logger.error(f"[D-8] 不可視線フィルタエラー: {e}", exc_info=True)
            return h_lines, v_lines

    def _is_line_visible(
        self,
        line: Dict[str, Any],
        img_array: np.ndarray,
        page_width: int,
        page_height: int,
        is_horizontal: bool,
        sample_margin: int = 3  # サンプリングマージン（px）
    ) -> bool:
        """
        線が可視かどうかを判定

        Args:
            line: 線情報（正規化座標）
            img_array: レンダリング画像（グレースケール）
            page_width: ページ幅（px）
            page_height: ページ高さ（px）
            is_horizontal: 水平線か
            sample_margin: サンプリングマージン（px）

        Returns:
            True: 可視, False: 不可視
        """
        # 正規化座標をピクセル座標に変換
        x0 = int(line['x0'] * page_width)
        y0 = int(line['y0'] * page_height)
        x1 = int(line['x1'] * page_width)
        y1 = int(line['y1'] * page_height)

        # サンプリング領域を設定（線幅 + マージン）
        if is_horizontal:
            # 水平線: y 方向にマージンを取る
            y_min = max(0, y0 - sample_margin)
            y_max = min(img_array.shape[0], y0 + sample_margin)
            x_min = max(0, min(x0, x1))
            x_max = min(img_array.shape[1], max(x0, x1))
        else:
            # 垂直線: x 方向にマージンを取る
            x_min = max(0, x0 - sample_margin)
            x_max = min(img_array.shape[1], x0 + sample_margin)
            y_min = max(0, min(y0, y1))
            y_max = min(img_array.shape[0], max(y0, y1))

        # サンプリング領域が有効か確認
        if x_max <= x_min or y_max <= y_min:
            return False

        # サンプリング領域の画素を取得
        region = img_array[y_min:y_max, x_min:x_max]

        # 非白ピクセル（<240）の比率を計算
        non_white_pixels = np.sum(region < 240)
        total_pixels = region.size

        if total_pixels == 0:
            return False

        visibility_ratio = non_white_pixels / total_pixels

        # 可視性閾値と比較
        return visibility_ratio >= self.visibility_threshold
