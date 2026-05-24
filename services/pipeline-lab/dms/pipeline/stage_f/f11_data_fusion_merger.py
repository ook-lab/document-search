"""
F-1: Data Fusion Merger（ハイブリッド統合）

Stage B（デジタル抽出）と Stage E（視覚抽出）の結果を
ページ・座標順に統合する。

目的:
1. デジタルテキスト（Stage B）をベースに構築
2. 視覚抽出（Stage E）で補完
3. ページ・座標順に論理的な読み順を復元
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from dms.pipeline.stage_f.stage_d_line_digest import build_stage_d_line_digest
from dms.pipeline.stage_f.stage_d_cell_bundle import build_stage_d_cell_bundle

# E37 は F に渡してはいけない。この source を検出したら設計違反として拒否する。
_FORBIDDEN_SOURCES_IN_TABLE = frozenset({'b_embed'})


def _normalize_text_key(text: str) -> str:
    return re.sub(r'[\s\u3000]+', '', text or '')


def _e_text_covered_by_b(b_text: str, e_text: str) -> bool:
    """Stage B に同内容があるとき Stage E ブロックを捨てる（B の pdfplumber 座標を正とする）。"""
    bk = _normalize_text_key(b_text)
    ek = _normalize_text_key(e_text)
    if not ek:
        return True
    if bk == ek:
        return True
    if len(ek) >= 6 and ek in bk:
        return True
    if len(bk) >= 6 and bk in ek:
        return True
    return False


def _page_and_image_metrics(
    stage_d_result: Optional[Dict[str, Any]],
) -> Optional[Tuple[float, float, float, float]]:
    """(page_w_pt, page_h_pt, non_table_img_w_px, non_table_img_h_px) または None。"""
    if not stage_d_result:
        return None
    try:
        vr = (stage_d_result.get('debug') or {}).get('vector_lines') or {}
        ps = vr.get('page_size')
        if not isinstance(ps, (list, tuple)) or len(ps) < 2:
            return None
        page_w_pt = float(ps[0])
        page_h_pt = float(ps[1])
        if page_w_pt <= 0 or page_h_pt <= 0:
            return None
    except (TypeError, ValueError, IndexError):
        return None

    img_path = stage_d_result.get('non_table_image_path')
    if not img_path:
        return None
    try:
        from PIL import Image

        with Image.open(str(img_path)) as im:
            img_w_px, img_h_px = float(im.size[0]), float(im.size[1])
    except OSError:
        return None
    if img_w_px <= 0 or img_h_px <= 0:
        return None
    return page_w_pt, page_h_pt, img_w_px, img_h_px


def _bbox_looks_like_image_px(
    x0: float, y0: float, x1: float, y1: float,
    page_w_pt: float, page_h_pt: float,
    img_w_px: float, img_h_px: float,
) -> bool:
    """E-21 bbox が D-10 非表画像のピクセル座標かどうか。"""
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.6:
        return False
    if max(x1, y1) > max(page_w_pt, page_h_pt) * 1.08:
        return True
    if img_h_px > page_h_pt * 1.2 and max(y1, y0) <= img_h_px * 1.02:
        if max(x1, x0) <= img_w_px * 1.02:
            return True
    return False


def _collapse_oversized_bbox_for_sort(
    bbox: List[float],
    page_h_pt: float,
) -> List[float]:
    """E-21 の巨大 bbox は並び用に上端＋1行分に潰す（下端が表領域まで伸びる誤り対策）。"""
    if len(bbox) < 4 or page_h_pt <= 0:
        return bbox
    x0, y0, x1, y1 = (float(bbox[i]) for i in range(4))
    if y1 < y0:
        y0, y1 = y1, y0
    if (y1 - y0) / page_h_pt <= 0.18:
        return [x0, y0, x1, y1]
    line_h = max(page_h_pt * 0.02, 8.0)
    return [x0, y0, x1, min(y1, y0 + line_h)]


def _bbox_image_px_to_pdf_pt(
    bbox: List[float],
    page_w_pt: float,
    page_h_pt: float,
    img_w_px: float,
    img_h_px: float,
) -> List[float]:
    x0, y0, x1, y1 = (float(bbox[i]) for i in range(4))
    if not _bbox_looks_like_image_px(x0, y0, x1, y1, page_w_pt, page_h_pt, img_w_px, img_h_px):
        return [x0, y0, x1, y1]
    sx = page_w_pt / img_w_px
    sy = page_h_pt / img_h_px
    return [x0 * sx, y0 * sy, x1 * sx, y1 * sy]


class F11DataFusionMerger:
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
        session_id=None,
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
                'raw_integrated_text': str, # B+E 統合本文（ファイル外テキストは含めない）
                'events': list,             # イベント・予定
                'tasks': list,              # タスク
                'notices': list,            # 注意事項
                'tables': list,             # 表データ
                'metadata': dict            # メタデータ
            }
        """
        return self._merge_impl(
            stage_a_result, stage_b_result, stage_d_result, stage_e_result,
            e40_table_ssot, rawdata_record=rawdata_record, session_id=session_id
        )

    def _merge_impl(
        self,
        stage_a_result: Optional[Dict[str, Any]] = None,
        stage_b_result: Optional[Dict[str, Any]] = None,
        stage_d_result: Optional[Dict[str, Any]] = None,
        stage_e_result: Optional[Dict[str, Any]] = None,
        e40_table_ssot: Optional[List[Dict[str, Any]]] = None,
        rawdata_record: Optional[Dict[str, Any]] = None,
        session_id=None,
    ) -> Dict[str, Any]:
        """merge() の実装本体"""
        logger.info("[F11] データ統合開始")

        try:
            # ドキュメント情報を構築
            document_info = self._build_document_info(
                stage_a_result, stage_b_result, stage_d_result=stage_d_result
            )

            # 地の文ブロックのみ座標順に併存（読み順で地の文+表を1本にするのは F17）
            body_text, text_merge_stats, ordered_blocks = self._merge_text(
                stage_b_result,
                stage_e_result,
                rawdata_record,
                stage_d_result=stage_d_result,
            )

            # raw_integrated_text: B+E 由来の本文のみ。
            # ファイル外テキスト（display_* / raw メタのラベル付きブロック）との統合は
            # Stage F では行わない（検索データ準備で raw と統合する）。
            raw_text = body_text

            # non_table_text: 地の文のみの連結（F13 日付正規化用。読み順正本は F17 reading_stream）
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
            metadata['f1_text_merge'] = text_merge_stats
            metadata['f11_scope'] = 'prose_blocks_and_tables_coexist'
            stage_d_line_digest = build_stage_d_line_digest(stage_d_result)
            stage_d_cell_bundle = build_stage_d_cell_bundle(stage_d_result)

            logger.info("[F11] 統合完了:")
            logger.info(f"[F11]   ├─ イベント: {len(events)}件")
            logger.info(f"[F11]   ├─ タスク: {len(tasks)}件")
            logger.info(f"[F11]   ├─ 注意事項: {len(notices)}件")
            logger.info(f"[F11]   └─ 表: {len(tables)}個")

            # 統合テキスト全文をログ出力
            logger.info("[F11] " + "=" * 80)
            logger.info("[F11] 統合テキスト全文:")
            logger.info("[F11] " + "=" * 80)
            logger.info(f"[F11] {raw_text if raw_text else '（テキストなし）'}")
            logger.info("[F11] " + "=" * 80)

            # ファイル外テキストは G21 に注入しない（検索データ準備側で raw とまとめる）
            display_fields = None

            result = {
                'success': True,
                'document_info': document_info,
                'raw_integrated_text': raw_text,
                'non_table_text': non_table_text,
                'non_table_text_blocks': ordered_blocks,
                'events': events,
                'tasks': tasks,
                'notices': notices,
                'tables': tables,
                'metadata': metadata,
                'display_sent_at': rawdata_record.get('display_sent_at') if rawdata_record else None,
                'display_fields': display_fields,
                'stage_d_line_digest': stage_d_line_digest,
                'stage_d_cell_bundle': stage_d_cell_bundle,
            }

            # ★チェーン: 次のステージ（F-3）を呼び出す
            if self.next_stage:
                logger.info("[F11] → 次のステージ（F13）を呼び出します")
                return self.next_stage.normalize(
                    events=events,
                    year_context=document_info.get('year_context'),
                    merge_result=result,
                    session_id=session_id,
                )

            return result

        except Exception as e:
            logger.error(f"[F11] 統合エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _build_document_info(
        self,
        stage_a_result: Optional[Dict[str, Any]],
        stage_b_result: Optional[Dict[str, Any]],
        stage_d_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        ドキュメント情報を構築

        Args:
            stage_a_result: Stage A の結果
            stage_b_result: Stage B の結果
            stage_d_result: Stage D の結果（page_size → reading_stream 用）

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
            purged = stage_b_result.get('purged_pdf_path')
            if purged:
                info['purged_pdf_path'] = purged
            src = stage_b_result.get('source_pdf_path') or stage_b_result.get('input_pdf_path')
            if src:
                info['source_pdf_path'] = str(src)
            for k in ('page_width_pt', 'page_height_pt'):
                v = stage_b_result.get(k)
                if isinstance(v, (int, float)) and float(v) > 0:
                    info[k] = float(v)

        metrics = _page_and_image_metrics(stage_d_result)
        if metrics:
            info.setdefault('page_width_pt', metrics[0])
            info.setdefault('page_height_pt', metrics[1])

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
        stage_e_result: Optional[Dict[str, Any]],
        rawdata_record: Optional[Dict[str, Any]] = None,
        stage_d_result: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
        """
        テキストを座標順に統合

        Args:
            stage_b_result: Stage B の結果
            stage_e_result: Stage E の結果

        Returns:
            (座標順に統合されたテキスト, 検証用メタ, 座標順ブロックのリスト)
        """
        blocks = []  # [{page, y0, x0, text, source}]
        stage_b_count = 0
        stage_e_count = 0
        stage_e_skipped_dup = 0
        stage_b_branch: Optional[str] = None
        b_texts: List[str] = []
        img_metrics = _page_and_image_metrics(stage_d_result)

        # Stage B のブロックを収集
        if stage_b_result:
            logger.debug(f"[F11 DEBUG] stage_b_result keys: {list(stage_b_result.keys())}")

            # logical_blocks（PDF処理）- 座標あり
            if 'logical_blocks' in stage_b_result:
                stage_b_branch = 'logical_blocks'
                logger.info(f"[F11] Stage B データソース: logical_blocks（PDF処理）")
                for block in stage_b_result['logical_blocks']:
                    page = block.get('page', 0)
                    bbox = block.get('bbox', [])
                    text = block.get('text', '').strip()
                    if not text:
                        continue

                    # bbox から座標（pdfplumber: x0, top, x1, bottom）
                    y0 = bbox[1] if len(bbox) >= 4 else 0
                    x0 = bbox[0] if len(bbox) >= 4 else 0
                    y1 = bbox[3] if len(bbox) >= 4 else y0
                    x1 = bbox[2] if len(bbox) >= 4 else x0

                    b_block: Dict[str, Any] = {
                        'page': page,
                        'y0': y0,
                        'x0': x0,
                        'y1': y1,
                        'x1': x1,
                        'text': text,
                        'source': 'stage_b'
                    }
                    if block.get('text_lines'):
                        b_block['text_lines'] = block['text_lines']
                    blocks.append(b_block)
                    b_texts.append(text)
                    stage_b_count += 1

            # paragraphs（Native Word）- 座標なし、順番のみ
            elif 'paragraphs' in stage_b_result:
                stage_b_branch = 'paragraphs'
                logger.info(f"[F11] Stage B データソース: paragraphs（Native Word）")
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
                stage_b_branch = 'records'
                logger.info(f"[F11] Stage B データソース: records（B-42 多段組み）")
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
                    if any(_e_text_covered_by_b(bt, text) for bt in b_texts):
                        stage_e_skipped_dup += 1
                        continue
                    bbox = list(b.get('bbox') or [])
                    if len(bbox) >= 4 and img_metrics:
                        pw, ph, iw, ih = img_metrics
                        bbox = _bbox_image_px_to_pdf_pt(bbox, pw, ph, iw, ih)
                        bbox = _collapse_oversized_bbox_for_sort(bbox, ph)
                    # bbox = [x0, y0, x1, y1]（pdf pt または正規化）
                    x0 = float(bbox[0]) if len(bbox) >= 1 else 0.0
                    y0 = float(bbox[1]) if len(bbox) >= 2 else 0.0
                    x1 = float(bbox[2]) if len(bbox) >= 3 else x0
                    y1 = float(bbox[3]) if len(bbox) >= 4 else y0
                    blocks.append({
                        'page': page,
                        'y0': y0,
                        'x0': x0,
                        'y1': y1,
                        'x1': x1,
                        'text': text,
                        'source': 'stage_e',
                    })
                    stage_e_count += 1

        # 座標順にソート（page → y0 → x0）
        blocks.sort(key=lambda b: (b['page'], b['y0'], b['x0']))

        # 統合詳細をログ出力
        logger.info("[F11] " + "=" * 80)
        logger.info("[F11] Stage B と Stage E の座標順統合:")
        logger.info("[F11] " + "=" * 80)
        logger.info(f"[F11]   ├─ Stage B（デジタル抽出）: {stage_b_count} ブロック")
        logger.info(f"[F11]   ├─ Stage E（視覚抽出）: {stage_e_count} ブロック")
        if stage_e_skipped_dup:
            logger.info(f"[F11]   ├─ Stage E（B と重複して除外）: {stage_e_skipped_dup} ブロック")
        if img_metrics:
            logger.info(
                f"[F11]   ├─ E bbox→pdf pt 変換: page={img_metrics[0]:.0f}x{img_metrics[1]:.0f}pt "
                f"image={img_metrics[2]:.0f}x{img_metrics[3]:.0f}px"
            )
        logger.info(f"[F11]   └─ 合計: {len(blocks)} ブロック")

        # ソート後のブロック全件をログ出力
        logger.info("[F11] " + "-" * 80)
        logger.info("[F11] 座標順ソート結果 全ブロック:")
        logger.info("[F11] " + "-" * 80)
        for idx, block in enumerate(blocks, 1):
            logger.info(f"[F11]   Block #{idx} [page={block['page']}, y={block['y0']:.3f}, x={block['x0']:.3f}]")
            logger.info(f"[F11]     source={block['source']}: 「{block['text']}」")
        logger.info("[F11] " + "=" * 80)

        # B+E 由来の本文テキストのみを返す（display_* は display_fields として別経路で渡す）
        text_parts = [b['text'] for b in blocks]
        merged = '\n'.join(text_parts)
        ordered_blocks = []
        for b in blocks:
            ob: Dict[str, Any] = {
                'page': int(b['page']) if isinstance(b.get('page'), (int, float)) else 0,
                'y0': float(b['y0']) if isinstance(b.get('y0'), (int, float)) else 0.0,
                'x0': float(b['x0']) if isinstance(b.get('x0'), (int, float)) else 0.0,
                'text': b['text'],
                'source': b['source'],
            }
            if isinstance(b.get('y1'), (int, float)):
                ob['y1'] = float(b['y1'])
            if isinstance(b.get('x1'), (int, float)):
                ob['x1'] = float(b['x1'])
            ordered_blocks.append(ob)
        stats: Dict[str, Any] = {
            'stage_b_blocks': stage_b_count,
            'stage_e_blocks': stage_e_count,
            'stage_e_skipped_duplicate': stage_e_skipped_dup,
            'merged_blocks': len(blocks),
            'stage_b_branch': stage_b_branch,
            'non_table_text_chars': len(merged),
            'ordered_block_count': len(ordered_blocks),
        }
        logger.info(
            f"[F1-EXIT] merged_len={len(merged)} ordered_block_count={len(ordered_blocks)} "
            f"stage_b={stage_b_count} stage_e={stage_e_count}"
        )
        return merged, stats, ordered_blocks

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
                            f"[F11] 設計違反: E40 結果に禁止 source '{cell.get('source')}' が含まれています。"
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
                    f"[F11] E40表採用: {e40.get('canonical_id')} ({e40.get('origin_uid')})"
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
                    b_meta = dict(table.get('metadata') or {})
                    if table.get('page') is not None:
                        b_meta.setdefault('page', table.get('page'))
                    if table.get('index') is not None:
                        b_meta.setdefault('b_plumber_index', table.get('index'))
                    if table.get('bbox') is not None:
                        b_meta.setdefault('bbox', table.get('bbox'))
                    tables.append({
                        'table_id': table.get('table_id', f'B_T{idx + 1}'),
                        'origin_uid': table.get('origin_uid', f'B:P{table.get("page",0)}:T{idx}'),
                        'source': 'stage_b',
                        'page': table.get('page', 0),
                        'b_plumber_index': table.get('index'),
                        'bbox': table.get('bbox'),
                        'data': table.get('data', []),
                        'metadata': b_meta,
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
