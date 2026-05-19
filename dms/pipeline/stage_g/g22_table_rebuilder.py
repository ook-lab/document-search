"""
F52: Table Rebuild（表の UI 用リビルドのみ）

入力は **F11 が `_merge_tables` で集めた結合前リスト** でも **F17 の `consolidated_tables`**
でもよい。いずれも同一の `ui_table` 形（headers / rows または columns / data）へリビルドする。

配置:
- **F52** … Stage F（G11 手前）で B/E 由来の表を UI 用グリッドに成型する。
- **F60** … F17 パススルー後の表リストを再リビルドする経路（`F60UIDeliveryController` 入口）。

対応入力（すべて同一の `ui_table` 形へリビルド）:

- **`cells`** … E-40 画像 SSOT（`stage_e40`）。行・列グリッドに展開する。
- **`data`** … Stage B 埋め込み表（dict 行 / list 行）。
- **`markdown`** … 旧 Stage E の Markdown 表。

※ OCR・セル確定は行わない（E-40 より前の責務）。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class G22TableRebuilder:
    """F52: F 由来の表行を UI 用 Pure JSON にリビルドする。"""

    def rebuild(
        self,
        tables: List[Dict[str, Any]],
        log_dir: Any = None,
    ) -> Dict[str, Any]:
        """
        Args:
            tables: F11 相当のプレフュージョン表リスト、または F17 の `consolidated_tables`
            log_dir: ログディレクトリ（任意）

        Returns:
            success, ui_tables, conversion_count
        """
        _log_dir = Path(log_dir) if log_dir else None
        _sink_id = None
        if _log_dir:
            _log_dir.mkdir(parents=True, exist_ok=True)
            _sink_id = logger.add(
                str(_log_dir / "g22_table_rebuilder.log"),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G22]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._rebuild_impl(tables)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _rebuild_impl(self, tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not tables:
            logger.info("[G22] 変換する表がありません")
            return {'success': True, 'ui_tables': [], 'conversion_count': 0}

        logger.info(f"[G22] 表リビルド開始: {len(tables)}個")

        try:
            ui_tables: List[Dict[str, Any]] = []

            for table in tables:
                table_id = table.get('table_id', 'Unknown')
                source = table.get('source', 'unknown')

                logger.info(f"[G22] 処理中: {table_id} (ソース: {source})")

                cells = table.get('cells')
                if isinstance(cells, list) and len(cells) > 0:
                    ui_table = self._cells_to_ui_table(table)
                elif 'markdown' in table:
                    ui_table = self._markdown_to_json(table)
                elif 'data' in table:
                    ui_table = self._stage_b_to_json(table)
                else:
                    logger.warning(
                        f"[G22] {table_id}: 変換方法不明（cells / markdown / data のいずれかが必要）"
                    )
                    continue

                if ui_table:
                    ui_tables.append(ui_table)

            logger.info(f"[G22] 変換完了: {len(ui_tables)}個")

            return {
                'success': True,
                'ui_tables': ui_tables,
                'conversion_count': len(ui_tables),
            }

        except Exception as e:
            logger.error(f"[G22] 変換エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'ui_tables': [],
                'conversion_count': 0,
            }

    def _cells_to_ui_table(self, table: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """E-40 `cells`（row,col,text）を行×列のグリッド（headers+rows）にリビルドする。"""
        cells = table.get('cells')
        if not isinstance(cells, list) or not cells:
            return None

        by_row: Dict[int, Dict[int, str]] = defaultdict(dict)
        for c in cells:
            if not isinstance(c, dict):
                continue
            try:
                r = int(c.get('row', 0))
                col = int(c.get('col', 0))
            except (TypeError, ValueError):
                continue
            by_row[r][col] = str(c.get('text') or '')

        if not by_row:
            logger.warning('[G22] cells に有効な (row,col) がありません')
            return None

        min_r = min(by_row.keys())
        max_r = max(by_row.keys())
        max_c = 0
        for r in by_row:
            if by_row[r]:
                max_c = max(max_c, max(by_row[r].keys()))

        rows: List[List[str]] = []
        for r in range(min_r, max_r + 1):
            rows.append([by_row[r].get(col, '') for col in range(0, max_c + 1)])

        logger.info('[G22] cells→grid 変換結果 全行:')
        for row_idx, row in enumerate(rows):
            logger.info(f'[G22]   行{row_idx}: {row}')

        return {
            'table_id': table.get('table_id', 'Unknown'),
            'type': 'ui_table',
            'source': table.get('source', 'stage_e40'),
            'headers': [],
            'rows': rows,
            'row_count': len(rows),
            'col_count': (max_c + 1) if rows else 0,
        }

    def _markdown_to_json(self, table: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        markdown = table.get('markdown', '')
        if not markdown:
            return None

        try:
            lines = [line.strip() for line in markdown.split('\n') if line.strip()]

            if len(lines) < 2:
                logger.warning("[G22] Markdown表が短すぎます")
                return None

            header_line = lines[0]
            headers = self._parse_markdown_row(header_line)

            rows = []
            for line in lines[2:]:
                if not line.startswith('|'):
                    continue
                row = self._parse_markdown_row(line)
                rows.append(row)

            logger.info("[G22] Markdown変換結果 全行:")
            for row_idx, row in enumerate(rows):
                logger.info(f"[G22]   行{row_idx}: {row}")

            return {
                'table_id': table.get('table_id', 'Unknown'),
                'type': 'ui_table',
                'source': table.get('source', 'unknown'),
                'columns': headers,
                'data': rows,
                'row_count': len(rows),
                'col_count': len(headers),
            }

        except Exception as e:
            logger.warning(f"[G22] Markdown変換エラー: {e}")
            return None

    def _parse_markdown_row(self, line: str) -> List[str]:
        line = line.strip()
        if line.startswith('|'):
            line = line[1:]
        if line.endswith('|'):
            line = line[:-1]
        return [cell.strip() for cell in line.split('|')]

    def _stage_b_to_json(self, table: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = table.get('data', [])
        if not data:
            logger.warning("[G22] data が空です")
            return None

        try:
            if isinstance(data, dict):
                if 'data' in data:
                    logger.debug("[G22] data は dict → 内部の 'data' キーを取得")
                    data = data['data']
                else:
                    logger.warning(f"[G22] data は dict だが 'data' キーがない: keys={list(data.keys())}")
                    return None

            if data is None or (isinstance(data, list) and len(data) == 0):
                logger.warning("[G22] data が None または空")
                return None

            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows = []
                for record in data:
                    row = [str(record.get(key, '')) for key in headers]
                    rows.append(row)

                logger.info("[G22] Stage B(dict)変換結果 全行:")
                for row_idx, row in enumerate(rows):
                    logger.info(f"[G22]   行{row_idx}: {row}")

                return {
                    'table_id': table.get('table_id', 'Unknown'),
                    'type': 'ui_table',
                    'source': table.get('source', 'unknown'),
                    'headers': headers,
                    'rows': rows,
                    'row_count': len(rows),
                    'col_count': len(headers),
                }

            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                existing_columns = table.get('columns') or table.get('headers')

                if existing_columns:
                    logger.info(f"[G22] columns が提供されています（{len(existing_columns)}列）→ data 全体をデータ行として扱う")
                    columns = existing_columns
                    column_spans: List[Any] = []
                    rows = data
                    row_count = len(data)
                else:
                    logger.info("[G22] columns が未提供 → headersは空で渡し、rowsにdata全体を渡す")
                    columns = []
                    column_spans = []
                    rows = data
                    row_count = len(data)

                logger.info("[G22] Stage B(array)変換結果 全行:")
                for row_idx, row in enumerate(rows):
                    logger.info(f"[G22]   行{row_idx}: {row}")

                out: Dict[str, Any] = {
                    'table_id': table.get('table_id', 'Unknown'),
                    'type': 'ui_table',
                    'source': table.get('source', 'unknown'),
                    'headers': columns,
                    'column_spans': column_spans,
                    'rows': rows,
                    'row_count': row_count,
                    'col_count': len(columns),
                }
                b_meta = dict(table.get('metadata') or {})
                if table.get('bbox') is not None:
                    b_meta.setdefault('bbox', table.get('bbox'))
                if table.get('page') is not None:
                    b_meta.setdefault('page', table.get('page'))
                if b_meta:
                    out['metadata'] = b_meta
                return out

            elif isinstance(data, list) and len(data) > 0:
                logger.warning(f"[G22] data[0] の型が想定外: {type(data[0])}, 1行として処理")
                rows = [[str(item) for item in data]]
                logger.info("[G22] Stage B(scalar)変換結果 全行:")
                for row_idx, row in enumerate(rows):
                    logger.info(f"[G22]   行{row_idx}: {row}")
                return {
                    'table_id': table.get('table_id', 'Unknown'),
                    'type': 'ui_table',
                    'source': table.get('source', 'unknown'),
                    'headers': [],
                    'rows': rows,
                    'row_count': 1,
                    'col_count': len(data),
                }

            else:
                logger.warning(
                    f"[G22] 未知のdata形式: type={type(data)}, len={len(data) if isinstance(data, (list, dict)) else ''}"
                )
                if isinstance(data, list) and len(data) > 0:
                    logger.warning(f"[G22] data[0] type: {type(data[0])}, value: {data[0]}")
                return None

        except Exception as e:
            logger.error(f"[G22] Stage B変換エラー: {e}", exc_info=True)
            return None
