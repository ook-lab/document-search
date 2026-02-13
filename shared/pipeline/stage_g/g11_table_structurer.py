"""
G-11: Table Structurer（表の構造化）

Stage G で生成された UI用表データを、metadata 形式に構造化する。
AI不要、純粋な変換処理。
"""

from typing import Dict, Any, List
from loguru import logger


class G11TableStructurer:
    """G-11: Table Structurer（表の構造化）"""

    def __init__(self):
        """Table Structurer 初期化"""
        pass

    def structure(
        self,
        ui_tables: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        UI用表データを metadata 形式に構造化

        Args:
            ui_tables: Stage G の UI用表データ

        Returns:
            {
                'success': bool,
                'structured_tables': list  # metadata 用の表データ
            }
        """
        logger.info("[G-11] 表の構造化開始")

        try:
            structured_tables = []

            for table in ui_tables:
                # UI用表データをそのまま metadata に含める
                # （既に headers/rows 形式になっている）
                structured_tables.append({
                    'headers': table.get('headers', []),
                    'rows': table.get('rows', []),
                    'table_id': table.get('table_id'),
                    'source_page': table.get('source_page')
                })

            logger.info(f"[G-11] 構造化完了: {len(structured_tables)}表")

            return {
                'success': True,
                'structured_tables': structured_tables
            }

        except Exception as e:
            logger.error(f"[G-11] 構造化エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'structured_tables': []
            }
