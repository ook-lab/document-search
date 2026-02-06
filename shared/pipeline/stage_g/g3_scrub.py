"""
G3: Scrub（唯一の書き換えゾーン）

【Ver 10.6】I/O契約（固定）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入力:
  - structured_table: F3の出力
  - logical_structure: F2の出力
  - e_physical_chars: E1の物理文字

出力（中間）: scrubbed_core
  - tagged_texts: 洗い替え済み
  - change_log: 差分ログ（before/after/reason）必須
  - text_source: 代表値
  - text_source_by_page: ページ単位
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ルール:
- 値を書き換えてよい唯一の場所
- 洗い替え（E1でOCRを上書き）
- 数値正規化
- 欠損の確定（補完しない）
- 必ず差分ログを残す
"""

import time
from typing import Dict, Any, List
from loguru import logger

class G3Scrub:
    """G3: Scrub - 唯一の書き換えゾーン"""

    BUCKET_SIZE = 50
    PROXIMITY_THRESHOLD = 2

    def __init__(self):
        self._change_log = []

    @property
    def change_log(self) -> List[Dict]:
        return self._change_log

    def scrub(
        self,
        structured_table: Dict[str, Any],
        logical_structure: Dict[str, Any],
        e_physical_chars: List[Dict],
        f1_quality: float = 1.0
    ) -> Dict[str, Any]:
        """
        正本化（書き換え）処理

        Returns:
            scrubbed_core（中間成果物、G4へ渡す）
        """
        g3_start = time.time()
        self._change_log = []

        tagged_texts = structured_table.get('tagged_texts', [])
        x_headers = structured_table.get('x_headers', [])
        y_headers = structured_table.get('y_headers', [])

        # 正本ソース判定
        has_physical = e_physical_chars and len(e_physical_chars) > 0
        text_source = "physical_chars" if has_physical else "vision_ocr"

        # ページ単位の追跡
        text_source_by_page = {}
        page_physical_count = {}
        page_vision_count = {}

        logger.info(f"[G3] Scrub開始: tokens={len(tagged_texts)}, physical={len(e_physical_chars) if e_physical_chars else 0}")

        # E1インデックス構築
        e_char_index = self._build_e_char_index(e_physical_chars)

        # 洗い替え処理
        scrubbed_data = []
        for tt in tagged_texts:
            scrubbed_item, source_type = self._scrub_item(tt, e_char_index, has_physical)
            scrubbed_data.append(scrubbed_item)

            page = scrubbed_item.get('page', 0)
            if source_type == 'physical':
                page_physical_count[page] = page_physical_count.get(page, 0) + 1
            else:
                page_vision_count[page] = page_vision_count.get(page, 0) + 1

        # ページ単位の正本ソース決定
        all_pages = set(page_physical_count.keys()) | set(page_vision_count.keys())
        for page in all_pages:
            p = page_physical_count.get(page, 0)
            v = page_vision_count.get(page, 0)
            text_source_by_page[page] = 'physical_chars' if p > v else 'vision_ocr'

        # 統計
        e1_count = sum(1 for d in scrubbed_data if d.get('_source') == 'e1_physical')
        e6_count = len(scrubbed_data) - e1_count

        elapsed = time.time() - g3_start
        logger.info(f"[G3] Scrub完了: E1={e1_count}, E6={e6_count}, changes={len(self._change_log)}")

        return {
            'tagged_texts': scrubbed_data,
            'x_headers': x_headers,
            'y_headers': y_headers,
            'text_source': text_source,
            'text_source_by_page': text_source_by_page,
            'change_log': self._change_log,
            'stats': {
                'e1_replaced': e1_count,
                'e6_adopted': e6_count,
                'total_changes': len(self._change_log),
                'elapsed': elapsed
            }
        }

    def _build_e_char_index(self, e_physical_chars: List[Dict]) -> Dict:
        """E1物理文字のインデックス構築"""
        index = {}
        if not e_physical_chars:
            return index

        for ec in e_physical_chars:
            page = ec.get('page', 0)
            bbox = ec.get('bbox', [0, 0, 0, 0])
            cx = (bbox[0] + bbox[2]) // 2
            cy = (bbox[1] + bbox[3]) // 2
            key = (page, cx // self.BUCKET_SIZE, cy // self.BUCKET_SIZE)
            if key not in index:
                index[key] = []
            index[key].append(ec)

        return index

    def _scrub_item(
        self,
        tt: Dict,
        e_char_index: Dict,
        has_physical: bool
    ) -> tuple:
        """単一アイテムの洗い替え"""
        tt_text = tt.get('text', '')
        tt_bbox = tt.get('bbox') or tt.get('coords', {}).get('bbox')
        page = tt.get('page', 0)

        base_item = {
            'id': tt.get('id', ''),
            'text': tt_text,
            'original_ocr': tt_text,
            'x_header': tt.get('x_header', ''),
            'y_header': tt.get('y_header', ''),
            'type': tt.get('type', 'cell'),
            'bbox': tt_bbox,
            'page': page,
            'token_ids': tt.get('token_ids', []),
            'bbox_agg': tt.get('bbox_agg'),
        }

        # bboxなし or E1なし → そのまま
        if not tt_bbox or not has_physical or len(tt_bbox) < 4:
            base_item['_scrubbed'] = False
            base_item['_source'] = 'e6_vision'
            return base_item, 'vision'

        # E1で洗い替え試行
        tt_cx = (tt_bbox[0] + tt_bbox[2]) / 2
        tt_cy = (tt_bbox[1] + tt_bbox[3]) / 2

        nearby = []
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                key = (page, int(tt_cx) // self.BUCKET_SIZE + dx, int(tt_cy) // self.BUCKET_SIZE + dy)
                nearby.extend(e_char_index.get(key, []))

        chars_in_bbox = []
        for ec in nearby:
            ec_bbox = ec.get('bbox', [0, 0, 0, 0])
            ec_cx = (ec_bbox[0] + ec_bbox[2]) / 2
            ec_cy = (ec_bbox[1] + ec_bbox[3]) / 2

            in_x = tt_bbox[0] - self.PROXIMITY_THRESHOLD <= ec_cx <= tt_bbox[2] + self.PROXIMITY_THRESHOLD
            in_y = tt_bbox[1] - self.PROXIMITY_THRESHOLD <= ec_cy <= tt_bbox[3] + self.PROXIMITY_THRESHOLD

            if in_x and in_y:
                chars_in_bbox.append({'text': ec.get('text', ''), 'x': ec_bbox[0]})

        if chars_in_bbox:
            sorted_chars = sorted(chars_in_bbox, key=lambda c: c['x'])

            # 重複除去（PDF太字対策）
            unique = [sorted_chars[0]] if sorted_chars else []
            for curr in sorted_chars[1:]:
                if curr['text'] != unique[-1]['text'] or abs(curr['x'] - unique[-1]['x']) >= 3:
                    unique.append(curr)

            scrubbed_text = ''.join(c['text'] for c in unique)

            if scrubbed_text != tt_text:
                self._log_change(
                    item_id=base_item['id'],
                    before=tt_text,
                    after=scrubbed_text,
                    reason='e1_replacement'
                )

            base_item['text'] = scrubbed_text
            base_item['_scrubbed'] = True
            base_item['_source'] = 'e1_physical'
            base_item['_e_char_count'] = len(chars_in_bbox)
            return base_item, 'physical'

        base_item['_scrubbed'] = False
        base_item['_source'] = 'e6_no_e1_match'
        return base_item, 'vision'

    def _log_change(self, item_id: str, before: str, after: str, reason: str):
        """差分ログ記録"""
        self._change_log.append({
            'item_id': item_id,
            'before': before,
            'after': after,
            'reason': reason,
            'timestamp': time.time()
        })
