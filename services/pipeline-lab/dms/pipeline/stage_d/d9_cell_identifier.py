"""
D-9: Cell Identifier（セル座標特定）

D-8で得られた交点情報から、個々のセル（矩形）を特定する。

目的:
1. 交点に囲まれた最小単位の矩形を全て特定
2. 各セルにIDと座標（bbox）を割り振る
3. 結合セル（Merged Cells）の検出と処理
"""

from typing import Dict, Any, List, Tuple, Optional
from loguru import logger
import numpy as np


class D9CellIdentifier:
    """D-9: Cell Identifier（セル座標特定）"""

    def __init__(
        self,
        merge_threshold: float = 0.005  # セル結合判定の閾値（正規化座標）
    ):
        """
        Cell Identifier 初期化

        Args:
            merge_threshold: セル結合判定の閾値（正規化座標）
        """
        self.merge_threshold = merge_threshold

    def identify(
        self,
        grid_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        格子解析結果からセル座標を特定

        Args:
            grid_result: D-8の解析結果

        Returns:
            {
                'cells': [
                    {
                        'cell_id': 'R1C1',
                        'bbox': [x0, y0, x1, y1],
                        'row': 1,
                        'col': 1
                    },
                    ...
                ],
                'grid_info': {
                    'rows': int,
                    'cols': int
                }
            }
        """
        logger.info("[D-9] セル特定開始")

        intersections = grid_result.get('intersections', [])
        h_lines = grid_result.get('unified_lines', {}).get('horizontal', [])
        v_lines = grid_result.get('unified_lines', {}).get('vertical', [])

        if len(intersections) < 4:
            logger.warning("[D-9] 交点が不足しています")
            return self._empty_result()

        # X座標とY座標を抽出してソート
        x_coords = sorted(list(set([p['x'] for p in intersections])))
        y_coords = sorted(list(set([p['y'] for p in intersections])))

        logger.info(f"[D-9] グリッド情報:")
        logger.info(f"  ├─ 列数（セル）: {len(x_coords) - 1}")
        logger.info(f"  ├─ 行数（セル）: {len(y_coords) - 1}")
        logger.info(f"  ├─ X座標数: {len(x_coords)}")
        logger.info(f"  └─ Y座標数: {len(y_coords)}")

        # 座標リストをログ出力（全件）
        logger.debug(f"[D-9] X座標リスト 全件: {[f'{x:.3f}' for x in x_coords]}")
        logger.debug(f"[D-9] Y座標リスト 全件: {[f'{y:.3f}' for y in y_coords]}")

        # セルを生成
        cells = self._generate_cells(x_coords, y_coords)

        cells = self._detect_merged_cells(cells, h_lines, v_lines)

        logger.info(f"[D-9] セル特定完了: {len(cells)}個")

        # セルの全件ログ
        if cells:
            logger.debug("[D-9] セル 全件:")
            for cell in cells:
                bbox = cell.get('bbox', [])
                logger.debug(
                    f"  {cell.get('cell_id')}: bbox=[{bbox[0]:.3f}, {bbox[1]:.3f}, "
                    f"{bbox[2]:.3f}, {bbox[3]:.3f}], row={cell.get('row')}, col={cell.get('col')}"
                )

        return {
            'cells': cells,
            'grid_info': {
                'rows': len(y_coords) - 1,
                'cols': len(x_coords) - 1,
                'x_coords': x_coords,
                'y_coords': y_coords
            }
        }

    def _generate_cells(
        self,
        x_coords: List[float],
        y_coords: List[float]
    ) -> List[Dict[str, Any]]:
        """
        格子座標からセルを生成

        Args:
            x_coords: X座標リスト（ソート済み）
            y_coords: Y座標リスト（ソート済み）

        Returns:
            セルリスト
        """
        cells = []
        cell_index = 0

        for row_idx in range(len(y_coords) - 1):
            for col_idx in range(len(x_coords) - 1):
                x0 = x_coords[col_idx]
                y0 = y_coords[row_idx]
                x1 = x_coords[col_idx + 1]
                y1 = y_coords[row_idx + 1]

                # セルID（R1C1形式）
                cell_id = f"R{row_idx + 1}C{col_idx + 1}"

                cells.append({
                    'cell_id': cell_id,
                    'bbox': [x0, y0, x1, y1],
                    'row': row_idx + 1,
                    'col': col_idx + 1,
                    'index': cell_index
                })

                cell_index += 1

        return cells

    def _detect_merged_cells(
        self,
        cells: List[Dict[str, Any]],
        h_lines: List[Dict[str, Any]],
        v_lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        セル境界に罫線が無い隣接ペアへ rowspan / colspan を付与（語彙不使用）。
        """
        if not cells or not h_lines:
            return cells

        by_rc: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for c in cells:
            by_rc[(int(c["row"]), int(c["col"]))] = c

        def _h_line_covers(line: Dict[str, Any], x0: float, x1: float, y_edge: float) -> bool:
            ly = (float(line.get("y0", 0)) + float(line.get("y1", 0))) / 2.0
            if abs(ly - y_edge) > self.merge_threshold:
                return False
            lx0, lx1 = float(line.get("x0", 0)), float(line.get("x1", 0))
            col_w = max(x1 - x0, 1e-6)
            overlap = max(0.0, min(x1, lx1) - max(x0, lx0))
            return overlap / col_w >= 0.35

        def _v_line_covers(line: Dict[str, Any], y0: float, y1: float, x_edge: float) -> bool:
            lx = (float(line.get("x0", 0)) + float(line.get("x1", 0))) / 2.0
            if abs(lx - x_edge) > self.merge_threshold:
                return False
            ly0, ly1 = float(line.get("y0", 0)), float(line.get("y1", 0))
            row_h = max(y1 - y0, 1e-6)
            overlap = max(0.0, min(y1, ly1) - max(y0, ly0))
            return overlap / row_h >= 0.35

        for c in cells:
            c.setdefault("rowspan", 1)
            c.setdefault("colspan", 1)

        max_row = max(int(c["row"]) for c in cells)
        max_col = max(int(c["col"]) for c in cells)

        for r in range(1, max_row):
            for col in range(1, max_col + 1):
                cur = by_rc.get((r, col))
                nxt = by_rc.get((r + 1, col))
                if not cur or not nxt:
                    continue
                bb = cur.get("bbox") or []
                if len(bb) < 4:
                    continue
                y_edge = float(bb[3])
                x0, x1 = float(bb[0]), float(bb[2])
                blocked = any(
                    _h_line_covers(ln, x0, x1, y_edge)
                    for ln in h_lines
                    if isinstance(ln, dict)
                )
                if not blocked:
                    cur["rowspan"] = int(cur.get("rowspan", 1)) + int(nxt.get("rowspan", 1))
                    nxt["merged_into"] = cur.get("cell_id")

        for r in range(1, max_row + 1):
            for col in range(1, max_col):
                cur = by_rc.get((r, col))
                nxt = by_rc.get((r, col + 1))
                if not cur or not nxt:
                    continue
                bb = cur.get("bbox") or []
                if len(bb) < 4:
                    continue
                x_edge = float(bb[2])
                y0, y1 = float(bb[1]), float(bb[3])
                blocked = any(
                    _v_line_covers(ln, y0, y1, x_edge)
                    for ln in v_lines
                    if isinstance(ln, dict)
                )
                if not blocked:
                    cur["colspan"] = int(cur.get("colspan", 1)) + int(nxt.get("colspan", 1))
                    nxt["merged_into"] = cur.get("cell_id")

        return cells

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'cells': [],
            'grid_info': {
                'rows': 0,
                'cols': 0,
                'x_coords': [],
                'y_coords': []
            }
        }
