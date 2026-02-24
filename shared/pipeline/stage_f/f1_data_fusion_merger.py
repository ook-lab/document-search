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
from typing import Any, Dict, List, Optional
from loguru import logger

# E37 は F に渡してはいけない。この source を検出したら設計違反として拒否する。
_FORBIDDEN_SOURCES_IN_TABLE = frozenset({'b_embed'})


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
        stage_e_result: Optional[Dict[str, Any]] = None,
        e40_table_ssot: Optional[List[Dict[str, Any]]] = None,
        log_dir=None,
        rawdata_record: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        各ステージの結果を統合

        Args:
            stage_a_result: Stage A の結果（document_type, dimensions）
            stage_b_result: Stage B の結果（デジタル抽出）
            stage_d_result: Stage D の結果（視覚構造）
            stage_e_result: Stage E の結果（視覚抽出）
            e40_table_ssot: E40 の結果リスト
            log_dir: ログディレクトリ（オプション）

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
        return self._merge_impl(
            stage_a_result, stage_b_result, stage_d_result, stage_e_result,
            e40_table_ssot, rawdata_record=rawdata_record
        )

    def _merge_impl(
        self,
        stage_a_result: Optional[Dict[str, Any]] = None,
        stage_b_result: Optional[Dict[str, Any]] = None,
        stage_d_result: Optional[Dict[str, Any]] = None,
        stage_e_result: Optional[Dict[str, Any]] = None,
        e40_table_ssot: Optional[List[Dict[str, Any]]] = None,
        rawdata_record: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """merge() の実装本体"""
        logger.info("[F-1] データ統合開始")

        try:
            # ドキュメント情報を構築
            document_info = self._build_document_info(stage_a_result, stage_b_result)

            # B+E 由来の本文テキストを座標順に統合
            body_text = self._merge_text(stage_b_result, stage_e_result, rawdata_record)

            # raw_integrated_text: display_* ヘッダー（ラベル付き）+ 本文（全情報を保持）
            if rawdata_record:
                header = self._build_display_header(rawdata_record)
                raw_text = (header + '\n\n' + body_text) if (header and body_text) else (header or body_text)
            else:
                raw_text = body_text

            # non_table_text: 本文のみ（G3 の段落分割に渡す。display_* を段落に混ぜない）
            non_table_text = body_text

            # イベント・タスク・注意事項を抽出
            events, tasks, notices = self._extract_structured_content(stage_e_result)

            # 表データを統合
            tables = self._merge_tables(stage_b_result, stage_e_result, e40_table_ssot)

            # メタデータを構築
            metadata = self._build_metadata(
                stage_a_result,
                stage_b_result,
                stage_d_result,
                stage_e_result
            )

            logger.info("[F-1] 統合完了:")
            logger.info(f"[F-1]   ├─ イベント: {len(events)}件")
            logger.info(f"[F-1]   ├─ タスク: {len(tasks)}件")
            logger.info(f"[F-1]   ├─ 注意事項: {len(notices)}件")
            logger.info(f"[F-1]   └─ 表: {len(tables)}個")

            # 統合テキスト全文をログ出力
            logger.info("[F-1] " + "=" * 80)
            logger.info("[F-1] 統合テキスト全文:")
            logger.info("[F-1] " + "=" * 80)
            logger.info(f"[F-1] {raw_text if raw_text else '（テキストなし）'}")
            logger.info("[F-1] " + "=" * 80)

            # display_* フィールドを個別ブロックとして G21 に渡すための辞書
            display_fields = None
            if rawdata_record:
                display_fields = {
                    '送信者':       rawdata_record.get('display_sender'),
                    'メール':       rawdata_record.get('display_sender_email'),
                    '送信日時':     rawdata_record.get('display_sent_at'),
                    '件名':         rawdata_record.get('display_subject'),
                    '本文':         rawdata_record.get('display_post_text'),
                }
                # 値が空のキーは除去
                display_fields = {k: v for k, v in display_fields.items() if v}
                if not display_fields:
                    display_fields = None

            result = {
                'success': True,
                'document_info': document_info,
                'raw_integrated_text': raw_text,
                'non_table_text': non_table_text,
                'events': events,
                'tasks': tasks,
                'notices': notices,
                'tables': tables,
                'metadata': metadata,
                'display_sent_at': rawdata_record.get('display_sent_at') if rawdata_record else None,
                'display_fields': display_fields,
            }

            # ★チェーン: 次のステージ（F-3）を呼び出す
            if self.next_stage:
                logger.info("[F-1] → 次のステージ（F-3）を呼び出します")
                return self.next_stage.normalize(
                    events=events,
                    year_context=document_info.get('year_context'),
                    merge_result=result,
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

    def _build_display_header(self, rawdata_record: Dict[str, Any]) -> str:
        """
        Rawdata_FILE_AND_MAIL の display_* フィールドをテキスト先頭に付与するヘッダーを生成。
        各フィールドは属性ラベル付きで出力する。値が空の場合はそのフィールドをスキップ。
        """
        fields = [
            ('送信者',     rawdata_record.get('display_sender')),
            ('メール',     rawdata_record.get('display_sender_email')),
            ('送信日時',   rawdata_record.get('display_sent_at')),
            ('件名',       rawdata_record.get('display_subject')),
            ('本文前テキスト', rawdata_record.get('display_post_text')),
        ]
        lines = [f"[{label}] {value}" for label, value in fields if value]
        if not lines:
            return ''
        return '\n'.join(lines)

    def _merge_text(
        self,
        stage_b_result: Optional[Dict[str, Any]],
        stage_e_result: Optional[Dict[str, Any]],
        rawdata_record: Optional[Dict[str, Any]] = None,
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

                # E-21 の座標付きブロックを使用（raw_response は JSON なので絶対に使わない）
                e21_blocks = non_table.get('blocks', [])
                for b in e21_blocks:
                    text = (b.get('text') or '').strip()
                    if not text:
                        continue
                    bbox = b.get('bbox', [])
                    # bbox = [x0, y0, x1, y1]
                    x0 = float(bbox[0]) if len(bbox) >= 1 else 0.0
                    y0 = float(bbox[1]) if len(bbox) >= 2 else 0.0
                    blocks.append({
                        'page': page,
                        'y0': y0,
                        'x0': x0,
                        'text': text,
                        'source': 'stage_e',
                    })
                    stage_e_count += 1

        # 座標順にソート（page → y0 → x0）
        blocks.sort(key=lambda b: (b['page'], b['y0'], b['x0']))

        # 統合詳細をログ出力
        logger.info("[F-1] " + "=" * 80)
        logger.info("[F-1] Stage B と Stage E の座標順統合:")
        logger.info("[F-1] " + "=" * 80)
        logger.info(f"[F-1]   ├─ Stage B（デジタル抽出）: {stage_b_count} ブロック")
        logger.info(f"[F-1]   ├─ Stage E（視覚抽出）: {stage_e_count} ブロック")
        logger.info(f"[F-1]   └─ 合計: {len(blocks)} ブロック")

        # ソート後のブロック全件をログ出力
        logger.info("[F-1] " + "-" * 80)
        logger.info("[F-1] 座標順ソート結果 全ブロック:")
        logger.info("[F-1] " + "-" * 80)
        for idx, block in enumerate(blocks, 1):
            logger.info(f"[F-1]   Block #{idx} [page={block['page']}, y={block['y0']:.3f}, x={block['x0']:.3f}]")
            logger.info(f"[F-1]     source={block['source']}: 「{block['text']}」")
        logger.info("[F-1] " + "=" * 80)

        # B+E 由来の本文テキストのみを返す（display_* は display_fields として別経路で渡す）
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
        stage_e_result: Optional[Dict[str, Any]],
        e40_table_ssot: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        表データを統合

        優先順位:
          1. E40（image SSOT）: D由来の画像表の確定テキスト
          2. Stage B: 埋め込みテキストの表（B表とD表は D1 dedup で排他保証済み）
          3. Stage E（旧互換）: 旧設計の table_contents（移行期のみ）

        Args:
            stage_b_result: Stage B の結果
            stage_e_result: Stage E の結果
            e40_table_ssot: E40 の結果リスト（D由来画像表の image SSOT）
                各要素: {origin_uid, canonical_id, cells: [{row, col, text, source:'image_ocr'}]}

        Returns:
            表データリスト

        Raises:
            ValueError: E40 結果に b_embed source が含まれている場合（設計違反）
        """
        tables = []

        # ─────────────────────────────────────────────────
        # 1) E40 (image SSOT): D由来の画像表。優先（B表とは排他）
        # ─────────────────────────────────────────────────
        if e40_table_ssot:
            for e40 in e40_table_ssot:
                # ガード: b_embed は設計違反（E37 の結果が混入したら即エラー）
                for cell in e40.get('cells', []):
                    if cell.get('source') in _FORBIDDEN_SOURCES_IN_TABLE:
                        raise ValueError(
                            f"[F-1] 設計違反: E40 結果に禁止 source '{cell.get('source')}' が含まれています。"
                            f" origin_uid={e40.get('origin_uid')} row={cell.get('row')} col={cell.get('col')}"
                            " → E37 の結果を F に渡してはいけません。"
                        )

                tables.append({
                    'table_id': e40.get('canonical_id', 'T?'),
                    'origin_uid': e40.get('origin_uid', ''),
                    'source': 'stage_e40',
                    'cells': e40.get('cells', []),
                    'provenance': {'table_text_source': 'E40_IMAGE_SSOT'},
                })
                logger.info(
                    f"[F-1] E40表採用: {e40.get('canonical_id')} ({e40.get('origin_uid')})"
                    f" cells={len(e40.get('cells', []))}"
                )

        # ─────────────────────────────────────────────────
        # 2) Stage B の表（埋め込みテキスト表）
        #    D1 dedup により E40 と重複しない保証あり
        # ─────────────────────────────────────────────────
        if stage_b_result:
            # structured_tables（PDF/Native Word）
            if 'structured_tables' in stage_b_result:
                for idx, table in enumerate(stage_b_result['structured_tables']):
                    tables.append({
                        'table_id': table.get('table_id', f'B_T{idx + 1}'),
                        'origin_uid': table.get('origin_uid', f'B:P{table.get("page",0)}:T{idx}'),
                        'source': 'stage_b',
                        'data': table.get('data', [])
                    })

            # sheets（Native Excel）
            elif 'sheets' in stage_b_result:
                for sheet in stage_b_result['sheets']:
                    tables.append({
                        'table_id': sheet.get('name', 'Sheet'),
                        'source': 'stage_b_excel',
                        'data': sheet
                    })

        # ─────────────────────────────────────────────────
        # 3) Stage E の表（旧設計互換。E40 移行完了後は削除予定）
        # ─────────────────────────────────────────────────
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
