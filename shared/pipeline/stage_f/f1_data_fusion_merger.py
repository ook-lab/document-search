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

    def __init__(self, next_stage=None):
        """
        Data Fusion Merger 初期化

        Args:
            next_stage: 次のステージ（F-3）のインスタンス
        """
        self.next_stage = next_stage

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

            # テキストを座標順に統合
            raw_text = self._merge_text(stage_b_result, stage_e_result)

            # 表を除いたテキスト（G-21用）
            # 座標順統合されたテキストを使用（B+Eの統合）
            non_table_text = raw_text

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

            result = {
                'success': True,
                'document_info': document_info,
                'raw_integrated_text': raw_text,
                'non_table_text': non_table_text,
                'events': events,
                'tasks': tasks,
                'notices': notices,
                'tables': tables,
                'metadata': metadata
            }

            # ★チェーン: 次のステージ（F-3）を呼び出す
            if self.next_stage:
                logger.info("[F-1] → 次のステージ（F-3）を呼び出します")
                return self.next_stage.normalize(
                    events=events,
                    year_context=document_info.get('year_context'),
                    merge_result=result
                )

            return result

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

    def _get_non_table_text(self, stage_e_result) -> str:
        """
        表セルを含まないテキストを取得（G-21用）。
        Stage E の non_table_content を使用する。
        B-90 purged PDF（表テキスト除去済み）から視覚抽出されたテキストなので
        G-11が処理済みの表セルテキストは含まれない。
        """
        if not stage_e_result:
            return ''
        non_table = stage_e_result.get('non_table_content', {})
        if not non_table.get('success'):
            return ''
        raw_response = non_table.get('raw_response', '')
        return raw_response.strip()

    def _merge_text(
        self,
        stage_b_result: Optional[Dict[str, Any]],
        stage_e_result: Optional[Dict[str, Any]]
    ) -> str:
        """
        テキストを座標順に統合

        Args:
            stage_b_result: Stage B の結果
            stage_e_result: Stage E の結果

        Returns:
            座標順に統合されたテキスト
        """
        blocks = []  # [{page, y0, x0, text, source}]
        stage_b_count = 0
        stage_e_count = 0

        # Stage B のブロックを収集
        if stage_b_result:
            logger.debug(f"[F-1 DEBUG] stage_b_result keys: {list(stage_b_result.keys())}")

            # logical_blocks（PDF処理）- 座標あり
            if 'logical_blocks' in stage_b_result:
                logger.info(f"[F-1] Stage B データソース: logical_blocks（PDF処理）")
                for block in stage_b_result['logical_blocks']:
                    page = block.get('page', 0)
                    bbox = block.get('bbox', [])
                    text = block.get('text', '').strip()
                    if not text:
                        continue

                    # bbox から y0, x0 を取得
                    y0 = bbox[1] if len(bbox) >= 4 else 0
                    x0 = bbox[0] if len(bbox) >= 4 else 0

                    blocks.append({
                        'page': page,
                        'y0': y0,
                        'x0': x0,
                        'text': text,
                        'source': 'stage_b'
                    })
                    stage_b_count += 1

            # paragraphs（Native Word）- 座標なし、順番のみ
            elif 'paragraphs' in stage_b_result:
                logger.info(f"[F-1] Stage B データソース: paragraphs（Native Word）")
                for idx, para in enumerate(stage_b_result['paragraphs']):
                    text = para.get('text', '').strip()
                    if not text:
                        continue
                    blocks.append({
                        'page': 0,
                        'y0': idx,  # 順番をy座標として使用
                        'x0': 0,
                        'text': text,
                        'source': 'stage_b'
                    })
                    stage_b_count += 1

            # records（B-42）- 座標なし
            elif 'records' in stage_b_result:
                logger.info(f"[F-1] Stage B データソース: records（B-42 多段組み）")
                for idx, record in enumerate(stage_b_result['records']):
                    record_str = ' | '.join([f"{k}: {v}" for k, v in record.items()])
                    blocks.append({
                        'page': 0,
                        'y0': idx,
                        'x0': 0,
                        'text': record_str,
                        'source': 'stage_b'
                    })
                    stage_b_count += 1

        # Stage E のブロックを収集
        if stage_e_result:
            non_table = stage_e_result.get('non_table_content', {})
            if non_table.get('success'):
                page = non_table.get('page', 0)
                raw_response = non_table.get('raw_response', '').strip()

                if raw_response:
                    # E-20の結果は1つのブロックとして扱う
                    # 座標は non_table_content に含まれる可能性がある
                    blocks.append({
                        'page': page,
                        'y0': 0,  # 非表領域は通常ページ上部
                        'x0': 0,
                        'text': raw_response,
                        'source': 'stage_e'
                    })
                    stage_e_count += 1

        # 座標順にソート（page → y0 → x0）
        blocks.sort(key=lambda b: (b['page'], b['y0'], b['x0']))

        # 統合詳細をログ出力
        logger.info("=" * 80)
        logger.info("[F-1] Stage B と Stage E の座標順統合:")
        logger.info("=" * 80)
        logger.info(f"  ├─ Stage B（デジタル抽出）: {stage_b_count} ブロック")
        logger.info(f"  ├─ Stage E（視覚抽出）: {stage_e_count} ブロック")
        logger.info(f"  └─ 合計: {len(blocks)} ブロック")

        # ソート後のブロックサンプルをログ出力
        logger.info("-" * 80)
        logger.info("[F-1] 座標順ソート結果（最初の5ブロック）:")
        logger.info("-" * 80)
        for idx, block in enumerate(blocks[:5], 1):
            preview = block['text'][:100].replace('\n', ' ') + ('...' if len(block['text']) > 100 else '')
            logger.info(f"  Block #{idx} [page={block['page']}, y={block['y0']:.3f}, x={block['x0']:.3f}]")
            logger.info(f"    source={block['source']}: 「{preview}」")
        logger.info("=" * 80)

        # テキストを結合
        text_parts = [b['text'] for b in blocks]
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
                    # ★修正: table オブジェクト全体ではなく、data フィールドを抽出
                    tables.append({
                        'table_id': f'B_T{idx + 1}',
                        'source': 'stage_b',
                        'data': table.get('data', [])  # table['data'] を取り出す
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
