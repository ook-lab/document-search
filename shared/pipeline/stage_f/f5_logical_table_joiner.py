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
        """Logical Table Joiner 初期化（チェーンの終端）"""
        pass

    def join(
        self,
        merge_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        チェーンパターン用: merge_resultから表を結合して統合結果を返す

        Args:
            merge_result: F-3からの統合結果

        Returns:
            F-stage全体の最終結果（F-1が期待する形式）
        """
        tables = merge_result.get('tables', [])

        join_result = self.join_tables(tables)
        consolidated_tables = join_result['joined_tables']
        join_count = join_result['join_count']
        logger.info(f"[F-5] 表結合: {join_count}個")

        # merge_resultを更新して返す
        merge_result['consolidated_tables'] = consolidated_tables

        # 最終結果を構築（F-1が期待する形式）
        result = {
            'success': True,
            'document_info': merge_result.get('document_info', {}),
            'normalized_events': merge_result.get('normalized_events', merge_result.get('events', [])),
            'tasks': merge_result.get('tasks', []),
            'notices': merge_result.get('notices', []),
            'consolidated_tables': consolidated_tables,
            'raw_integrated_text': merge_result.get('raw_integrated_text', ''),
            'non_table_text': merge_result.get('non_table_text', ''),
            'metadata': merge_result.get('metadata', {}),
            'display_fields': merge_result.get('display_fields'),
        }

        logger.info("=" * 60)
        logger.info("[F-5] Stage F チェーン完了")
        logger.info(f"  ├─ イベント: {len(result['normalized_events'])}件")
        logger.info(f"  ├─ タスク: {len(result['tasks'])}件")
        logger.info(f"  ├─ 注意事項: {len(result['notices'])}件")
        logger.info(f"  ├─ 表: {len(consolidated_tables)}個")
        logger.info(f"  └─ 総トークン: {result['metadata'].get('total_tokens', 0)}")
        logger.info("=" * 60)

        return result

    def join_tables(
        self,
        tables: List[Dict[str, Any]],
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

        logger.info("")
        logger.info("[F-5] ========== 表結合開始 ==========")
        logger.info(f"[F-5] 入力表数: {len(tables)}個")

        # 入力詳細ログ
        logger.info("")
        logger.info("[F-5] 入力表の詳細:")
        for idx, table in enumerate(tables, 1):
            table_id = table.get('table_id', 'Unknown')
            source = table.get('source', 'unknown')

            # データサイズの計算
            if 'data' in table:
                data = table.get('data', [])
                data_size = len(data) if isinstance(data, list) else 'N/A'
            elif 'markdown' in table:
                markdown = table.get('markdown', '')
                data_size = f"{len(markdown.split(chr(10)))}行"
            else:
                data_size = 'N/A'

            logger.info(f"  Table {idx}:")
            logger.info(f"    ├─ table_id: {table_id}")
            logger.info(f"    ├─ source: {source}")
            logger.info(f"    ├─ size: {data_size}")
            logger.info(f"    └─ keys: {list(table.keys())}")

        # 同一ソースの表をグループ化
        source_groups = self._group_by_source(tables)

        logger.info("")
        logger.info("[F-5] グループ化結果:")
        for source, table_list in source_groups.items():
            logger.info(f"  {source}: {len(table_list)}個の表")
            for idx, table in enumerate(table_list, 1):
                table_id = table.get('table_id', 'Unknown')
                logger.info(f"    └─ Table {idx}: {table_id}")

        # 各グループを結合
        joined_tables = []
        join_count = 0

        logger.info("")
        logger.info("[F-5] グループ別処理:")
        for source, table_list in source_groups.items():
            if len(table_list) == 1:
                # 単一の表はそのまま
                logger.info(f"  {source}: 1個のみ → そのまま通過")
                joined_tables.append(table_list[0])
            elif source in ('stage_b', 'stage_b_excel', 'stage_e'):
                # 既知ソース: カラム構造が同じ表のみ結合
                logger.info(f"  {source}: {len(table_list)}個の表を検証中...")

                # カラム構造の類似性チェック
                if self._tables_are_compatible(table_list):
                    logger.info(f"  {source}: カラム構造が一致 → 結合開始")

                    # 結合前の各表の詳細
                    total_rows_before = 0
                    for idx, table in enumerate(table_list, 1):
                        if 'data' in table:
                            data = table.get('data', [])
                            row_count = len(data) if isinstance(data, list) else 0
                            total_rows_before += row_count
                            logger.info(f"    ├─ Table {idx}: {row_count}行")
                        elif 'markdown' in table:
                            markdown = table.get('markdown', '')
                            line_count = len(markdown.split('\n'))
                            logger.info(f"    ├─ Table {idx}: {line_count}行")

                    joined = self._join_table_group(table_list)
                    if joined:
                        # 結合後の詳細
                        if 'data' in joined:
                            total_rows_after = len(joined.get('data', []))
                            logger.info(f"    └─ 結合後: {total_rows_after}行 (結合前合計: {total_rows_before}行)")

                        joined_tables.append(joined)
                        join_count += len(table_list) - 1
                    else:
                        logger.warning(f"  {source}: 結合失敗 → 個別に保持")
                        joined_tables.extend(table_list)
                else:
                    logger.info(f"  {source}: カラム構造が異なる → 個別に保持")
                    joined_tables.extend(table_list)
            else:
                # 未知ソース: 結合せず個別にそのまま通過
                logger.info(f"  {source}: 未知ソース {len(table_list)}個 → そのまま通過")
                joined_tables.extend(table_list)

        # 最終結果のサマリー
        logger.info("")
        logger.info("[F-5] 結合完了サマリー:")
        logger.info(f"  ├─ 結合前: {len(tables)}個")
        logger.info(f"  ├─ 結合後: {len(joined_tables)}個")
        logger.info(f"  └─ 結合数: {join_count}個")

        # 結合後の各表の詳細
        logger.info("")
        logger.info("[F-5] 結合後の表詳細:")
        total_rows = 0
        for idx, table in enumerate(joined_tables, 1):
            table_id = table.get('table_id', 'Unknown')
            if 'data' in table:
                data = table.get('data', [])
                row_count = len(data) if isinstance(data, list) else 0
                total_rows += row_count
                logger.info(f"  Table {idx} ({table_id}): {row_count}行")
            elif 'markdown' in table:
                markdown = table.get('markdown', '')
                line_count = len(markdown.split('\n'))
                logger.info(f"  Table {idx} ({table_id}): {line_count}行")
            else:
                logger.info(f"  Table {idx} ({table_id}): データなし")

        if total_rows > 0:
            logger.info(f"[F-5] 総行数: {total_rows}行")

        logger.info("=" * 50)

        return {
            'success': True,
            'joined_tables': joined_tables,
            'join_count': join_count
        }

    def _tables_are_compatible(
        self,
        table_list: List[Dict[str, Any]]
    ) -> bool:
        """
        表リストのカラム構造が一致するか確認

        Args:
            table_list: 表データリスト

        Returns:
            True: カラム構造が一致（結合可能）
            False: カラム構造が異なる（結合不可）
        """
        if not table_list or len(table_list) < 2:
            return True  # 1個以下なら結合不要

        logger.info("")
        logger.info("[F-5] _tables_are_compatible: カラム構造互換性チェック")
        logger.info(f"[F-5] チェック対象: {len(table_list)}個の表")

        # 各表のカラム数を取得
        column_counts = []
        for idx, table in enumerate(table_list):
            table_id = table.get('table_id', f'Unknown_{idx}')

            if 'data' in table:
                data = table.get('data', [])
                if isinstance(data, list) and len(data) > 0:
                    # 最初の行のキー数をカラム数とする
                    first_row = data[0]
                    if isinstance(first_row, dict):
                        col_count = len(first_row.keys())
                    elif isinstance(first_row, list):
                        # リスト形式の場合、要素数をカラム数とする
                        col_count = len(first_row)
                    else:
                        col_count = 0
                else:
                    col_count = 0
            elif 'markdown' in table:
                markdown = table.get('markdown', '')
                lines = markdown.strip().split('\n')
                if len(lines) > 0:
                    # ヘッダー行（1行目）のカラム数を取得
                    header_line = lines[0]
                    col_count = len(header_line.split('|')) - 2  # 前後の空文字を除外
                else:
                    col_count = 0
            else:
                col_count = 0

            column_counts.append(col_count)
            logger.info(f"  Table {idx+1} ({table_id}): {col_count}カラム")

        # カラム数の一致を確認
        if len(set(column_counts)) == 1:
            logger.info(f"[F-5] ✓ カラム構造一致: 全て{column_counts[0]}カラム → 結合可能")
            return True
        else:
            logger.warning(f"[F-5] ✗ カラム構造不一致: {column_counts} → 結合不可")
            return False

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
        logger.info("")
        logger.info("[F-5] _join_stage_b_tables: Stage B 表結合詳細")
        logger.info(f"[F-5] 入力表数: {len(table_list)}個")

        # 全ての data を連結
        all_data = []

        for idx, table in enumerate(table_list):
            data = table.get('data', [])
            table_id = table.get('table_id', f'Unknown_{idx}')

            logger.info(f"  Table {idx+1} ({table_id}):")
            logger.info(f"    ├─ data type: {type(data)}")
            logger.info(f"    ├─ data len: {len(data) if isinstance(data, list) else 'N/A'}")

            if isinstance(data, list):
                if data:
                    logger.info(f"    ├─ 全行データ:")
                    for sample_idx, row in enumerate(data, 1):
                        logger.info(f"    │   Row {sample_idx}: {row}")

                logger.info(f"    └─ all_data に {len(data)}行を extend")
                all_data.extend(data)
            else:
                logger.warning(f"    └─ [警告] data が list ではありません: {type(data)}")

        logger.info("")
        logger.info(f"[F-5] 結合完了: all_data = {len(all_data)}行")

        if all_data:
            logger.info(f"[F-5] 結合後の全行データ:")
            for idx, row in enumerate(all_data, 1):
                logger.info(f"  Row {idx}: {row}")

        joined_result = {
            'table_id': 'Joined_B_Tables',
            'source': 'stage_b',
            'data': all_data,
            'joined_from': [t.get('table_id', '') for t in table_list]
        }

        logger.info(f"[F-5] joined_from: {joined_result['joined_from']}")

        return joined_result

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
        logger.info("")
        logger.info("[F-5] _join_stage_e_tables: Stage E Markdown 表結合詳細")
        logger.info(f"[F-5] 入力表数: {len(table_list)}個")

        # Markdown を連結
        markdown_parts = []

        for idx, table in enumerate(table_list):
            markdown = table.get('markdown', '')
            table_id = table.get('table_id', f'Unknown_{idx}')

            logger.info(f"  Table {idx+1} ({table_id}):")
            logger.info(f"    ├─ markdown len: {len(markdown)}文字")

            if markdown:
                # ヘッダー行を除いて連結（2行目以降）
                lines = markdown.split('\n')
                logger.info(f"    ├─ 行数: {len(lines)}行")

                if len(markdown_parts) == 0:
                    # 最初の表はヘッダー含めて全て
                    logger.info(f"    └─ 全行を追加（ヘッダー含む）")
                    markdown_parts.append(markdown)
                else:
                    # 2つ目以降はデータ行のみ
                    if len(lines) > 2:
                        data_lines = '\n'.join(lines[2:])
                        logger.info(f"    └─ データ行のみ追加（{len(lines)-2}行）")
                        markdown_parts.append(data_lines)
                    else:
                        logger.info(f"    └─ [スキップ] ヘッダー行のみでデータ行なし")
            else:
                logger.info(f"    └─ [スキップ] markdown が空")

        joined_markdown = '\n'.join(markdown_parts)
        logger.info("")
        logger.info(f"[F-5] 結合完了: 最終 markdown = {len(joined_markdown)}文字, {len(joined_markdown.split(chr(10)))}行")

        if joined_markdown:
            logger.info(f"[F-5] 結合後の全行データ:")
            for idx, line in enumerate(joined_markdown.split('\n'), 1):
                logger.info(f"  Line {idx}: {line}")

        joined_result = {
            'table_id': 'Joined_E_Tables',
            'source': 'stage_e',
            'markdown': joined_markdown,
            'joined_from': [t.get('table_id', '') for t in table_list]
        }

        logger.info(f"[F-5] joined_from: {joined_result['joined_from']}")

        return joined_result
