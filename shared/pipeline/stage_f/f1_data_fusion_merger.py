"""
F-1: Data Fusion Merger（ハイブリッド統合）

Stage B（デジタル抽出）と Stage E（視覚抽出）の結果を
ページ・座標順に統合する。

目的:
1. デジタルテキスト（Stage B）をベースに構築
2. 視覚抽出（Stage E）で補完
3. ページ・座標順に論理的な読み順を復元
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger


class F1DataFusionMerger:
    """F-1: Data Fusion Merger（ハイブリッド統合）"""

    def __init__(self):
        """Data Fusion Merger 初期化"""
        pass

    def merge(
        self,
        stage_a_result: Optional[Dict[str, Any]] = None,
        stage_b_result: Optional[Dict[str, Any]] = None,
        stage_d_result: Optional[Dict[str, Any]] = None,
        stage_e_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        各ステージの結果を統合

        Args:
            stage_a_result: Stage A の結果（document_type, dimensions）
            stage_b_result: Stage B の結果（デジタル抽出）
            stage_d_result: Stage D の結果（視覚構造）
            stage_e_result: Stage E の結果（視覚抽出）

        Returns:
            {
                'success': bool,
                'document_info': dict,      # ドキュメント情報
                'raw_text': str,            # 統合されたテキスト
                'events': list,             # イベント・予定
                'tasks': list,              # タスク
                'notices': list,            # 注意事項
                'tables': list,             # 表データ
                'metadata': dict            # メタデータ
            }
        """
        logger.info("[F-1] データ統合開始")

        try:
            # ドキュメント情報を構築
            document_info = self._build_document_info(stage_a_result, stage_b_result)

            # テキストを統合
            raw_text = self._merge_text(stage_b_result, stage_e_result)

            # イベント・タスク・注意事項を抽出
            events, tasks, notices = self._extract_structured_content(stage_e_result)

            # 表データを統合
            tables = self._merge_tables(stage_b_result, stage_e_result)

            # メタデータを構築
            metadata = self._build_metadata(
                stage_a_result,
                stage_b_result,
                stage_d_result,
                stage_e_result
            )

            logger.info("[F-1] 統合完了:")
            logger.info(f"  ├─ イベント: {len(events)}件")
            logger.info(f"  ├─ タスク: {len(tasks)}件")
            logger.info(f"  ├─ 注意事項: {len(notices)}件")
            logger.info(f"  └─ 表: {len(tables)}個")

            # 統合テキスト全文をログ出力
            logger.info("=" * 80)
            logger.info("[F-1] 統合テキスト全文:")
            logger.info("=" * 80)
            logger.info(raw_text if raw_text else "（テキストなし）")
            logger.info("=" * 80)

            return {
                'success': True,
                'document_info': document_info,
                'raw_integrated_text': raw_text,
                'events': events,
                'tasks': tasks,
                'notices': notices,
                'tables': tables,
                'metadata': metadata
            }

        except Exception as e:
            logger.error(f"[F-1] 統合エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _build_document_info(
        self,
        stage_a_result: Optional[Dict[str, Any]],
        stage_b_result: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        ドキュメント情報を構築

        Args:
            stage_a_result: Stage A の結果
            stage_b_result: Stage B の結果

        Returns:
            ドキュメント情報
        """
        info = {
            'document_type': 'unknown',
            'year_context': None,
            'title': '',
            'source_file': ''
        }

        if stage_a_result:
            info['document_type'] = stage_a_result.get('document_type', 'unknown')

        if stage_b_result:
            info['processor_name'] = stage_b_result.get('processor_name', '')
            info['data_type'] = stage_b_result.get('data_type', '')

        return info

    def _merge_text(
        self,
        stage_b_result: Optional[Dict[str, Any]],
        stage_e_result: Optional[Dict[str, Any]]
    ) -> str:
        """
        テキストを統合

        Args:
            stage_b_result: Stage B の結果
            stage_e_result: Stage E の結果

        Returns:
            統合されたテキスト
        """
        text_parts = []

        # Stage B のテキスト（優先）
        if stage_b_result:
            logger.debug(f"[F-1 DEBUG] stage_b_result keys: {list(stage_b_result.keys())}")
            if 'logical_blocks' in stage_b_result:
                logger.debug(f"[F-1 DEBUG] logical_blocks count: {len(stage_b_result['logical_blocks'])}")

            # paragraphs（Native Word）
            if 'paragraphs' in stage_b_result:
                for para in stage_b_result['paragraphs']:
                    text_parts.append(para.get('text', ''))

            # logical_blocks（PDF処理）
            elif 'logical_blocks' in stage_b_result:
                for block in stage_b_result['logical_blocks']:
                    text_parts.append(block.get('text', ''))

            # records（B-42）
            elif 'records' in stage_b_result:
                for record in stage_b_result['records']:
                    # レコードを文字列化
                    record_str = ' | '.join([f"{k}: {v}" for k, v in record.items()])
                    text_parts.append(record_str)

        # Stage E のテキスト（補完）
        if stage_e_result:
            non_table = stage_e_result.get('non_table_content', {})
            if non_table.get('success'):
                raw_response = non_table.get('raw_response', '')
                if raw_response and raw_response not in '\n'.join(text_parts):
                    text_parts.append('\n--- 視覚抽出 ---\n')
                    text_parts.append(raw_response)

        return '\n\n'.join(text_parts)

    def _extract_structured_content(
        self,
        stage_e_result: Optional[Dict[str, Any]]
    ) -> tuple[List[Dict], List[Dict], List[Dict]]:
        """
        Stage E から構造化コンテンツを抽出

        Args:
            stage_e_result: Stage E の結果

        Returns:
            (events, tasks, notices)
        """
        events = []
        tasks = []
        notices = []

        if not stage_e_result:
            return events, tasks, notices

        non_table = stage_e_result.get('non_table_content', {})
        if not non_table.get('success'):
            return events, tasks, notices

        extracted = non_table.get('extracted_content', {})

        # 予定（schedule）
        if 'schedule' in extracted:
            events = extracted['schedule']

        # タスク（tasks）
        if 'tasks' in extracted:
            tasks = extracted['tasks']

        # 注意事項（notices）
        if 'notices' in extracted:
            notices = extracted['notices']

        return events, tasks, notices

    def _merge_tables(
        self,
        stage_b_result: Optional[Dict[str, Any]],
        stage_e_result: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        表データを統合

        Args:
            stage_b_result: Stage B の結果
            stage_e_result: Stage E の結果

        Returns:
            表データリスト
        """
        tables = []

        # Stage B の表（優先）
        if stage_b_result:
            # structured_tables（PDF/Native Word）
            if 'structured_tables' in stage_b_result:
                for idx, table in enumerate(stage_b_result['structured_tables']):
                    tables.append({
                        'table_id': f'B_T{idx + 1}',
                        'source': 'stage_b',
                        'data': table
                    })

            # sheets（Native Excel）
            elif 'sheets' in stage_b_result:
                for sheet in stage_b_result['sheets']:
                    tables.append({
                        'table_id': sheet.get('name', 'Sheet'),
                        'source': 'stage_b_excel',
                        'data': sheet
                    })

        # Stage E の表（補完）
        if stage_e_result:
            table_contents = stage_e_result.get('table_contents', [])
            for idx, table in enumerate(table_contents):
                if table.get('success'):
                    tid = table.get('table_id') or f"E_p000_t{idx:02d}"
                    tables.append({
                        'table_id': tid,
                        'source': 'stage_e',
                        'markdown': table.get('table_markdown', ''),
                        'json': table.get('table_json', {})
                    })

        return tables

    def _build_metadata(
        self,
        stage_a_result: Optional[Dict[str, Any]],
        stage_b_result: Optional[Dict[str, Any]],
        stage_d_result: Optional[Dict[str, Any]],
        stage_e_result: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        メタデータを構築

        Args:
            stage_a_result: Stage A の結果
            stage_b_result: Stage B の結果
            stage_d_result: Stage D の結果
            stage_e_result: Stage E の結果

        Returns:
            メタデータ
        """
        metadata = {
            'stages_processed': [],
            'total_tokens': 0,
            'models_used': []
        }

        if stage_a_result:
            metadata['stages_processed'].append('A')

        if stage_b_result:
            metadata['stages_processed'].append('B')

        if stage_d_result:
            metadata['stages_processed'].append('D')

        if stage_e_result:
            metadata['stages_processed'].append('E')
            e_metadata = stage_e_result.get('metadata', {})
            metadata['total_tokens'] = e_metadata.get('total_tokens', 0)
            metadata['models_used'] = e_metadata.get('models_used', [])

        return metadata

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'document_info': {},
            'raw_integrated_text': '',
            'events': [],
            'tasks': [],
            'notices': [],
            'tables': [],
            'metadata': {}
        }
