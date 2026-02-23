"""
E-40: Image SSOT Consolidator（表テキストの正本化）

【役割】
  E-32（image_candidates：Gemini/Vision画像由来）と
  E-37（embedded_candidates：B埋め込み由来、監査用）を受け取り、
  D10 cell_map の各セルに「確定テキスト」を割り当てて SSOT を生成する。

【優先順位】
  1. image_e31（E-32 由来、Gemini画像OCR）を最優先
  2. b_embed（E-37 由来、Bの埋め込みテキスト）をフォールバック

【設計上の大原則】
  - E-40 は controller が唯一の実行者（next_stage での自動実行は禁止）
  - E-32 と E-37 の両方が空なら controller がスキップするため、E-40 は必ず候補あり前提で動く
  - F は table_contents（= E-40 の table_ssot リスト）だけを受け取る

【F との関係】
  - F1._merge_tables は E40 の table_ssot（cells with confirmed text）だけを受け取る
  - E37 監査結果（b_embed）は table_audit に格納され、F には渡さない
"""

from typing import Any, Dict, List, Optional
from loguru import logger


class E40ImageSsotConsolidator:
    """E-40: E32 + E37 の候補を統合して image SSOT を生成する"""

    def consolidate(
        self,
        d10_table: Dict[str, Any],
        e32_result: Optional[Dict[str, Any]] = None,
        e37_result: Optional[Dict[str, Any]] = None,
        separator: str = ' ',
    ) -> Dict[str, Any]:
        """
        D10 cell_map の各セルに image_candidates（E32）と
        embedded_candidates（E37）を優先順位でマージして SSOT を生成する。

        Args:
            d10_table: D10 tables[] の1要素
                {origin_uid, canonical_id, cell_map: [{row, col, bbox}]}
            e32_result: E32.merge() の結果（image_candidates を含む）
                None でも可（E37 だけでも動く）
            e37_result: E37.assign() の結果（embedded_candidates を含む）
                None でも可（E32 だけでも動く）
            separator: セル内テキスト結合の区切り文字

        Returns:
            {
                'success': bool,
                'route': 'E40_TABLE_SSOT',
                'table_ssot': {
                    'origin_uid': str,
                    'canonical_id': str,
                    'cells': [{row, col, bbox_norm, text: str, source: str}],
                    'stats': {total_cells, filled_cells, empty_cells}
                }
            }
        """
        origin_uid = d10_table.get('origin_uid', '')
        canonical_id = d10_table.get('canonical_id', d10_table.get('table_id', 'T?'))
        page_index = d10_table.get('page_index', 0)
        cell_map = d10_table.get('cell_map', [])

        logger.info("=" * 80)
        logger.info(f"[E-40] SSOT正本化: origin_uid={origin_uid} canonical_id={canonical_id}")
        logger.info(f"[E-40] D10 cell_map={len(cell_map)}セル")

        # E32 image_candidates を (row,col) でインデックス化
        e32_index: Dict[tuple, List[Dict]] = {}
        if e32_result and e32_result.get('success'):
            for cand in e32_result.get('image_candidates', []):
                rc = (cand.get('row'), cand.get('col'))
                e32_index[rc] = cand.get('items', [])
            logger.info(f"[E-40] E32 image_candidates: {len(e32_index)}セル（有効）")
        else:
            logger.info("[E-40] E32 なし/失敗 → b_embed のみで SSOT 化")

        # E37 embedded_candidates を (row,col) でインデックス化
        e37_index: Dict[tuple, List[Dict]] = {}
        if e37_result and e37_result.get('success'):
            for cell in e37_result.get('embedded_candidates', []):
                rc = (cell.get('row'), cell.get('col'))
                e37_index[rc] = cell.get('text_candidates', [])
            logger.info(f"[E-40] E37 embedded_candidates: {len(e37_index)}セル（有効）")
        else:
            logger.info("[E-40] E37 なし/失敗 → image のみで SSOT 化")

        # D10 cell_map の各セルを確定
        cells_out = []
        filled_count = 0
        empty_count = 0

        for d10_cell in cell_map:
            row = d10_cell.get('row')
            col = d10_cell.get('col')
            bbox_norm = d10_cell.get('bbox') or d10_cell.get('bbox_norm')
            rc = (row, col)

            # 優先: image_e31（E32）
            e32_items = e32_index.get(rc, [])
            if e32_items:
                texts = [item.get('text', '').strip() for item in e32_items if item.get('text', '').strip()]
                final_text = separator.join(texts)
                source = 'image_e31'
            else:
                # フォールバック: b_embed（E37）
                e37_items = e37_index.get(rc, [])
                if e37_items:
                    texts = [tc.get('text', '').strip() for tc in e37_items if tc.get('text', '').strip()]
                    final_text = separator.join(texts)
                    source = 'b_embed'
                else:
                    final_text = ''
                    source = 'none'

            if final_text:
                filled_count += 1
            else:
                empty_count += 1

            cells_out.append({
                'row': row,
                'col': col,
                'bbox_norm': bbox_norm,
                'text': final_text,
                'source': source,
            })

        if not filled_count and cells_out:
            logger.warning(
                f"[E-40] 全セルが空テキスト: {origin_uid}"
                " (E32/E37 両方空の場合 controller がスキップするはずなので確認)"
            )

        logger.info(
            f"[E-40] 正本化完了: filled={filled_count} / empty={empty_count}"
            f" / total={len(cells_out)}"
        )
        for row_idx, cell in enumerate(cells_out):
            logger.info(f"[E-40]   行{row_idx}: {cell}")

        table_ssot = {
            'origin_uid': origin_uid,
            'canonical_id': canonical_id,
            'page_index': page_index,
            'cells': cells_out,
            'route': 'E40_TABLE_SSOT',
            'stats': {
                'total_cells': len(cells_out),
                'filled_cells': filled_count,
                'empty_cells': empty_count,
            },
        }

        return {
            'success': True,
            'route': 'E40_TABLE_SSOT',
            'table_ssot': table_ssot,
        }
