"""
F3: 物理仕分け（セル割当）

【Ver 10.8】I/O契約 + トークン中心処理
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入力:
  - grid: F2が確定したgrid正本
  - tokens: 文字トークン（E1 or E6）- 全トークン（フィルタなし）
  - structure: F2が解析した構造情報
  - panels: F2が生成したpanels配列（オプション）

出力（固定）: structured_table
  - rows: List[List[Dict]]  # row × col 配列
  - cells: List[Dict]       # 全セルのフラット配列
    - token_ids: List[str]  # 根拠token参照（証拠リンク）
    - source: 'physical' | 'vision'
    - bbox_agg: [x0,y0,x1,y1]  # セル内bbox合成
  - tagged_texts: List[Dict] # タグ付きテキスト（全トークン、座標順）
    - type: 'cell' | 'untagged'
    - bbox: 必ず保持（座標情報の完全維持）
    - cell_targets: [{panel_id, row, col, reason, overlap}, ...]  # セル住所（cellのみ）
  - x_headers: List[str]
  - y_headers: List[str]
  - stats: {assigned, unassigned, elapsed}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

モデル: 不要（完全決定論）

処理（Ver 10.8: トークン中心）:
- 全トークンをループし、各トークンにタグ付け
- セル内: type='cell' + cell_targets
- セル外: type='untagged' + _reason
- bbox は全トークンで必ず保持（読み順ソート用）
- 「分ける」のではなく「タグ付け」する

ここで「文字の所属」が確定する。
"""

import time
from typing import Dict, Any, List, Tuple, Optional
from loguru import logger


class F3CellAssigner:
    """F3: 物理仕分け（セル割当） - 決定論・モデル不要

    Ver 10.8: トークン中心処理 + bbox完全保持
    - 全トークンを受け取り、タグ付けして返す
    - セル内/セル外を「分ける」のではなく「属性を付加」する
    - bbox（座標情報）は全トークンで必ず保持
    - cell_targets に (panel_id, row, col) を付与
    """

    CENTER_MARGIN = 2
    OVERLAP_THRESHOLD = 0.3

    # 結合セル対応（Ver 10.3: 無制限通過・記録のみ）
    SPAN_OVERLAP_THRESHOLD = 0.20         # 縦横共通（方向差別なし）
    SMALL_TOKEN_AREA_THRESHOLD = 100      # 小トークンはspan禁止（例：数字1文字）
    LARGE_SPAN_WARN_THRESHOLD = 10        # この数以上で警告ログ（落とさない）

    def __init__(self):
        pass

    def assign(
        self,
        grid: Dict[str, Any],
        tokens: List[Dict[str, Any]],
        structure: Dict[str, Any],
        panels: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Dict[str, Any], List[Dict]]:
        """
        トークンをセルに決定論で割り当てる

        Args:
            grid: F2が確定したgrid（互換性: panels[0].grid）
            tokens: 文字トークン
            structure: F2が解析した構造情報
            panels: F2のpanels配列（オプション、あればpanelごとに処理）

        Returns:
            (structured_table, low_confidence_items)
            - structured_table: row × col 配列
            - low_confidence_items: 低信頼アイテム（通常は空）
        """
        f3_start = time.time()
        logger.info(f"[F3] 物理仕分け開始")
        logger.info(f"[F3]   入力tokens: {len(tokens)}個")
        logger.info(f"[F3]   panels: {len(panels) if panels else 0}個")
        logger.info(f"[F3]   header_rows: {structure.get('header_rows', [])}")
        logger.info(f"[F3]   header_cols: {structure.get('header_cols', [])}")

        # panels情報を保存（cell_targets用）
        self._panels = panels or []

        result = {
            'rows': [],
            'cells': [],
            'tagged_texts': [],
            'x_headers': [],
            'y_headers': [],
            'stats': {}
        }
        low_confidence = []

        if not grid or not grid.get('cells'):
            logger.warning("[F3] gridなし → 全トークンをテキスト扱い")
            for token in tokens:
                result['tagged_texts'].append({
                    'id': token.get('id', ''),
                    'text': token.get('text', ''),
                    'type': 'untagged',
                    'x_header': '',
                    'y_header': '',
                    'bbox': token.get('bbox') or token.get('coords', {}).get('bbox'),
                    'bbox_agg': token.get('bbox') or token.get('coords', {}).get('bbox'),
                    'page': token.get('page', 0),
                    '_reason': 'no_grid'
                })
            return result, low_confidence

        cells = grid.get('cells', [])
        row_count = grid.get('row_count', 0)
        col_count = grid.get('col_count', 0)

        header_rows = set(structure.get('header_rows', []))
        header_cols = set(structure.get('header_cols', []))

        cell_index = {(c['row'], c['col']): {
            **c,
            'tokens': [],
            'text': '',
            'token_ids': [],      # 証拠リンク
            'source': 'vision',   # デフォルト
            'bbox_agg': None,     # セル内bbox合成
            '_span_assigned': False,   # 結合セル由来の複製フラグ
            '_span_uncertain': False   # 大きいspan（曖昧さフラグ）
        } for c in cells}

        # Stats: 2系統に分離（Ver 10.2）
        assigned_unique_tokens = 0   # 処理したユニークtoken数
        assigned_cell_links = 0      # token→cell のリンク数（複製により増加）
        span_assignments = []        # span割当のログ用
        unassigned = []

        # panel_idのデフォルト値（panelsがない場合は0）
        default_panel_id = 0

        for token in tokens:
            bbox = token.get('bbox') or token.get('coords', {}).get('bbox')
            if not bbox or len(bbox) < 4:
                unassigned.append({**token, '_reason': 'no_bbox'})
                continue

            tx = (bbox[0] + bbox[2]) / 2
            ty = (bbox[1] + bbox[3]) / 2
            token_id = token.get('block_id') or token.get('id') or f"t{assigned_unique_tokens}"

            # 複数セル割当を試行（Ver 10.3: 無制限通過・記録のみ）
            targets = self._find_cells_for_token(tx, ty, bbox, cells)

            if targets:
                assigned_unique_tokens += 1
                is_span = len(targets) > 1

                # cell_targets を構築（panel_id付き）
                cell_targets = []
                for target in targets:
                    row, col = target
                    cell_targets.append({
                        'panel_id': default_panel_id,
                        'row': row,
                        'col': col,
                        'reason': 'overlap' if is_span else 'center',
                        'overlap': None  # 後で設定可能
                    })

                # span割当のログ記録
                if is_span:
                    bbox_w = bbox[2] - bbox[0]
                    bbox_h = bbox[3] - bbox[1]
                    token_area = bbox_w * bbox_h
                    rows_in_span = sorted(set(t[0] for t in targets))
                    cols_in_span = sorted(set(t[1] for t in targets))
                    span_direction = "HORIZONTAL" if len(rows_in_span) == 1 else "VERTICAL"
                    is_large_span = len(targets) >= self.LARGE_SPAN_WARN_THRESHOLD

                    span_assignments.append({
                        'token_id': token_id,
                        'text': token.get('text', ''),
                        'cells': targets,
                        'cell_targets': cell_targets,
                        'cell_count': len(targets),
                        'direction': span_direction,
                        'bbox_area': token_area,
                        'bbox': bbox,
                        'is_large': is_large_span
                    })

                for target in targets:
                    # 大きいspanは uncertain フラグを付ける（落とさない）
                    is_large = is_span and len(targets) >= self.LARGE_SPAN_WARN_THRESHOLD
                    token_with_meta = {
                        **token,
                        '_center': (tx, ty),
                        '_cell_targets': cell_targets,
                        '_span_assigned': is_span,
                        '_span_cells': targets if is_span else None,
                        '_ambiguous_span': is_large  # 曖昧さフラグ
                    }
                    cell_index[target]['tokens'].append(token_with_meta)
                    cell_index[target]['token_ids'].append(token_id)
                    if is_span:
                        cell_index[target]['_span_assigned'] = True
                    if is_large:
                        cell_index[target]['_span_uncertain'] = True  # 曖昧さフラグ
                    # source判定: physical_charならphysical
                    if token.get('_source') == 'physical' or token.get('source') == 'physical':
                        cell_index[target]['source'] = 'physical'
                    assigned_cell_links += 1
            else:
                unassigned.append({**token, '_reason': 'outside', '_center': (tx, ty)})

        x_headers = []
        y_headers = []

        for (row, col), cell in cell_index.items():
            sorted_tokens = sorted(cell['tokens'], key=lambda t: (t['_center'][1], t['_center'][0]))
            cell['text'] = ' '.join(t.get('text', '') for t in sorted_tokens).strip()

            # bbox_agg: セル内全トークンのbbox合成
            if sorted_tokens:
                bboxes = [t.get('bbox') or t.get('coords', {}).get('bbox') for t in sorted_tokens]
                bboxes = [b for b in bboxes if b and len(b) >= 4]
                if bboxes:
                    cell['bbox_agg'] = [
                        min(b[0] for b in bboxes),
                        min(b[1] for b in bboxes),
                        max(b[2] for b in bboxes),
                        max(b[3] for b in bboxes)
                    ]

            if row in header_rows and cell['text']:
                if cell['text'] not in x_headers:
                    x_headers.append(cell['text'])
            if col in header_cols and row not in header_rows and cell['text']:
                if cell['text'] not in y_headers:
                    y_headers.append(cell['text'])

        result['x_headers'] = x_headers
        result['y_headers'] = y_headers

        # ============================================
        # 【Ver 10.8】トークン中心のtagged_texts構築
        # 全トークンを入力順（≒座標順）で処理し、タグ付けする
        # ============================================

        # トークンIDからセル情報へのマッピングを構築
        token_to_cell_info = {}
        for (row, col), cell in cell_index.items():
            if row in header_rows or col in header_cols:
                continue  # ヘッダーはスキップ

            x_h = x_headers[col] if col < len(x_headers) else ''
            y_h = ''
            for hc in header_cols:
                hcell = cell_index.get((row, hc))
                if hcell and hcell['text']:
                    y_h = hcell['text']
                    break

            # cell_targets を構築
            cell_target = {
                'panel_id': default_panel_id,
                'row': row,
                'col': col,
                'reason': 'center',
                'overlap': None
            }
            all_cell_targets = [cell_target]
            if cell.get('_span_assigned'):
                seen = set()
                for tok in cell.get('tokens', []):
                    for ct in tok.get('_cell_targets', []):
                        key = (ct['panel_id'], ct['row'], ct['col'])
                        if key not in seen:
                            seen.add(key)
                            all_cell_targets.append(ct)
                seen_final = set()
                deduped = []
                for ct in all_cell_targets:
                    key = (ct['panel_id'], ct['row'], ct['col'])
                    if key not in seen_final:
                        seen_final.add(key)
                        deduped.append(ct)
                all_cell_targets = deduped

            # このセルに属する全トークンIDに情報を紐付け
            for token_id in cell.get('token_ids', []):
                token_to_cell_info[token_id] = {
                    'row': row,
                    'col': col,
                    'x_header': x_h,
                    'y_header': y_h,
                    'cell_targets': all_cell_targets,
                    'source': cell['source'],
                    'bbox_agg': cell['bbox_agg'],
                    '_span_assigned': cell.get('_span_assigned', False)
                }

        # 全トークンを入力順でtagged_textsに追加
        processed_tokens = set()
        for token in tokens:
            token_id = token.get('block_id') or token.get('id') or ''
            bbox = token.get('bbox') or token.get('coords', {}).get('bbox')

            # 同一トークンの重複処理を防止
            if token_id and token_id in processed_tokens:
                continue
            if token_id:
                processed_tokens.add(token_id)

            if token_id in token_to_cell_info:
                # セルに割り当てられたトークン
                cell_info = token_to_cell_info[token_id]
                result['tagged_texts'].append({
                    'id': token_id,
                    'text': token.get('text', ''),
                    'x_header': cell_info['x_header'],
                    'y_header': cell_info['y_header'],
                    'type': 'cell',
                    'bbox': bbox,
                    'bbox_agg': cell_info['bbox_agg'],
                    'page': token.get('page', 0),
                    'token_ids': [token_id],
                    'source': cell_info['source'],
                    'cell_targets': cell_info['cell_targets'],
                    'row': cell_info['row'],
                    'col': cell_info['col'],
                    '_physical_decision': True,
                    '_span_assigned': cell_info['_span_assigned']
                })
            else:
                # セルに割り当てられなかったトークン（untagged）
                reason = 'no_bbox' if not bbox else 'outside'
                result['tagged_texts'].append({
                    'id': token_id,
                    'text': token.get('text', ''),
                    'x_header': '',
                    'y_header': '',
                    'type': 'untagged',
                    'bbox': bbox,
                    'bbox_agg': bbox,
                    'page': token.get('page', 0),
                    '_reason': reason
                })

        result['cells'] = list(cell_index.values())

        # span統計を集計（Ver 10.3: 無制限通過）
        vertical_spans = [s for s in span_assignments if s.get('direction') == 'VERTICAL']
        horizontal_spans = [s for s in span_assignments if s.get('direction') == 'HORIZONTAL']
        large_spans = [s for s in span_assignments if s.get('is_large')]

        result['stats'] = {
            'assigned': assigned_unique_tokens,           # 互換性のため維持
            'assigned_unique_tokens': assigned_unique_tokens,
            'assigned_cell_links': assigned_cell_links,   # 複製により増加
            'span_count': len(span_assignments),          # span割当が発生したtoken数
            'vertical_span_count': len(vertical_spans),
            'horizontal_span_count': len(horizontal_spans),
            'large_span_count': len(large_spans),         # 大きいspan（警告対象）
            'max_span_size': max((s['cell_count'] for s in span_assignments), default=0),
            'unassigned': len(unassigned),
            'elapsed': time.time() - f3_start
        }

        # 詳細ログ出力
        self._log_assignment_result(result, cell_index, x_headers, y_headers, unassigned, span_assignments)

        logger.info(f"[F3] 完了: unique_tokens={assigned_unique_tokens}, cell_links={assigned_cell_links}, "
                   f"span(V={len(vertical_spans)}/H={len(horizontal_spans)}/LARGE={len(large_spans)}), "
                   f"unassigned={len(unassigned)}")
        return result, low_confidence

    def _find_cells_for_token(
        self,
        tx: float,
        ty: float,
        bbox: List[float],
        cells: List[Dict]
    ) -> List[Tuple[int, int]]:
        """
        トークンが割り当たる全セルを返す（Ver 10.3: 無制限通過・記録のみ）

        原則:
        - F3は殺さない
        - 大きくても通す
        - 危険は記録する、判断は後段

        戦略:
        1. 小トークン（面積<閾値）はspan禁止（単一セル）
        2. overlap_ratio を全セルで計算（縦横共通閾値）
        3. 連続性チェック（形だけ判定、落とさない）
        4. 大きいspanは警告ログだけ出して通す

        Returns:
            割当先セルの (row, col) リスト（空なら割当不可）
            ※ _span_uncertain フラグ付きの場合あり（呼び出し元で処理）
        """
        bbox_w = bbox[2] - bbox[0]
        bbox_h = bbox[3] - bbox[1]
        token_area = bbox_w * bbox_h

        if token_area <= 0:
            # 面積ゼロ → 中心点ルールにフォールバック
            return self._find_cell_by_center(tx, ty, cells)

        # 小トークンはspan禁止（数字1文字など）
        if token_area < self.SMALL_TOKEN_AREA_THRESHOLD:
            return self._find_cell_by_center(tx, ty, cells)

        # 全セルの overlap_ratio を計算（縦横共通閾値）
        candidates = []
        for c in cells:
            cell_bbox = c['bbox']
            overlap_ratio = self._overlap_ratio(bbox, cell_bbox, token_area)
            if overlap_ratio >= self.SPAN_OVERLAP_THRESHOLD:
                candidates.append({
                    'row': c['row'],
                    'col': c['col'],
                    'overlap_ratio': overlap_ratio,
                    'bbox': cell_bbox
                })

        if not candidates:
            # 閾値を超えるセルなし → 中心点ルールにフォールバック
            return self._find_cell_by_center(tx, ty, cells)

        if len(candidates) == 1:
            # 単一セル → そのまま返す
            return [(candidates[0]['row'], candidates[0]['col'])]

        # 複数候補あり → 連続性チェック（形だけ判定）
        rows = sorted(set(c['row'] for c in candidates))
        cols = sorted(set(c['col'] for c in candidates))

        is_row_consecutive = self._is_consecutive(rows)
        is_col_consecutive = self._is_consecutive(cols)

        # 方向判定
        is_vertical_span = is_row_consecutive and len(cols) == 1
        is_horizontal_span = is_col_consecutive and len(rows) == 1

        # 連続性がない場合：落とさない、絞るだけ
        if not is_vertical_span and not is_horizontal_span:
            if is_row_consecutive:
                # 縦優先（複数列に跨っているが行は連続）→ 最頻出列に絞る
                col_counts = {}
                for c in candidates:
                    col_counts[c['col']] = col_counts.get(c['col'], 0) + c['overlap_ratio']
                best_col = max(col_counts, key=col_counts.get)
                candidates = [c for c in candidates if c['col'] == best_col]
                is_vertical_span = True
            elif is_col_consecutive:
                # 横優先（複数行に跨っているが列は連続）→ 最頻出行に絞る
                row_counts = {}
                for c in candidates:
                    row_counts[c['row']] = row_counts.get(c['row'], 0) + c['overlap_ratio']
                best_row = max(row_counts, key=row_counts.get)
                candidates = [c for c in candidates if c['row'] == best_row]
                is_horizontal_span = True
            else:
                # バラバラ → overlap最大のセルに絞る（単一）
                best = max(candidates, key=lambda c: c['overlap_ratio'])
                return [(best['row'], best['col'])]

        # 方向決定
        span_direction = "HORIZONTAL" if is_horizontal_span else "VERTICAL"

        # 大きいspanは警告ログだけ出して通す（落とさない）
        if len(candidates) >= self.LARGE_SPAN_WARN_THRESHOLD:
            logger.warning(
                f"[F3] LARGE SPAN PASSED: direction={span_direction}, "
                f"span_size={len(candidates)}, bbox_area={token_area:.0f}"
            )

        # 複数セルに割当（必ず通す）
        result = [(c['row'], c['col']) for c in candidates]
        result.sort()

        # 通常ログ（span適用）
        logger.info(
            f"[F3] SPAN APPLIED: direction={span_direction}, "
            f"span_size={len(result)}, cells={result[:10]}{'...' if len(result) > 10 else ''}"
        )

        return result

    def _find_cell_by_center(self, tx: float, ty: float, cells: List[Dict]) -> List[Tuple[int, int]]:
        """中心点ルールで単一セルを探す（フォールバック用）"""
        for c in cells:
            b = c['bbox']
            if b[0] - self.CENTER_MARGIN <= tx <= b[2] + self.CENTER_MARGIN:
                if b[1] - self.CENTER_MARGIN <= ty <= b[3] + self.CENTER_MARGIN:
                    return [(c['row'], c['col'])]
        return []

    def _find_cell(self, tx, ty, bbox, cells) -> Optional[Tuple[int, int]]:
        """単一セルを返す（互換性のためのラッパー）"""
        targets = self._find_cells_for_token(tx, ty, bbox, cells)
        if targets:
            # 複数ある場合は最初のものを返す（互換性）
            return targets[0]
        return None

    def _overlap_ratio(self, token_bbox: List[float], cell_bbox: List[float], token_area: float) -> float:
        """トークンとセルの overlap_ratio を計算（交差面積 / トークン面積）"""
        x1 = max(token_bbox[0], cell_bbox[0])
        y1 = max(token_bbox[1], cell_bbox[1])
        x2 = min(token_bbox[2], cell_bbox[2])
        y2 = min(token_bbox[3], cell_bbox[3])

        if x2 <= x1 or y2 <= y1:
            return 0.0

        inter_area = (x2 - x1) * (y2 - y1)
        return inter_area / token_area if token_area > 0 else 0.0

    def _is_consecutive(self, values: List[int]) -> bool:
        """値が連続しているかチェック"""
        if len(values) <= 1:
            return True
        for i in range(1, len(values)):
            if values[i] - values[i-1] != 1:
                return False
        return True

    def _iou(self, b1, b2):
        x1, y1 = max(b1[0], b2[0]), max(b1[1], b2[1])
        x2, y2 = min(b1[2], b2[2]), min(b1[3], b2[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        inter = (x2 - x1) * (y2 - y1)
        a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
        a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
        return inter / (a1 + a2 - inter) if (a1 + a2 - inter) > 0 else 0.0

    def _log_assignment_result(
        self,
        result: Dict[str, Any],
        cell_index: Dict,
        x_headers: List[str],
        y_headers: List[str],
        unassigned: List[Dict],
        span_assignments: List[Dict] = None
    ):
        """割当結果の詳細ログ（全件出力）"""
        logger.info("[F3] ===== 生成物ログ開始 =====")

        # Stats サマリ（Ver 10.3: 無制限通過）
        stats = result.get('stats', {})
        logger.info(f"[F3] Stats:")
        logger.info(f"[F3]   unique_tokens: {stats.get('assigned_unique_tokens', stats.get('assigned', 0))}")
        logger.info(f"[F3]   cell_links: {stats.get('assigned_cell_links', 0)}")
        logger.info(f"[F3]   span_count: {stats.get('span_count', 0)} "
                   f"(V={stats.get('vertical_span_count', 0)}, H={stats.get('horizontal_span_count', 0)}, "
                   f"LARGE={stats.get('large_span_count', 0)})")
        logger.info(f"[F3]   max_span_size: {stats.get('max_span_size', 0)}")
        logger.info(f"[F3]   unassigned: {stats.get('unassigned', 0)}")

        # span割当の詳細（Ver 10.3: 無制限通過・LARGE明示）
        if span_assignments:
            logger.info(f"[F3] ----- span割当 ({len(span_assignments)}件) -----")
            for sa in span_assignments[:30]:  # 先頭30件まで
                text = sa['text'].replace('\n', '\\n') if sa['text'] else '(empty)'
                cells = sa['cells']
                cell_count = sa['cell_count']
                direction = sa.get('direction', '?')
                bbox_area = sa.get('bbox_area', 0)
                is_large = sa.get('is_large', False)
                large_flag = ' [LARGE]' if is_large else ''
                cells_str = ', '.join(f"({r},{c})" for r, c in cells[:10])
                if cell_count > 10:
                    cells_str += f"... (+{cell_count - 10})"
                logger.info(f"[F3]   [{direction}]{large_flag} '{text}' -> {cell_count}セル (area={bbox_area:.0f}): [{cells_str}]")
            if len(span_assignments) > 30:
                logger.info(f"[F3]   ... and {len(span_assignments) - 30} more span assignments")

        # ヘッダー情報（全件）
        logger.info(f"[F3] x_headers ({len(x_headers)}件):")
        for i, xh in enumerate(x_headers):
            logger.info(f"[F3]   [{i}] '{xh}'")

        logger.info(f"[F3] y_headers ({len(y_headers)}件):")
        for i, yh in enumerate(y_headers):
            logger.info(f"[F3]   [{i}] '{yh}'")

        # 全セル割当（全件）
        logger.info(f"[F3] ----- 全セル割当 ({len(cell_index)}件) -----")
        for (row, col), cell in sorted(cell_index.items()):
            text = cell['text'].replace('\n', '\\n') if cell['text'] else '(empty)'
            token_count = len(cell.get('tokens', []))
            token_ids = cell.get('token_ids', [])
            span_flag = ' [SPAN]' if cell.get('_span_assigned') else ''
            logger.info(f"[F3]   [{row},{col}] '{text}' (tokens={token_count}, ids={token_ids}){span_flag}")

        # 未割当トークン（全件）
        logger.info(f"[F3] ----- 未割当トークン ({len(unassigned)}件) -----")
        for ut in unassigned:
            text = ut.get('text', '').replace('\n', '\\n')
            reason = ut.get('_reason', 'unknown')
            center = ut.get('_center', (0, 0))
            bbox = ut.get('bbox', [0, 0, 0, 0])
            logger.info(f"[F3]   '{text}' reason={reason} center=({center[0]:.1f},{center[1]:.1f}) bbox={bbox}")

        # tagged_texts（全件）
        tagged = result.get('tagged_texts', [])
        logger.info(f"[F3] ----- tagged_texts ({len(tagged)}件) -----")
        for t in tagged:
            t_type = t.get('type', 'unknown')
            text = t.get('text', '').replace('\n', '\\n')
            x_h = t.get('x_header', '')
            y_h = t.get('y_header', '')
            token_ids = t.get('token_ids', [])
            if t_type == 'cell':
                logger.info(f"[F3]   [cell] '{text}' x_header='{x_h}' y_header='{y_h}' token_ids={token_ids}")
            else:
                reason = t.get('_reason', '')
                logger.info(f"[F3]   [untagged] '{text}' reason={reason}")

        logger.info("[F3] ===== 生成物ログ終了 =====")
