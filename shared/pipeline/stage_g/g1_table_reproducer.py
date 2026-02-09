"""
G-1: High-Fidelity Table Reproduction（表の完全再現）

Markdown形式や文字列ベースの表を、
UIコンポーネントが即座に描画できる Pure JSON 構造に変換する。

目的:
1. Markdown表 → headers[] + rows[][] の配列化
2. 多段組み（B-42）の整頓
3. 結合セルのメタデータ化
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import re


class G1TableReproducer:
    """G-1: High-Fidelity Table Reproduction（表の完全再現）"""

    def __init__(self):
        """Table Reproducer 初期化"""
        pass

    def reproduce(
        self,
        tables: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        表データを Pure JSON 形式に変換

        Args:
            tables: Stage F の consolidated_tables

        Returns:
            {
                'success': bool,
                'ui_tables': list,  # UI用表データ
                'conversion_count': int
            }
        """
        if not tables:
            logger.info("[G-1] 変換する表がありません")
            return {
                'success': True,
                'ui_tables': [],
                'conversion_count': 0
            }

        logger.info(f"[G-1] 表の完全再現開始: {len(tables)}個")

        try:
            ui_tables = []

            for table in tables:
                table_id = table.get('table_id', 'Unknown')
                source = table.get('source', 'unknown')

                logger.info(f"[G-1] 処理中: {table_id} (ソース: {source})")

                # ソースごとに変換方法を変える
                if 'markdown' in table:
                    # Markdown形式の表を変換
                    ui_table = self._markdown_to_json(table)
                elif 'data' in table:
                    # Stage B の data を変換
                    ui_table = self._stage_b_to_json(table)
                else:
                    logger.warning(f"[G-1] {table_id}: 変換方法不明")
                    continue

                if ui_table:
                    ui_tables.append(ui_table)

            logger.info(f"[G-1] 変換完了: {len(ui_tables)}個")

            return {
                'success': True,
                'ui_tables': ui_tables,
                'conversion_count': len(ui_tables)
            }

        except Exception as e:
            logger.error(f"[G-1] 変換エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'ui_tables': [],
                'conversion_count': 0
            }

    def _markdown_to_json(
        self,
        table: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Markdown形式の表を Pure JSON に変換

        Args:
            table: 表データ（markdown含む）

        Returns:
            UI用表データ
        """
        markdown = table.get('markdown', '')
        if not markdown:
            return None

        try:
            lines = [line.strip() for line in markdown.split('\n') if line.strip()]

            if len(lines) < 2:
                logger.warning("[G-1] Markdown表が短すぎます")
                return None

            # ヘッダー行（1行目）
            header_line = lines[0]
            headers = self._parse_markdown_row(header_line)

            # データ行（3行目以降、2行目は区切り線）
            rows = []
            for line in lines[2:]:
                if not line.startswith('|'):
                    continue
                row = self._parse_markdown_row(line)
                rows.append(row)

            return {
                'table_id': table.get('table_id', 'Unknown'),
                'type': 'ui_table',
                'source': table.get('source', 'unknown'),
                'columns': headers,
                'data': rows,
                'row_count': len(rows),
                'col_count': len(headers)
            }

        except Exception as e:
            logger.warning(f"[G-1] Markdown変換エラー: {e}")
            return None

    def _parse_markdown_row(self, line: str) -> List[str]:
        """
        Markdown の行をパース

        Args:
            line: Markdown行（例: "| A | B | C |"）

        Returns:
            セルのリスト
        """
        # 先頭と末尾の | を除去
        line = line.strip()
        if line.startswith('|'):
            line = line[1:]
        if line.endswith('|'):
            line = line[:-1]

        # | で分割
        cells = [cell.strip() for cell in line.split('|')]

        return cells

    def _stage_b_to_json(
        self,
        table: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Stage B の data を Pure JSON に変換

        Args:
            table: 表データ（data含む）

        Returns:
            UI用表データ
        """
        data = table.get('data', [])
        if not data:
            return None

        try:
            # data が辞書のリストの場合
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                # カラム名を抽出
                headers = list(data[0].keys())

                # 行データを配列化
                rows = []
                for record in data:
                    row = [str(record.get(key, '')) for key in headers]
                    rows.append(row)

                return {
                    'table_id': table.get('table_id', 'Unknown'),
                    'type': 'ui_table',
                    'source': table.get('source', 'unknown'),
                    'columns': headers,
                    'data': rows,
                    'row_count': len(rows),
                    'col_count': len(headers)
                }

            # data が配列の配列の場合（そのまま）
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                return {
                    'table_id': table.get('table_id', 'Unknown'),
                    'type': 'ui_table',
                    'source': table.get('source', 'unknown'),
                    'columns': data[0] if len(data) > 0 else [],
                    'data': data[1:] if len(data) > 1 else [],
                    'row_count': len(data) - 1,
                    'col_count': len(data[0]) if len(data) > 0 else 0
                }

            else:
                logger.warning(f"[G-1] 未知のdata形式")
                return None

        except Exception as e:
            logger.warning(f"[G-1] Stage B変換エラー: {e}")
            return None
