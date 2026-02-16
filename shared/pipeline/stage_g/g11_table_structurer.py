"""
G-11: Table Structurer（表の構造化）

Stage G で生成された UI用表データを、metadata 形式に構造化する。
AI不要、純粋な変換処理。
"""

from typing import Dict, Any, List, Optional
from loguru import logger
from shared.common.database.client import DatabaseClient


class G11TableStructurer:
    """G-11: Table Structurer（表の構造化）"""

    def __init__(self, document_id=None, next_stage=None):
        """
        Table Structurer 初期化

        Args:
            document_id: ドキュメントID（Supabase保存用）
            next_stage: 次のステージ（G-12）のインスタンス
        """
        self.document_id = document_id
        self.next_stage = next_stage

    def structure(
        self,
        ui_tables: List[Dict[str, Any]],
        year_context: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        UI用表データを metadata 形式に構造化

        Args:
            ui_tables: Stage G の UI用表データ
            year_context: 年度コンテキスト（G-12に渡す）

        Returns:
            {
                'success': bool,
                'structured_tables': list  # metadata 用の表データ
            }
        """
        logger.info("")
        logger.info("[G-11] ========== 表の構造化開始 ==========")
        logger.info(f"[G-11] 入力表数: {len(ui_tables)}個")

        # 入力詳細ログ
        if ui_tables:
            logger.info("")
            logger.info("[G-11] 入力表の詳細:")
            for idx, table in enumerate(ui_tables, 1):
                table_id = table.get('table_id', 'Unknown')
                # ★ headers/rows を優先（G-1 Reproducer の出力形式）
                columns = table.get('headers') or table.get('columns', [])
                data = table.get('rows') or table.get('data', [])
                source_page = table.get('source_page', 'N/A')

                logger.info(f"  Table {idx}:")
                logger.info(f"    ├─ table_id: {table_id}")
                logger.info(f"    ├─ source_page: {source_page}")
                logger.info(f"    ├─ columns: {len(columns)}列")
                logger.info(f"    │   {columns}")
                logger.info(f"    ├─ data: {len(data)}行")

                # サンプル行を表示
                if data:
                    logger.info(f"    └─ サンプル行（先頭3行）:")
                    for row_idx, row in enumerate(data[:3], 1):
                        logger.info(f"        Row {row_idx}: {row}")
                else:
                    logger.info(f"    └─ データ行なし")

        try:
            structured_tables = []

            logger.info("")
            logger.info("[G-11] 変換処理:")
            for idx, table in enumerate(ui_tables, 1):
                table_id = table.get('table_id', 'Unknown')
                # ★ headers/rows を優先（G-1 Reproducer の出力形式）
                columns = table.get('headers') or table.get('columns', [])
                data = table.get('rows') or table.get('data', [])
                source_page = table.get('source_page')

                logger.info(f"  Table {idx} ({table_id}):")
                logger.info(f"    ├─ columns → headers: {len(columns)}列")
                logger.info(f"    ├─ data → rows: {len(data)}行")

                # UI用表データ（columns/data形式）を metadata 形式（headers/rows形式）に変換
                structured_table = {
                    'headers': columns,
                    'rows': data,
                    'table_id': table_id,
                    'source_page': source_page
                }

                structured_tables.append(structured_table)
                logger.info(f"    └─ 変換完了")

            logger.info("")
            logger.info(f"[G-11] 構造化完了: {len(structured_tables)}表")

            # 出力詳細ログ
            if structured_tables:
                logger.info("")
                logger.info("[G-11] ========== 構造化された表の詳細 ==========")
                total_rows = 0
                for idx, table in enumerate(structured_tables, 1):
                    table_id = table.get('table_id', 'Unknown')
                    headers = table.get('headers', [])
                    rows = table.get('rows', [])
                    source_page = table.get('source_page', 'N/A')
                    total_rows += len(rows)

                    logger.info(f"Table {idx} ({table_id}):")
                    logger.info(f"  ├─ source_page: {source_page}")
                    logger.info(f"  ├─ headers: {len(headers)}列")
                    logger.info(f"  │   {headers}")
                    logger.info(f"  ├─ rows: {len(rows)}行")

                    # サンプル行
                    if rows:
                        logger.info(f"  └─ サンプル行（先頭3行、末尾1行）:")
                        for row_idx, row in enumerate(rows[:3], 1):
                            logger.info(f"      Row {row_idx}: {row}")
                        if len(rows) > 3:
                            logger.info(f"      ... ({len(rows)-4}行省略)")
                            logger.info(f"      Row {len(rows)}: {rows[-1]}")
                    else:
                        logger.info(f"  └─ データ行なし")

                    logger.info("")

                logger.info(f"[G-11] 総表数: {len(structured_tables)}表")
                logger.info(f"[G-11] 総行数: {total_rows}行")
                logger.info("=" * 50)

            result = {
                'success': True,
                'structured_tables': structured_tables
            }

            # Supabaseに保存
            if self.document_id:
                try:
                    db = DatabaseClient(use_service_role=True)
                    db.client.table('Rawdata_FILE_AND_MAIL').update({
                        'g11_structured_tables': structured_tables
                    }).eq('id', self.document_id).execute()
                    logger.info(f"[G-11] ✓ g11_structured_tables を Supabase に保存: {len(structured_tables)}表")
                except Exception as e:
                    logger.error(f"[G-11] Supabase保存エラー: {e}")

            # ★チェーン: 次のステージ（G-12）を呼び出す
            if self.next_stage:
                logger.info("[G-11] → 次のステージ（G-12）を呼び出します")
                g12_result = self.next_stage.process(structured_tables, year_context=year_context)
                # G-11の結果も返す
                result['g12_result'] = g12_result
                return result

            return result

        except Exception as e:
            logger.error(f"[G-11] 構造化エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'structured_tables': []
            }
