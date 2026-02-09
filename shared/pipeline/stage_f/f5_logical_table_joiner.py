"""
F-5: Logical Table Joiner（論理表結合）

多段組みや複数ページにまたがる表を、
一つの論理的なデータセットに結合する。

目的:
1. 多段組み表（B-42）の結合
2. ページ跨ぎ表の統合
3. カラムヘッダーの整合性チェック
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger


class F5LogicalTableJoiner:
    """F-5: Logical Table Joiner（論理表結合）"""

    def __init__(self):
        """Logical Table Joiner 初期化"""
        pass

    def join_tables(
        self,
        tables: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        表データを結合

        Args:
            tables: 表データリスト

        Returns:
            {
                'success': bool,
                'joined_tables': list,  # 結合済み表
                'join_count': int       # 結合された表の数
            }
        """
        if not tables:
            logger.info("[F-5] 結合する表がありません")
            return {
                'success': True,
                'joined_tables': [],
                'join_count': 0
            }

        logger.info(f"[F-5] 表結合開始: {len(tables)}個")

        try:
            # 同一ソースの表をグループ化
            source_groups = self._group_by_source(tables)

            # 各グループを結合
            joined_tables = []
            join_count = 0

            for source, table_list in source_groups.items():
                if len(table_list) == 1:
                    # 単一の表はそのまま
                    joined_tables.append(table_list[0])
                else:
                    # 複数の表を結合
                    logger.info(f"[F-5] {source}: {len(table_list)}個の表を結合")
                    joined = self._join_table_group(table_list)
                    if joined:
                        joined_tables.append(joined)
                        join_count += len(table_list) - 1

            logger.info(f"[F-5] 結合完了:")
            logger.info(f"  ├─ 結合前: {len(tables)}個")
            logger.info(f"  ├─ 結合後: {len(joined_tables)}個")
            logger.info(f"  └─ 結合数: {join_count}個")

            return {
                'success': True,
                'joined_tables': joined_tables,
                'join_count': join_count
            }

        except Exception as e:
            logger.error(f"[F-5] 結合エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'joined_tables': tables,  # エラー時は元のまま
                'join_count': 0
            }

    def _group_by_source(
        self,
        tables: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        表をソース別にグループ化

        Args:
            tables: 表データリスト

        Returns:
            {source: [table1, table2, ...]}
        """
        groups = {}

        for table in tables:
            source = table.get('source', 'unknown')
            if source not in groups:
                groups[source] = []
            groups[source].append(table)

        return groups

    def _join_table_group(
        self,
        table_list: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        同一ソースの表グループを結合

        Args:
            table_list: 表リスト

        Returns:
            結合された表
        """
        if not table_list:
            return None

        # 最初の表をベースとする
        base_table = table_list[0].copy()
        source = base_table.get('source', 'unknown')

        # ソースごとに結合方法を変える
        if source == 'stage_b' and 'data' in base_table:
            # Stage B の structured_tables を結合
            return self._join_stage_b_tables(table_list)

        elif source == 'stage_e' and 'markdown' in base_table:
            # Stage E の Markdown 表を結合
            return self._join_stage_e_tables(table_list)

        else:
            # その他は単純に統合
            base_table['table_id'] = 'Joined_' + base_table.get('table_id', 'Unknown')
            base_table['joined_from'] = [t.get('table_id', '') for t in table_list]
            return base_table

    def _join_stage_b_tables(
        self,
        table_list: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Stage B の表を結合

        Args:
            table_list: 表リスト

        Returns:
            結合された表
        """
        # 全ての data を連結
        all_data = []

        for table in table_list:
            data = table.get('data', [])
            if isinstance(data, list):
                all_data.extend(data)

        return {
            'table_id': 'Joined_B_Tables',
            'source': 'stage_b',
            'data': all_data,
            'joined_from': [t.get('table_id', '') for t in table_list]
        }

    def _join_stage_e_tables(
        self,
        table_list: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Stage E の Markdown 表を結合

        Args:
            table_list: 表リスト

        Returns:
            結合された表
        """
        # Markdown を連結
        markdown_parts = []

        for table in table_list:
            markdown = table.get('markdown', '')
            if markdown:
                # ヘッダー行を除いて連結（2行目以降）
                lines = markdown.split('\n')
                if len(markdown_parts) == 0:
                    # 最初の表はヘッダー含めて全て
                    markdown_parts.append(markdown)
                else:
                    # 2つ目以降はデータ行のみ
                    if len(lines) > 2:
                        markdown_parts.append('\n'.join(lines[2:]))

        return {
            'table_id': 'Joined_E_Tables',
            'source': 'stage_e',
            'markdown': '\n'.join(markdown_parts),
            'joined_from': [t.get('table_id', '') for t in table_list]
        }
