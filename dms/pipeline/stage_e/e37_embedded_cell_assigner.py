"""
E-37: Embedded Cell Assigner（B埋め込みテキスト→D10 cell_map 割当）

【役割】
  B（pdfplumber）の埋め込みテキスト（表の 2D data）を D10 cell_map のセルに割り当てる。
  「監査/比較用」の別チャンネルとして保存するだけで、F には渡さない。

【設計上の大原則】
  - この結果は絶対に F の入力に含めない（F1 の _merge_tables に渡さない）
  - F は表テキストを E-40（image SSOT）からのみ受け取る
  - E-37 はデバッグ・差分検知・ログ専用

【呼び出し方】
  controller から: assign(d10_table=..., stage_b_result=...)
  stage_b_result から structured_tables を取得して処理する。
"""

from typing import Any, Dict, List, Optional
from loguru import logger


class E37EmbeddedCellAssigner:
    """E-37: B埋め込みテキストを D10 cell_map に割り当てる（監査専用）"""

    def assign(
        self,
        d10_table: Dict[str, Any],
        stage_b_result: Optional[Dict[str, Any]] = None,
        page_width_pt: Optional[float] = None,
        page_height_pt: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        stage_b_result の structured_tables を D10 cell_map に割り当てる。

        Args:
            d10_table: D10 tables[] の1要素
                {origin_uid, canonical_id, cell_map: [{row, col, bbox}]}
            stage_b_result: Stage B の結果（structured_tables を含む）
            page_width_pt: ページ幅（pt）。B bbox 正規化用（オプション）
            page_height_pt: ページ高さ（pt）。B bbox 正規化用（オプション）

        Returns:
            {
                'success': bool,
                'origin_uid': str,
                'canonical_id': str,
                'cells': [{row, col, bbox_norm, text_candidates}],
                'embedded_candidates': [{row, col, bbox_norm, text_candidates}],  # text がある分のみ
                'route': 'E37_EMBEDDED_AUDIT',
                'note': '監査専用。F には渡さない。'
            }
        """
        d10_origin_uid = d10_table.get('origin_uid', '')
        canonical_id = d10_table.get('canonical_id', d10_table.get('table_id', 'T?'))
        page_index = d10_table.get('page_index', 0)
        cell_map = d10_table.get('cell_map', [])

        logger.info("=" * 80)
        logger.info(f"[E-37] B埋め込みテキスト割当（監査用）:")
        logger.info(f"  ├─ D10 origin_uid: {d10_origin_uid}")
        logger.info(f"  └─ D10 cell_map:  {len(cell_map)}セル")
        logger.warning("[E-37] ★この結果は監査専用です。F1 の _merge_tables には渡さないこと。")

        if stage_b_result is None:
            return self._empty_result(d10_origin_uid, canonical_id, page_index, cell_map, 'stage_b_result 未指定')

        b_tables = stage_b_result.get('structured_tables', [])
        if not b_tables:
            return self._empty_result(d10_origin_uid, canonical_id, page_index, cell_map, 'B structured_tables が空')

        if not cell_map:
            return self._empty_result(d10_origin_uid, canonical_id, page_index, cell_map, 'D10 cell_map が空')

        # B bbox 正規化係数（page_width_pt は stage_b_result から取得可能な場合もある）
        pw = page_width_pt or stage_b_result.get('page_width_pt')
        ph = page_height_pt or stage_b_result.get('page_height_pt')

        # D10 cell_map を (row,col) でインデックス化
        d10_index: Dict[tuple, Dict] = {}
        for d10_cell in cell_map:
            rc = (d10_cell.get('row'), d10_cell.get('col'))
            d10_index[rc] = {
                'row': d10_cell.get('row'),
                'col': d10_cell.get('col'),
                'bbox_norm': d10_cell.get('bbox') or d10_cell.get('bbox_norm'),
                'text_candidates': [],
            }

        # 全 B 表をループして D10 セルにテキストを収集
        for b_table in b_tables:
            b_data = b_table.get('data', [])  # List[List[str | None]]
            b_bbox_raw = b_table.get('bbox')
            b_origin_uid = b_table.get('origin_uid', f"B:P{b_table.get('page', 0)}:T{b_table.get('index', 0)}")

            b_bbox_norm = None
            if b_bbox_raw and pw and ph and pw > 0 and ph > 0:
                b_bbox_norm = [
                    b_bbox_raw[0] / pw,
                    b_bbox_raw[1] / ph,
                    b_bbox_raw[2] / pw,
                    b_bbox_raw[3] / ph,
                ]

            for r_idx, row in enumerate(b_data):
                if not isinstance(row, list):
                    continue
                for c_idx, cell_text in enumerate(row):
                    text = (cell_text or '').strip() if cell_text is not None else ''
                    if not text:
                        continue
                    rc = (r_idx, c_idx)
                    if rc in d10_index:
                        d10_index[rc]['text_candidates'].append({
                            'text': text,
                            'bbox_norm': b_bbox_norm,
                            'source': 'b_embed',
                            'granularity': 'cell',
                            'confidence': None,
                            'b_origin_uid': b_origin_uid,
                        })

        cells = list(d10_index.values())
        embedded_candidates = [c for c in cells if c.get('text_candidates')]
        empty_count = len(cells) - len(embedded_candidates)

        logger.info(
            f"[E-37] 割当完了（監査）: embedded={len(embedded_candidates)} / empty={empty_count}"
            f" / total={len(cells)}"
        )
        for row_idx, cand in enumerate(embedded_candidates):
            logger.info(f"[E-37]   行{row_idx}: {cand}")

        return {
            'success': True,
            'origin_uid': d10_origin_uid,
            'canonical_id': canonical_id,
            'page_index': page_index,
            'cells': cells,
            'embedded_candidates': embedded_candidates,  # text があるセルのみ
            'route': 'E37_EMBEDDED_AUDIT',
            'note': '監査専用。F1 の _merge_tables には渡さないこと。',
        }

    def _empty_result(
        self,
        origin_uid: str,
        canonical_id: str,
        page_index: int,
        cell_map: List[Dict],
        reason: str
    ) -> Dict[str, Any]:
        """空結果を返す（cell_map の構造は保持）"""
        cells = [
            {
                'row': c.get('row'),
                'col': c.get('col'),
                'bbox_norm': c.get('bbox') or c.get('bbox_norm'),
                'text_candidates': [],
            }
            for c in cell_map
        ]
        return {
            'success': False,
            'origin_uid': origin_uid,
            'canonical_id': canonical_id,
            'page_index': page_index,
            'cells': cells,
            'embedded_candidates': [],
            'route': 'E37_EMBEDDED_AUDIT',
            'note': '監査専用。F1 の _merge_tables には渡さないこと。',
            'error': reason,
        }
