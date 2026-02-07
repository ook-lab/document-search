"""
G4: Assemble（物理正本確定）

【Ver 10.8】I/O契約
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入力:
  - scrubbed_core: G3の出力
  - logical_structure: F2の出力（row_boundaries, col_boundaries, panels）

出力（中間）: assembled_payload
  - tagged_texts: 座標ロック済み＆読み順ソート済み正本
  - anchors: アンカーパケット（TBL_xxx, TXT_xxx）
  - tables: 物理ロック済みテーブル
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ルール:
- H1にあった「全軸座標ロック」をここで完遂する
- G3の洗い替え済みテキストを、F2の幾何境界に対し数学的に再配置
- 値は不変（read-only）、配置のみ確定
- 【Ver 10.8追加】全要素を物理座標で読み順ソート（上→下、左→右）
"""

import time
from typing import Dict, Any, List, Optional
from loguru import logger


class G4Assemble:
    """G4: Assemble - 物理正本確定（Ver 10.8: 座標ロック＋読み順ソート版）"""

    # 同一行とみなすY座標の許容差（ピクセル）
    ROW_TOLERANCE = 10.0

    def __init__(self):
        pass

    def assemble(
        self,
        scrubbed_core: Dict[str, Any],
        logical_structure: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        物理正本を確定する

        G3の洗い替え済みテキストを、F2の物理境界に基づき
        数学的に再配置（座標ロック）してから組み立てる。

        Args:
            scrubbed_core: G3の出力（tagged_texts, x_headers, y_headers等）
            logical_structure: F2の出力（row_boundaries, col_boundaries, panels等）
            metadata: 追加メタ情報

        Returns:
            assembled_payload（中間成果物、G5へ渡す）
        """
        g4_start = time.time()

        tagged_texts = scrubbed_core.get('tagged_texts', [])
        x_headers = scrubbed_core.get('x_headers', [])
        y_headers = scrubbed_core.get('y_headers', [])

        logger.info(f"[G4] 物理正本確定開始: texts={len(tagged_texts)}")

        # ============================================
        # 【核心】物理座標ロック（Ver 10.7）
        # F2の幾何境界を用いてトークンを数学的に再配置する
        # ============================================
        locked_table = None
        if logical_structure:
            locked_table = self._build_physically_locked_table(
                tagged_texts, logical_structure
            )

        # アンカー構築
        table_bbox = None
        if locked_table and locked_table.get('cells_flat'):
            anchors = self._build_anchors_from_locked(locked_table)
            # ロック済みセルを取得
            locked_cells = locked_table['cells_flat']
            # 表外トークン（untagged）を保持
            untagged_texts = [t for t in tagged_texts if t.get('type') == 'untagged']
            # 両方を結合（読み順ソートは後で行う）
            final_tagged_texts = locked_cells + untagged_texts
            tables = [locked_table]
            # table_bboxを計算（タイトル検出用）
            table_bbox = self._get_table_bbox(locked_table)
            logger.info(
                f"[G4] 座標ロック完了: {locked_table['row_count']}行 x {locked_table['col_count']}列 "
                f"= {len(locked_cells)}セル, 表外={len(untagged_texts)}"
            )
        else:
            # logical_structureがない場合は従来どおりの組み立て
            anchors = self._build_anchors(tagged_texts, x_headers, y_headers)
            tables = self._merge_tables(tagged_texts, x_headers, y_headers)
            final_tagged_texts = tagged_texts

        # ============================================
        # 【Ver 10.8】読み順ソート（上→下、左→右）
        # ============================================
        sorted_texts = self._sort_by_reading_order(final_tagged_texts)

        # 表タイトル検出（表の直上にあるuntaggedを関連付け）
        if table_bbox:
            sorted_texts = self._detect_table_context(sorted_texts, table_bbox)

        # extracted_texts形式への変換（read-only）
        extracted_texts = self._build_extracted_texts(sorted_texts)

        # full_text構築（読み順ソート済み）
        full_text = '\n'.join(t.get('text', '') for t in sorted_texts if t.get('text'))

        elapsed = time.time() - g4_start
        logger.info(f"[G4] 物理正本確定完了: anchors={len(anchors)}, tables={len(tables)}")

        return {
            'tagged_texts': sorted_texts,
            'extracted_texts': extracted_texts,
            'full_text_ordered': full_text,
            'anchors': anchors,
            'tables': tables,
            'x_headers': x_headers,
            'y_headers': y_headers,
            'text_source': scrubbed_core.get('text_source'),
            'text_source_by_page': scrubbed_core.get('text_source_by_page', {}),
            'change_log': scrubbed_core.get('change_log', []),
            'stats': {
                **scrubbed_core.get('stats', {}),
                'anchor_count': len(anchors),
                'table_count': len(tables),
                'coordinate_locked': locked_table is not None,
                'reading_order_sorted': True,
                'g4_elapsed': elapsed
            }
        }

    # ============================================================
    # 物理座標ロック（Ver 10.7: H1から移設）
    # ============================================================

    def _build_physically_locked_table(
        self,
        tagged_texts: List[Dict],
        logical_structure: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        F3のrow/colインデックスとテキストをそのまま継承する

        文字列の結合（join）は行わない。F3が「別の単語」として
        仕分けたトークンは、同一セル内でも独立したアイテムとして
        H1へ渡す。AIが癒着の解釈を行う。

        Args:
            tagged_texts: G3の洗い替え済みテキスト（F3の row/col 付き）
            logical_structure: F2の出力（参照用）

        Returns:
            locked_table: {row_count, col_count, cells_flat} or None
        """
        # F3のrow/colを持つセルトークンのみ対象
        cell_tokens = [tt for tt in tagged_texts
                       if tt.get('type') == 'cell'
                       and tt.get('row') is not None
                       and tt.get('col') is not None]

        if not cell_tokens:
            logger.info("[G4] F3 row/col 付きセルなし → 座標ロック不可")
            return None

        # グリッドサイズはF3のインデックスから算出
        max_row = max(tt['row'] for tt in cell_tokens)
        max_col = max(tt['col'] for tt in cell_tokens)
        rows = max_row + 1
        cols = max_col + 1

        # F3トークンを結合せず、個別アイテムとしてcells_flatに格納
        # X座標順にソートして読み順を保証
        cells_flat = sorted(
            [
                {
                    'row': tt['row'],
                    'col': tt['col'],
                    'text': tt.get('text', ''),
                    'type': 'cell',
                    'bbox': tt.get('bbox') or tt.get('bbox_agg') or [0, 0, 0, 0],
                    'panel_id': tt.get('panel_id'),
                }
                for tt in cell_tokens
            ],
            key=lambda t: (t['row'], t['col'], t['bbox'][0] if t['bbox'] else 0)
        )

        # F2の物理境界（参照用に保持）
        row_boundaries = logical_structure.get('row_boundaries', [])
        col_boundaries = logical_structure.get('col_boundaries', [])
        if not col_boundaries:
            panels = logical_structure.get('panels', [])
            if panels:
                col_boundaries = panels[0].get('col_boundaries', [])

        row_y = [b['y'] for b in row_boundaries if isinstance(b, dict) and 'y' in b]
        col_x = [b['x'] for b in col_boundaries if isinstance(b, dict) and 'x' in b]

        logger.info(f"[G4] F3トークン個別継承: {len(cells_flat)}アイテム → {rows}行x{cols}列（結合なし）")

        return {
            'ref_id': 'TBL_001',
            'row_count': rows,
            'col_count': cols,
            'cells_flat': cells_flat,
            'row_boundaries': row_y,
            'col_boundaries': col_x
        }

    # ============================================================
    # アンカー構築
    # ============================================================

    def _build_anchors_from_locked(self, locked_table: Dict[str, Any]) -> List[Dict]:
        """物理ロック済みテーブルからアンカーを発行（Ver 10.7）"""
        anchors = []
        cells_flat = locked_table.get('cells_flat', [])

        if cells_flat:
            anchors.append({
                "anchor_id": "TBL_001",
                "type": "table",
                "table_type": "grid_table",
                "tagged_texts": cells_flat,
                "row_count": locked_table['row_count'],
                "col_count": locked_table['col_count'],
                "is_heavy": len(cells_flat) >= 100,
            })

        return anchors

    def _build_anchors(
        self,
        tagged_texts: List[Dict],
        x_headers: List[str],
        y_headers: List[str]
    ) -> List[Dict]:
        """アンカーパケット構築（従来互換）"""
        anchors = []
        anchor_index = 1

        # セル（type=cell）→ 表アンカー
        cell_items = [t for t in tagged_texts if t.get('type') == 'cell']
        if cell_items:
            anchors.append({
                "anchor_id": f"TBL_{anchor_index:03d}",
                "type": "table",
                "table_type": "grid_table",
                "tagged_texts": cell_items,
                "x_headers": x_headers,
                "y_headers": y_headers,
                "row_count": len(set(c.get('y_header', '') for c in cell_items)),
                "col_count": len(x_headers),
                "is_heavy": len(cell_items) >= 100,
            })
            anchor_index += 1

        # テキスト（type=untagged）→ テキストアンカー
        text_items = [t for t in tagged_texts if t.get('type') == 'untagged']
        for txt in text_items:
            anchors.append({
                "anchor_id": f"TXT_{anchor_index:03d}",
                "type": "text",
                "content": txt.get('text', ''),
                "page": txt.get('page', 0),
                "is_heavy": False,
            })
            anchor_index += 1

        # ヘッダー（type=header）→ 参照用
        header_items = [t for t in tagged_texts if t.get('type') == 'header']
        if header_items:
            anchors.append({
                "anchor_id": f"HDR_{anchor_index:03d}",
                "type": "header_reference",
                "items": header_items,
                "is_heavy": False,
            })

        return anchors

    # ============================================================
    # テーブルマージ・テキスト変換（従来互換）
    # ============================================================

    def _merge_tables(
        self,
        tagged_texts: List[Dict],
        x_headers: List[str],
        y_headers: List[str]
    ) -> List[Dict]:
        """テーブルのマージ（複数チャンク統合）"""
        cell_items = [t for t in tagged_texts if t.get('type') == 'cell']
        if not cell_items:
            return []

        # ページごとにグループ化
        pages = {}
        for cell in cell_items:
            page = cell.get('page', 0)
            if page not in pages:
                pages[page] = []
            pages[page].append(cell)

        tables = []
        for page, cells in sorted(pages.items()):
            tables.append({
                'page': page,
                'cells': cells,
                'x_headers': x_headers,
                'y_headers': [yh for yh in y_headers if any(c.get('y_header') == yh for c in cells)],
                'row_count': len(set(c.get('y_header', '') for c in cells)),
                'col_count': len(x_headers),
            })

        return tables

    def _build_extracted_texts(self, tagged_texts: List[Dict]) -> List[Dict]:
        """extracted_texts形式への変換（read-only）"""
        return [
            {
                'block_id': t.get('id', ''),
                'text': t.get('text', ''),
                'coords': t.get('bbox', []),
                'page': t.get('page', 0),
                'x_header': t.get('x_header', ''),
                'y_header': t.get('y_header', ''),
                'type': t.get('type', 'untagged'),
            }
            for t in tagged_texts
        ]

    # ============================================================
    # 読み順ソート（Ver 10.8）
    # ============================================================

    def _get_y_center(self, item: Dict) -> float:
        """アイテムのY中心座標を取得"""
        bbox = item.get('bbox') or item.get('bbox_agg') or item.get('coords', {}).get('bbox')
        if bbox and len(bbox) >= 4:
            return (bbox[1] + bbox[3]) / 2
        return float('inf')

    def _get_x_center(self, item: Dict) -> float:
        """アイテムのX中心座標を取得"""
        bbox = item.get('bbox') or item.get('bbox_agg') or item.get('coords', {}).get('bbox')
        if bbox and len(bbox) >= 4:
            return (bbox[0] + bbox[2]) / 2
        return float('inf')

    def _sort_by_reading_order(self, items: List[Dict]) -> List[Dict]:
        """
        全アイテムを読み順（上→下、左→右）でソートする

        同一行（Y座標差がROW_TOLERANCE以内）のアイテムは左から右へ並べ、
        異なる行はY座標順に並べる。

        Args:
            items: ソート対象のアイテムリスト

        Returns:
            読み順でソートされたリスト
        """
        if not items:
            return items

        # ページ → Y座標 → X座標 でソート
        # 同一行の吸収: Y座標を ROW_TOLERANCE で量子化
        def sort_key(item):
            page = item.get('page', 0)
            y = self._get_y_center(item)
            x = self._get_x_center(item)
            # Y座標を許容範囲で丸めて同一行を同じ値に
            y_quantized = int(y / self.ROW_TOLERANCE) * self.ROW_TOLERANCE
            return (page, y_quantized, x)

        sorted_items = sorted(items, key=sort_key)

        # 読み順インデックスを付与
        for i, item in enumerate(sorted_items):
            item['reading_order'] = i

        logger.info(f"[G4] 読み順ソート完了: {len(sorted_items)}件")
        return sorted_items

    def _get_table_bbox(self, locked_table: Dict[str, Any]) -> Optional[List[float]]:
        """物理ロック済みテーブルのbboxを取得"""
        row_y = locked_table.get('row_boundaries', [])
        col_x = locked_table.get('col_boundaries', [])
        if row_y and col_x:
            return [col_x[0], row_y[0], col_x[-1], row_y[-1]]
        return None

    def _detect_table_context(
        self,
        sorted_items: List[Dict],
        table_bbox: List[float]
    ) -> List[Dict]:
        """
        表の直上にあるuntaggedテキストを表のタイトル/コンテキストとして検出

        表の上端から一定距離（表高さの10%または50px以内）にある
        untaggedテキストに 'context_for': 'TBL_001' を付与する。

        Args:
            sorted_items: 読み順ソート済みアイテム
            table_bbox: 表の境界 [x0, y0, x1, y1]

        Returns:
            コンテキスト情報が付与されたアイテムリスト
        """
        if not table_bbox or len(table_bbox) < 4:
            return sorted_items

        table_top = table_bbox[1]
        table_height = table_bbox[3] - table_bbox[1]
        # タイトル検出の許容距離: 表高さの10%または50pxの大きい方
        context_threshold = max(table_height * 0.10, 50)

        context_count = 0
        for item in sorted_items:
            if item.get('type') != 'untagged':
                continue

            y_center = self._get_y_center(item)
            # 表の上端より上で、かつ閾値以内にある
            if y_center < table_top and (table_top - y_center) <= context_threshold:
                item['context_for'] = 'TBL_001'
                item['_context_type'] = 'title'
                context_count += 1

        if context_count > 0:
            logger.info(f"[G4] 表タイトル検出: {context_count}件を TBL_001 に関連付け")

        return sorted_items
