"""
E-32: Image Candidate Builder（候補集合化）

E-30（Gemini: cells=bbox/row/col/text）と
E-31（セルOCR転送: cell_texts）を受け取り、

D10セル(row/col)ごとの「画像由来候補集合（image_candidates）」を生成する。

NOTE:
- 正本化（SSOT化）は E-40 のみ。ここでは候補集合化だけ。
- next_stage は持たない（E-40 は controller が明示実行）
- cell_texts は E-31 が struct_result.cells[].text から変換したもの
"""

from typing import Dict, Any, List
from loguru import logger


class E32TableCellMerger:
    """E-32: 候補集合化（image_candidates を返して終わり）"""

    def merge(
        self,
        struct_result: Dict[str, Any],
        ocr_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        E-30 の構造結果と E-31 の OCR 結果（Gemini テキスト変換済み）を
        (row,col) でマッチさせて image_candidates を生成する。

        Args:
            struct_result: E-30 の出力
                {cells: [{row, col, x0, y0, x1, y1, rowspan, colspan, text}], ...}
            ocr_result: E-31 の OCR 結果
                {cell_texts: [{row, col, text, confidence}], ocr_engine: str}

        Returns:
            {
                'success': bool,
                'route': 'E32_IMAGE_CANDIDATES',
                'table_id': str,
                'image_candidates': [
                    {
                        'cell_id': 'R0C1',
                        'row': int, 'col': int,
                        'items': [{'text': str, 'confidence': float, 'source': 'image_e31'}]
                    }
                ]
            }
        """
        if not struct_result or not struct_result.get('success'):
            logger.warning("[E-32] struct_result なし/失敗 → 空candidates")
            return {'success': False, 'route': 'E32_NO_STRUCT', 'image_candidates': []}

        cells = struct_result.get('cells', []) or []
        if not cells:
            logger.info("[E-32] struct_result.cells が空 → 空candidates")
            return {'success': False, 'route': 'E32_NO_CELLS', 'image_candidates': []}

        cell_texts = (ocr_result or {}).get('cell_texts', []) or []

        # (row,col) → cell_texts のインデックス化
        ct_index: Dict[tuple, List[Dict]] = {}
        for t in cell_texts:
            r = t.get('row')
            c = t.get('col')
            if r is None or c is None:
                continue
            key = (int(r), int(c))
            ct_index.setdefault(key, []).append(t)

        image_candidates = []
        non_empty = 0

        for cell in cells:
            r = int(cell.get('row', 0))
            c = int(cell.get('col', 0))
            cell_id = f"R{r}C{c}"

            items = []
            for t in ct_index.get((r, c), []):
                txt = (t.get('text') or '').strip()
                if not txt:
                    continue
                items.append({
                    'text': txt,
                    'confidence': float(t.get('confidence', 1.0)),
                    'source': 'image_e31',
                })

            if items:
                non_empty += 1

            image_candidates.append({
                'cell_id': cell_id,
                'row': r,
                'col': c,
                'items': items,
            })

        logger.info(
            f"[E-32] 候補集合化完了: "
            f"image_candidates={len(image_candidates)} non_empty={non_empty}"
        )
        for row_idx, cand in enumerate(image_candidates):
            logger.info(f"[E-32]   行{row_idx}: {cand}")

        return {
            'success': True,
            'route': 'E32_IMAGE_CANDIDATES',
            'table_id': struct_result.get('table_id'),
            'image_candidates': image_candidates,
        }
