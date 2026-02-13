"""
E-32: Table Cell Merger（構造 + セルOCR 合成）

E-30（構造）と E-31（セルOCR）の結果を合成して
最終的な表データを完成させる。

正しい依存順：
  E-30（構造：セルbbox確定）→ E-31（セルOCR）→ E-32（合成）

入力：
  struct_result: E-30 の出力 (cells, n_rows, n_cols, table_id)
  ocr_result: E-31 の出力 (cell_texts)

出力：
  grid: list[list[str]]  2次元テキストグリッド
  cells: 全セル（text 埋め済み）
  table_markdown: Markdown 形式の表
  route: "E30→E31→E32"
"""

from typing import Dict, Any, List
from loguru import logger


class E32TableCellMerger:
    """E-32: Table Cell Merger（構造とOCRを合成）"""

    def merge(
        self,
        struct_result: Dict[str, Any],
        ocr_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        E-30 の構造と E-31 の OCR テキストを合成する。

        Args:
            struct_result: E-30 の結果
                {cells: [{row,col,x0,y0,x1,y1,rowspan,colspan}],
                 n_rows, n_cols, table_id, tokens_used, ...}
            ocr_result: E-31 の結果
                {cell_texts: [{row, col, text, confidence}]}

        Returns:
            {
                'success': bool,
                'table_id': str,
                'n_rows': int,
                'n_cols': int,
                'cells': list,                # text 埋め済みセルリスト
                'grid': list[list[str]],      # 2次元グリッド
                'table_markdown': str,        # Markdown 形式
                'route': 'E30→E31→E32',
                'model_used': str,            # E-30 の model_used
                'tokens_used': int            # E-30 のトークン数（E-31はAPI課金なので0）
            }
        """
        table_id = struct_result.get('table_id', 'E30_Unknown')
        n_rows = struct_result.get('n_rows', 0)
        n_cols = struct_result.get('n_cols', 0)
        cells = struct_result.get('cells', [])

        logger.info(
            f"[E-32] 合成開始: table_id={table_id},"
            f" {n_rows}行 × {n_cols}列, セル={len(cells)}"
        )

        if not cells or n_rows == 0 or n_cols == 0:
            logger.warning("[E-32] 構造情報が空 → 空テーブルを返す")
            return self._empty_result(table_id, struct_result)

        # OCR テキストを (row, col) → text の辞書に変換
        text_map: Dict[tuple, str] = {}
        for ct in ocr_result.get('cell_texts', []):
            key = (ct.get('row', -1), ct.get('col', -1))
            text_map[key] = ct.get('text', '')

        # セルに text を埋める
        cells_with_text = []
        for cell in cells:
            row = cell.get('row', 0)
            col = cell.get('col', 0)
            text = text_map.get((row, col), '')
            merged_cell = {**cell, 'text': text}
            cells_with_text.append(merged_cell)

        # 2次元グリッドを構築
        grid = self._build_grid(cells_with_text, n_rows, n_cols)

        # Markdown 形式に変換
        table_markdown = self._grid_to_markdown(grid)

        logger.info(f"[E-32] 合成完了: route=E30→E31→E32")

        return {
            'success': True,
            'table_id': table_id,
            'n_rows': n_rows,
            'n_cols': n_cols,
            'cells': cells_with_text,
            'grid': grid,
            'table_markdown': table_markdown,
            'route': 'E30→E31→E32',
            'model_used': struct_result.get('model_used', ''),
            'tokens_used': struct_result.get('tokens_used', 0)
        }

    def _build_grid(
        self,
        cells: List[Dict[str, Any]],
        n_rows: int,
        n_cols: int
    ) -> List[List[str]]:
        """セルリストから2次元グリッド（テキスト）を構築"""
        # 空グリッドで初期化
        grid = [['' for _ in range(n_cols)] for _ in range(n_rows)]

        for cell in cells:
            row = cell.get('row', 0)
            col = cell.get('col', 0)
            text = cell.get('text', '')
            rowspan = cell.get('rowspan', 1)
            colspan = cell.get('colspan', 1)

            # グリッド範囲内かチェック
            if row >= n_rows or col >= n_cols:
                continue

            # rowspan/colspan が及ぶ全セルにテキストを配置
            for dr in range(rowspan):
                for dc in range(colspan):
                    r = row + dr
                    c = col + dc
                    if r < n_rows and c < n_cols:
                        grid[r][c] = text

        return grid

    def _grid_to_markdown(self, grid: List[List[str]]) -> str:
        """2次元グリッドを Markdown 表形式に変換"""
        if not grid:
            return ''

        lines = []
        for i, row in enumerate(grid):
            # セル内の改行を <br> に変換
            cells = [cell.replace('\n', '<br>').replace('|', '\\|') for cell in row]
            lines.append('| ' + ' | '.join(cells) + ' |')
            if i == 0:
                # ヘッダー行の後にセパレータ
                separator = '|' + '|'.join(['---'] * len(row)) + '|'
                lines.append(separator)

        return '\n'.join(lines)

    def _empty_result(
        self,
        table_id: str,
        struct_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """空テーブルを返す"""
        return {
            'success': True,
            'table_id': table_id,
            'n_rows': 0,
            'n_cols': 0,
            'cells': [],
            'grid': [],
            'table_markdown': '',
            'route': 'E30→E31→E32',
            'model_used': struct_result.get('model_used', ''),
            'tokens_used': struct_result.get('tokens_used', 0)
        }
