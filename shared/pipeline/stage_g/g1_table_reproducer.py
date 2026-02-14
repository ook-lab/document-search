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

    def _extract_column_spans(self, columns: List) -> List[Dict[str, Any]]:
        """
        結合セル情報を抽出

        Args:
            columns: 元のカラムリスト（Noneは結合セルを表す）

        Returns:
            結合セル情報のリスト [{'text': '5A', 'start': 1, 'span': 7}, ...]
        """
        spans = []
        i = 0
        while i < len(columns):
            col = columns[i]
            if col is not None and col != '':
                # このセルから始まる結合セルの範囲を計算
                start = i
                span = 1
                # 次のセルがNoneの間はspanを拡大
                j = i + 1
                while j < len(columns) and (columns[j] is None or columns[j] == ''):
                    span += 1
                    j += 1

                if span > 1:
                    # 結合セル情報を保存
                    spans.append({
                        'text': str(col),
                        'start': start,
                        'span': span
                    })
                i = j
            else:
                i += 1

        return spans

    def _normalize_columns(self, columns: List) -> List[str]:
        """
        カラムヘッダーを正規化（null/空文字列/重複を一意の値に置き換え）

        Args:
            columns: 元のカラムリスト（Noneや重複を含む可能性がある）

        Returns:
            正規化されたカラムリスト（すべて一意の文字列）
        """
        normalized = []
        used_names = set()

        for i, col in enumerate(columns):
            # nullまたは空文字列の場合、列番号を使用
            if col is None or col == '':
                base_name = f'列{i + 1}'
            else:
                base_name = str(col)

            # 重複がある場合、サフィックスを追加
            name = base_name
            counter = 1
            while name in used_names:
                name = f'{base_name}_{counter}'
                counter += 1

            normalized.append(name)
            used_names.add(name)

        return normalized

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
            logger.warning(f"[G-1] data が空です")
            return None

        try:
            # data が dict の場合、Stage F が table 全体を 'data' に格納している
            # その中の 'data' キーに実際の配列の配列がある
            if isinstance(data, dict):
                if 'data' in data:
                    logger.debug(f"[G-1] data は dict → 内部の 'data' キーを取得")
                    data = data['data']
                else:
                    logger.warning(f"[G-1] data は dict だが 'data' キーがない: keys={list(data.keys())}")
                    return None

            # data が None または空の場合
            if data is None or (isinstance(data, list) and len(data) == 0):
                logger.warning(f"[G-1] data が None または空")
                return None

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
                # 元のカラムから結合セル情報を抽出
                original_columns = data[0] if len(data) > 0 else []
                column_spans = self._extract_column_spans(original_columns)

                # カラムヘッダーを正規化（nullや重複を一意の値に置き換え）
                columns = self._normalize_columns(original_columns)

                return {
                    'table_id': table.get('table_id', 'Unknown'),
                    'type': 'ui_table',
                    'source': table.get('source', 'unknown'),
                    'columns': columns,
                    'column_spans': column_spans,  # 結合セル情報
                    'data': data[1:] if len(data) > 1 else [],
                    'row_count': len(data) - 1,
                    'col_count': len(columns)
                }

            # data が配列だが、中身が dict でも list でもない場合
            elif isinstance(data, list) and len(data) > 0:
                # 文字列や数値の配列を1行の表として扱う
                logger.warning(f"[G-1] data[0] の型が想定外: {type(data[0])}, 1行として処理")
                return {
                    'table_id': table.get('table_id', 'Unknown'),
                    'type': 'ui_table',
                    'source': table.get('source', 'unknown'),
                    'columns': [f"Col{i+1}" for i in range(len(data))],
                    'data': [[str(item) for item in data]],
                    'row_count': 1,
                    'col_count': len(data)
                }

            else:
                # それでも処理できない場合、詳細をログに出力
                logger.warning(f"[G-1] 未知のdata形式: type={type(data)}, len={len(data) if isinstance(data, (list, dict)) else 'N/A'}")
                if isinstance(data, list) and len(data) > 0:
                    logger.warning(f"[G-1] data[0] type: {type(data[0])}, value: {data[0]}")
                return None

        except Exception as e:
            logger.error(f"[G-1] Stage B変換エラー: {e}", exc_info=True)
            return None
