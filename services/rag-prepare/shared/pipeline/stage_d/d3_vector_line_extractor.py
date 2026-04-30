"""
D-3: Vector Line Extractor（ベクトル罫線抽出）

pdfplumber を使用して、PDF内のベクトルデータ（Line/Rect）として
存在する罫線を抽出する。

目的:
1. DTP由来のデジタルPDFから、表の罫線を正確に取得
2. 短すぎる装飾線や不要な線をフィルタリング
3. 正規化座標（0.0-1.0）で出力
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import pdfplumber


class D3VectorLineExtractor:
    """D-3: Vector Line Extractor（ベクトル罫線抽出）"""

    def __init__(
        self,
        min_line_length: float = 10.0,  # 最小線長（pt）
        max_line_thickness: float = 5.0  # 最大線幅（pt）
    ):
        """
        Vector Line Extractor 初期化

        Args:
            min_line_length: 罫線として認識する最小の長さ（pt）
            max_line_thickness: 罫線として認識する最大の太さ（pt）
        """
        self.min_line_length = min_line_length
        self.max_line_thickness = max_line_thickness

    def extract(
        self,
        file_path: Path,
        page_num: int = 0,
        known_table_regions: List[Dict] = None,
    ) -> Dict[str, Any]:
        """
        PDFページからベクトル罫線を抽出

        Args:
            file_path: PDFファイルパス
            page_num: ページ番号（0始まり）

        Returns:
            {
                'horizontal_lines': [...],  # 水平線リスト
                'vertical_lines': [...],    # 垂直線リスト
                'all_lines': [...],         # 全線リスト
                'page_size': (width, height)
            }
        """
        logger.info(f"[D-3] ベクトル罫線抽出開始: ページ {page_num + 1}")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                if page_num >= len(pdf.pages):
                    logger.error(f"[D-3] ページ番号が範囲外: {page_num}")
                    return self._empty_result()

                page = pdf.pages[page_num]
                page_width = page.width
                page_height = page.height

                # 線とrectを抽出
                lines = self._extract_lines(page, page_width, page_height)
                logger.info(f"[D-3] pdfplumber.lines から抽出: {len(lines)}本")
                if lines:
                    logger.info(f"[D-3] lines 全件:")
                    for i, line in enumerate(lines, 1):
                        logger.info(f"  {i}. x0={line['x0']:.3f}, y0={line['y0']:.3f}, x1={line['x1']:.3f}, y1={line['y1']:.3f}, length={line.get('length', 0):.1f}pt")

                rect_lines = self._extract_rect_edges(page, page_width, page_height)
                logger.info(f"[D-3] pdfplumber.rects から抽出: {len(rect_lines)}本")
                if rect_lines:
                    logger.info(f"[D-3] rect_lines 全件:")
                    for i, line in enumerate(rect_lines, 1):
                        logger.info(f"  {i}. x0={line['x0']:.3f}, y0={line['y0']:.3f}, x1={line['x1']:.3f}, y1={line['y1']:.3f}, length={line.get('length', 0):.1f}pt")

                # 統合してクレンジング
                before_merge = len(lines) + len(rect_lines)
                all_lines = self._merge_and_clean(lines + rect_lines)
                logger.info(f"[D-3] マージ・重複除去: {before_merge}本 → {len(all_lines)}本")

                # B 抽出済み表領域の線をフィルタリング（二重検出防止）
                if known_table_regions:
                    before = len(all_lines)
                    logger.debug(f"[D-3] B抽出済み表領域: {len(known_table_regions)}個")
                    for i, region in enumerate(known_table_regions, 1):
                        bbox = region.get('bbox', [])
                        logger.debug(f"  {i}. page={region.get('page')}, bbox={bbox}")

                    all_lines = self._filter_known_tables(
                        all_lines, known_table_regions, page_width, page_height, page_num
                    )
                    skipped = before - len(all_lines)
                    if skipped > 0:
                        logger.info(f"[D-3] B抽出済み表領域フィルタ: {skipped}本をスキップ（{before}本 → {len(all_lines)}本）")

                # 水平・垂直に分類
                horizontal_lines = [
                    line for line in all_lines
                    if self._is_horizontal(line)
                ]
                vertical_lines = [
                    line for line in all_lines
                    if self._is_vertical(line)
                ]

                logger.info(f"[D-3] 抽出完了:")
                logger.info(f"  ├─ 水平線: {len(horizontal_lines)}本")
                logger.info(f"  ├─ 垂直線: {len(vertical_lines)}本")
                logger.info(f"  └─ 合計: {len(all_lines)}本")

                return {
                    'horizontal_lines': horizontal_lines,
                    'vertical_lines': vertical_lines,
                    'all_lines': all_lines,
                    'page_size': (page_width, page_height)
                }

        except Exception as e:
            logger.error(f"[D-3] 抽出エラー: {e}", exc_info=True)
            return self._empty_result()

    def _extract_lines(
        self,
        page,
        page_width: float,
        page_height: float
    ) -> List[Dict[str, Any]]:
        """
        page.lines からベクトル線を抽出

        Args:
            page: pdfplumber page object
            page_width: ページ幅
            page_height: ページ高さ

        Returns:
            正規化座標の線リスト
        """
        lines = []
        raw_lines = page.lines if hasattr(page, 'lines') else []

        for line in raw_lines:
            # 線の長さを計算
            x0, y0 = line['x0'], line['top']
            x1, y1 = line['x1'], line['bottom']
            length = max(abs(x1 - x0), abs(y1 - y0))

            # --- ページ外枠（紙と同サイズ）の4辺lineをノイズとして除去 ---
            EDGE_TOL = 2.0          # pt
            SPAN_RATIO = 0.95       # 95%以上の長さなら「外枠候補」
            is_horizontal = abs(y1 - y0) < 1.0
            is_vertical   = abs(x1 - x0) < 1.0

            if is_horizontal and abs(y0 - 0) < EDGE_TOL and length >= page_width * SPAN_RATIO:
                logger.info("[D-3] ページ外枠上辺(line)をスキップ")
                continue
            if is_horizontal and abs(y0 - page_height) < EDGE_TOL and length >= page_width * SPAN_RATIO:
                logger.info("[D-3] ページ外枠下辺(line)をスキップ")
                continue
            if is_vertical and abs(x0 - 0) < EDGE_TOL and length >= page_height * SPAN_RATIO:
                logger.info("[D-3] ページ外枠左辺(line)をスキップ")
                continue
            if is_vertical and abs(x0 - page_width) < EDGE_TOL and length >= page_height * SPAN_RATIO:
                logger.info("[D-3] ページ外枠右辺(line)をスキップ")
                continue

            # フィルタリング
            if length < self.min_line_length:
                logger.debug(f"[D-3] 短すぎる線をスキップ: length={length:.1f}pt < min={self.min_line_length}pt")
                continue
            line_width = line.get('width', 0)
            if line_width > self.max_line_thickness:
                logger.debug(f"[D-3] 太すぎる線をスキップ: width={line_width:.1f}pt > max={self.max_line_thickness}pt")
                continue

            # 正規化座標に変換
            lines.append({
                'x0': x0 / page_width,
                'y0': y0 / page_height,
                'x1': x1 / page_width,
                'y1': y1 / page_height,
                'length': length,
                'type': 'line'
            })

        return lines

    def _extract_rect_edges(
        self,
        page,
        page_width: float,
        page_height: float
    ) -> List[Dict[str, Any]]:
        """
        page.rects から矩形の辺を線として抽出
        （ページ同サイズのフレーム rect はスキップ）

        Args:
            page: pdfplumber page object
            page_width: ページ幅
            page_height: ページ高さ

        Returns:
            正規化座標の線リスト
        """
        lines = []
        raw_rects = page.rects if hasattr(page, 'rects') else []

        # ページ同サイズフレーム判定の閾値
        EDGE_THRESHOLD = 2.0      # ページ端からの距離（pt）
        AREA_RATIO_DROP = 0.90    # 面積比の閾値

        for rect in raw_rects:
            x0, y0 = rect['x0'], rect['top']
            x1, y1 = rect['x1'], rect['bottom']

            # ページ同サイズのフレーム rect をスキップ（ノイズ除去）
            rect_width = x1 - x0
            rect_height = y1 - y0
            area_ratio = (rect_width * rect_height) / (page_width * page_height + 1e-9)

            # 4辺すべてがページ端に接近 AND 面積比が大きい → スキップ
            near_left = abs(x0 - 0) < EDGE_THRESHOLD
            near_right = abs(x1 - page_width) < EDGE_THRESHOLD
            near_top = abs(y0 - 0) < EDGE_THRESHOLD
            near_bottom = abs(y1 - page_height) < EDGE_THRESHOLD

            if (area_ratio >= AREA_RATIO_DROP and
                near_left and near_right and near_top and near_bottom):
                logger.info(
                    f"[D-3] ページ同サイズフレーム rect をスキップ: "
                    f"area_ratio={area_ratio:.3f}"
                )
                continue  # この rect は処理しない

            # 矩形の4辺を線として抽出
            edges = [
                # 上辺
                {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y0},
                # 下辺
                {'x0': x0, 'y0': y1, 'x1': x1, 'y1': y1},
                # 左辺
                {'x0': x0, 'y0': y0, 'x1': x0, 'y1': y1},
                # 右辺
                {'x0': x1, 'y0': y0, 'x1': x1, 'y1': y1},
            ]

            for edge in edges:
                length = max(
                    abs(edge['x1'] - edge['x0']),
                    abs(edge['y1'] - edge['y0'])
                )

                if length < self.min_line_length:
                    continue

                # 正規化座標に変換
                lines.append({
                    'x0': edge['x0'] / page_width,
                    'y0': edge['y0'] / page_height,
                    'x1': edge['x1'] / page_width,
                    'y1': edge['y1'] / page_height,
                    'length': length,
                    'type': 'rect_edge'
                })

        return lines

    def _merge_and_clean(
        self,
        lines: List[Dict[str, Any]],
        tolerance: float = 0.005  # 正規化座標での許容誤差
    ) -> List[Dict[str, Any]]:
        """
        重複する線を統合し、クレンジング

        Args:
            lines: 線リスト
            tolerance: 重複判定の許容誤差（正規化座標）

        Returns:
            クレンジング済み線リスト
        """
        if not lines:
            return []

        # 重複除去（簡易版）
        unique_lines = []
        for line in lines:
            is_duplicate = False
            for existing in unique_lines:
                if self._is_same_line(line, existing, tolerance):
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique_lines.append(line)

        return unique_lines

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

    def _is_horizontal(
        self,
        line: Dict[str, Any],
        angle_tolerance: float = 0.01
    ) -> bool:
        """
        線が水平かどうかを判定

        Args:
            line: 線
            angle_tolerance: 角度許容誤差

        Returns:
            水平ならTrue
        """
        return abs(line['y1'] - line['y0']) < angle_tolerance

    def _is_vertical(
        self,
        line: Dict[str, Any],
        angle_tolerance: float = 0.01
    ) -> bool:
        """
        線が垂直かどうかを判定

        Args:
            line: 線
            angle_tolerance: 角度許容誤差

        Returns:
            垂直ならTrue
        """
        return abs(line['x1'] - line['x0']) < angle_tolerance

    def _filter_known_tables(
        self,
        lines: List[Dict[str, Any]],
        known_table_regions: List[Dict],
        page_width: float,
        page_height: float,
        page_num: int
    ) -> List[Dict[str, Any]]:
        """
        B ステージで抽出済みの表領域に含まれる線をスキップ

        pdfplumber の table.bbox は (x0, top, x1, bottom) の絶対pt座標。
        D3 の lines は正規化座標（0.0-1.0）。
        同一ページの表領域のみ対象。

        Args:
            lines: 正規化座標の線リスト
            known_table_regions: B の structured_tables（page, bbox を含む）
            page_width: ページ幅（pt）
            page_height: ページ高さ（pt）
            page_num: 現在のページ番号

        Returns:
            フィルタリング済み線リスト
        """
        TOL = 0.005  # 正規化座標での境界許容誤差

        # 同一ページの表 bbox を正規化座標に変換
        norm_regions = []
        for table in known_table_regions:
            if table.get('page', 0) != page_num:
                continue
            bbox = table.get('bbox')
            if not bbox:
                continue
            x0, top, x1, bottom = bbox
            norm_regions.append((
                x0 / page_width - TOL,
                top / page_height - TOL,
                x1 / page_width + TOL,
                bottom / page_height + TOL
            ))

        if not norm_regions:
            return lines

        filtered = []
        for line in lines:
            in_known = False
            for (rx0, ry0, rx1, ry1) in norm_regions:
                # 線の両端点が領域内に収まっていればスキップ
                if (rx0 <= line['x0'] <= rx1 and rx0 <= line['x1'] <= rx1 and
                        ry0 <= line['y0'] <= ry1 and ry0 <= line['y1'] <= ry1):
                    in_known = True
                    break
            if not in_known:
                filtered.append(line)

        return filtered

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'horizontal_lines': [],
            'vertical_lines': [],
            'all_lines': [],
            'page_size': (0, 0)
        }
