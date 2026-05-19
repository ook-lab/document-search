"""
F54: Table Structurer（表の構造化）

G22 リビルド後の UI 用表データを、metadata 形式に構造化する（`stage_f` 実装）。
AI不要、純粋な変換処理。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class G24TableStructurer:
    """F54: Table Structurer（表の構造化）"""

    def __init__(self, document_id=None, next_stage=None):
        """
        Args:
            document_id: ドキュメントID（Supabase保存用）
            next_stage: 次のステージ（F55）のインスタンス
        """
        self.document_id = document_id
        self.next_stage = next_stage

    def structure(
        self,
        ui_tables: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        log_file=None,
        table_log_dir: Optional[Path] = None,
        chain_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        UI用表データを metadata 形式に構造化

        Args:
            ui_tables: UI 表データ
            year_context: 年度コンテキスト（下流へ引き継ぎ）
            log_file: ログファイルパス（オプション、[G24] のみ）
            table_log_dir: 表チェーン専用ログの親ディレクトリ（任意）
            chain_context: 表チェーン横断コンテキスト（例: stage_d_line_digest）

        Returns:
            {
                'success': bool,
                'structured_tables': list  # metadata 用の表データ
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G24]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._structure_impl(
                ui_tables, year_context, table_log_dir, log_file, chain_context=chain_context
            )
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _structure_impl(
        self,
        ui_tables: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        table_log_dir: Optional[Path] = None,
        log_file=None,
        *,
        chain_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """structure() の実装本体"""
        logger.info("")
        logger.info("[G24] ========== 表の構造化開始 ==========")
        logger.info(f"[G24] 入力表数: {len(ui_tables)}個")

        if ui_tables:
            logger.info("")
            logger.info("[G24] 入力表の詳細:")
            for idx, table in enumerate(ui_tables, 1):
                table_id = table.get('table_id', 'Unknown')
                columns = table.get('headers') or table.get('columns', [])
                data = table.get('rows') or table.get('data', [])
                source_page = table.get('source_page', '')

                logger.info(f"  Table {idx}:")
                logger.info(f"    ├─ table_id: {table_id}")
                logger.info(f"    ├─ source_page: {source_page}")
                logger.info(f"    ├─ columns: {len(columns)}列")
                logger.info(f"    │   {columns}")
                logger.info(f"    ├─ data: {len(data)}行")

                if data:
                    logger.info(f"    └─ 全行:")
                    for row_idx, row in enumerate(data, 1):
                        logger.info(f"        Row {row_idx}: {row}")
                else:
                    logger.info(f"    └─ データ行なし")

        try:
            structured_tables: List[Dict[str, Any]] = []

            logger.info("")
            logger.info("[G24] 変換処理:")
            for idx, table in enumerate(ui_tables, 1):
                table_id = table.get('table_id', 'Unknown')
                columns = table.get('headers') or table.get('columns', [])
                data = table.get('rows') or table.get('data', [])
                source_page = table.get('source_page')

                logger.info(f"  Table {idx} ({table_id}):")
                logger.info(f"    ├─ columns → headers: {len(columns)}列")
                logger.info(f"    ├─ data → rows: {len(data)}行")

                structured_table = {
                    'headers': columns,
                    'rows': data,
                    'table_id': table_id,
                    'source_page': source_page,
                }
                ui_meta = table.get('metadata')
                if isinstance(ui_meta, dict) and ui_meta:
                    structured_table['metadata'] = dict(ui_meta)

                structured_tables.append(structured_table)
                logger.info(f"    └─ 変換完了")

            logger.info("")
            logger.info(f"[G24] 構造化完了: {len(structured_tables)}表")

            if structured_tables:
                logger.info("")
                logger.info("[G24] ========== 構造化された表の詳細 ==========")
                total_rows = 0
                for idx, table in enumerate(structured_tables, 1):
                    table_id = table.get('table_id', 'Unknown')
                    headers = table.get('headers', [])
                    rows = table.get('rows', [])
                    source_page = table.get('source_page', '')
                    total_rows += len(rows)

                    logger.info(f"Table {idx} ({table_id}):")
                    logger.info(f"  ├─ source_page: {source_page}")
                    logger.info(f"  ├─ headers: {len(headers)}列")
                    logger.info(f"  │   {headers}")
                    logger.info(f"  ├─ rows: {len(rows)}行")

                    if rows:
                        logger.info(f"  └─ 全行:")
                        for row_idx, row in enumerate(rows, 1):
                            logger.info(f"      Row {row_idx}: {row}")
                    else:
                        logger.info(f"  └─ データ行なし")

                    logger.info("")

                logger.info(f"[G24] 総表数: {len(structured_tables)}表")
                logger.info(f"[G24] 総行数: {total_rows}行")
                logger.info("=" * 50)

            ctx = dict(chain_context or {})

            result = {
                'success': True,
                'structured_tables': structured_tables,
                'stage_d_line_digest': ctx.get('stage_d_line_digest'),
                'line_semantics_ai': ctx.get('line_semantics_ai'),
                'd_line_split_contract': ctx.get('d_line_split_contract'),
            }

            if self.next_stage:
                logger.info("[G24] → 次のステージ（G41）")
                _tld = table_log_dir
                if _tld is None and log_file:
                    _tld = Path(log_file).parent
                e13_result = self.next_stage.process(
                    result, year_context=year_context, table_log_dir=_tld
                )
                result['e13_result'] = e13_result
                return result

            return result

        except Exception as e:
            logger.error(f"[G24] 構造化エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'structured_tables': []
            }
