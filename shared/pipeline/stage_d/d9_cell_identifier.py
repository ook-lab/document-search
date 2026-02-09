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
        grid_result: Dict[str, Any]
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

        logger.info(f"[D-9] グリッド:")
        logger.info(f"  ├─ 列数: {len(x_coords) - 1}")
        logger.info(f"  └─ 行数: {len(y_coords) - 1}")

        # セルを生成
        cells = self._generate_cells(x_coords, y_coords)

        # 結合セルを検出（オプション）
        # cells = self._detect_merged_cells(cells, h_lines, v_lines)

        logger.info(f"[D-9] セル特定完了: {len(cells)}個")

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
        v_lines: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        結合セルを検出

        Args:
            cells: セルリスト
            h_lines: 水平線リスト
            v_lines: 垂直線リスト

        Returns:
            結合セル処理済みセルリスト
        """
        # 簡易実装: セル間に罫線がない場合、結合セルと判定
        # 実装は複雑になるため、現時点ではスキップ
        # TODO: 必要に応じて実装

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
