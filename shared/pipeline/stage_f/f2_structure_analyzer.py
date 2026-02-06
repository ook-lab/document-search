"""
F2: 構造解析（グリッド無し行の発見・行列座標候補の生成）

【Ver 10.7】幾何中心モード + Panel対応 + 物理密度モード（ドメイン知識排除）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
F2の役割:
  1. F1の line_candidates + panel_candidates + separator_candidates を受け取る
  2. E8の tokens から「密集行」を検出（数値限定を外し、物理条件のみ）
  3. row_centers / row_boundaries を全体で1系統生成（全panelで共通）
  4. col_boundaries をパネルごとに独立生成（separator候補 + token x_center統合）
  5. grids をパネル数分生成（F3は各panelごとにcell assignment）

【禁止事項】
  - 行数の決め打ち
  - 意味付け（内容やドメインに基づく解釈）
  - table_type / column_roles の確定（unknown許容）
  - has_table を意味で落とす（物理条件: rows>=3 & cols>=2 のみ）

【入力ソースの優先順位】
  1. F1の line_candidates（solid/dashed）+ panel_candidates + separator_candidates
  2. E8の tokens（bbox + text）
  3. （最後に）LLMレスキュー（候補生成の補助に限定）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

入力:
  - line_candidates: F1の観測結果
  - panel_candidates: F1のパネル候補
  - separator_candidates_all: F1の全separator候補
  - separator_candidates_ranked: F1のseparator候補（score順）
  - tokens: E8正規化済みトークン

出力（候補レイヤ中心）:
  - data_rect_candidate: {x0,y0,x1,y1,score,source}
  - note_areas: [{x0,y0,x1,y1,score,source}, ...]
  - row_centers: [{y, score, source, evidence}, ...] ※全panel共通
  - row_boundaries: [{y, score, source, evidence}, ...] ※全panel共通
  - panels: [
      {
        panel_id: int,
        panel_bbox: [x0,y0,x1,y1],
        col_boundaries: [{x, score, source}, ...],
        col_boundaries_candidates: [{x, score, source}, ...],  # 不採用含む全候補
        grid: {row_count, col_count, cells, ...},
        confidence: float
      }, ...
    ]
  - grids: [grid0, grid1, ...] ※panels内のgridを配列化
  - grid: grids[0] ※互換性のため
  - has_table: bool
  - warnings: List[str]
"""

import time
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter
from loguru import logger
import numpy as np

class F2StructureAnalyzer:
    """F2: 構造解析（グリッド無し行の発見・幾何中心モード + Panel対応 + separator候補統合）"""

    # ============================================================
    # 閾値設定
    # ============================================================

    # 密集行の検出閾値
    Y_INTERVAL_CV_THRESHOLD = 0.25     # y間隔の変動係数がこれ以下で「等間隔」
    MIN_DENSE_ROWS = 5                 # 密集行と認めるための最小行数

    # クラスタリング閾値
    CLUSTER_MERGE_RATIO = 0.6          # token_height中央値のこの倍率でマージ

    # グリッド構築閾値
    MIN_GRID_ROWS = 3                  # gridと認めるための最小行数
    MIN_GRID_COLS = 2                  # gridと認めるための最小列数

    # 行境界生成
    EDGE_TOLERANCE_RATIO = 0.3         # 端境界のピッチ許容倍率

    # Panel検出（tokens空白帯）
    GAP_WIDTH_RATIO = 0.04             # 空白帯の最小幅（表幅の割合）
    HISTOGRAM_BINS = 400               # x軸ヒストグラムのbin数

    def __init__(self, llm_client=None):
        """
        llm_client: LLMクライアント（レスキュー用・必須ではない）
        """
        self.llm_client = llm_client
        self._usage: List[Dict[str, Any]] = []

    @property
    def usage(self) -> List[Dict[str, Any]]:
        return self._usage

    def analyze(
        self,
        line_candidates: Dict[str, Any],
        tokens: List[Dict[str, Any]],
        page_image: Optional[Any] = None,
        page_size: Optional[Dict[str, int]] = None,
        table_bbox_candidate: Optional[List[float]] = None,
        panel_candidates: Optional[List[Dict[str, Any]]] = None,
        separator_candidates_all: Optional[List[Dict[str, Any]]] = None,
        separator_candidates_ranked: Optional[List[Dict[str, Any]]] = None,
        doc_type: str = "unknown",
        bypass_prompt: Optional[str] = None,
        bypass_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        【Ver 10.6】構造解析 - 幾何中心モード + Panel対応 + separator候補統合

        F1のline_candidates/separator_candidatesとE8のtokensから、行列座標候補を生成する。
        Panel（横並び表）がある場合は、panelごとにcol_boundariesとgridを生成。

        Args:
            line_candidates: F1の観測結果 {horizontal: [...], vertical: [...]}
            tokens: E8正規化済みトークン
            page_image: ページ画像（LLMレスキュー用）
            page_size: ページサイズ {'w': int, 'h': int}
            table_bbox_candidate: F1推定の外枠
            panel_candidates: F1推定のパネル候補
            separator_candidates_all: F1の全separator候補（弱いものも含む）
            separator_candidates_ranked: F1のseparator候補（score順）
            doc_type: ドキュメントタイプ（参考）
            bypass_prompt: バイパスプロンプト（参考）
            bypass_meta: バイパスメタ情報（参考）

        Returns:
            候補レイヤ中心の結果（panels配列を含む）
        """
        f2_start = time.time()
        logger.info(f"[F2] 構造解析開始（幾何中心モード + Panel対応 + separator候補統合）")

        # separator候補を保存（col_boundaries生成で使用）
        self._separator_candidates_all = separator_candidates_all or []
        self._separator_candidates_ranked = separator_candidates_ranked or []
        logger.info(f"[F2] separator_candidates: all={len(self._separator_candidates_all)}, ranked={len(self._separator_candidates_ranked)}")

        result: Dict[str, Any] = {
            # 候補レイヤ（本丸）
            'data_rect_candidate': None,
            'note_areas': [],
            'row_centers': [],
            'row_boundaries': [],

            # Panel対応
            'panels': [],
            'grids': [],

            # F3向け互換（grids[0]を入れる）
            'grid': None,
            'col_boundaries': [],  # 互換用（panels[0]のcol_boundaries）
            'has_table': False,

            # メタ情報（確定しない）
            'table_type': 'unknown',
            'column_roles': [],
            'format_id': None,
            'confidence': 0.0,

            # その他
            'warnings': [],
            'metadata': {
                'source': 'geometry',
                'bypass_used': bypass_meta is not None,
                'panel_count': 0
            }
        }

        if page_size is None:
            page_size = {'w': 1275, 'h': 1800}  # A4@150dpiのデフォルト

        # ============================================
        # 1. F1の線情報を取得
        # ============================================
        h_lines = line_candidates.get('horizontal', [])
        v_lines = line_candidates.get('vertical', [])

        logger.info(f"[F2] F1観測: horizontal={len(h_lines)}本, vertical={len(v_lines)}本")

        # 実線/破線を分離
        h_solid = [l for l in h_lines if l.get('style') == 'solid']
        h_dashed = [l for l in h_lines if l.get('style') == 'dashed']
        v_solid = [l for l in v_lines if l.get('style') == 'solid']

        logger.info(f"[F2]   H: solid={len(h_solid)}, dashed={len(h_dashed)}")
        logger.info(f"[F2]   V: solid={len(v_solid)}")

        # ============================================
        # 2. Panel候補を確定（F1優先、なければtokens空白帯で検出）
        # ============================================
        if panel_candidates and len(panel_candidates) > 0:
            panels_input = panel_candidates
            logger.info(f"[F2] panel_candidates from F1: {len(panels_input)}パネル")
        else:
            # F1からパネルが来なかった → tokensの空白帯で検出
            panels_input = self._detect_panels_from_tokens(
                tokens, table_bbox_candidate, page_size
            )
            logger.info(f"[F2] panel_candidates from tokens: {len(panels_input)}パネル")

        # ============================================
        # 3. データ領域の推定（ヘッダー/データ分離）
        # ============================================
        data_rect, header_rect, note_areas = self._estimate_data_rect(
            h_solid, v_solid, table_bbox_candidate, page_size
        )
        result['data_rect_candidate'] = data_rect
        result['note_areas'] = note_areas

        if data_rect:
            logger.info(f"[F2] data_rect_candidate: ({data_rect['x0']:.1f}, {data_rect['y0']:.1f}) - ({data_rect['x1']:.1f}, {data_rect['y1']:.1f})")

        # ============================================
        # 4. 密集行の検出（全panel統合）- Ver 10.7: 物理密度のみ
        # ============================================
        all_panel_bboxes = [p.get('bbox') for p in panels_input if p.get('bbox')]

        # 汎用密集行検出（数値に限定しない物理条件）
        dense_row_result = self._detect_dense_rows_generic(
            tokens, data_rect, all_panel_bboxes, page_size
        )
        is_dense_generic = dense_row_result.get('is_dense', False)
        generic_row_tokens = dense_row_result.get('row_tokens', [])

        logger.info(f"[F2] 密集行判定: generic_dense={is_dense_generic}(rows={len(generic_row_tokens)})")

        # ============================================
        # 5. 行中心候補の生成（全panel共通・本丸）
        # ============================================
        if is_dense_generic:
            # 汎用密集 → 全tokensから行中心生成（新規）
            row_centers = self._generate_row_centers_from_tokens(
                generic_row_tokens, h_dashed, data_rect, page_size
            )
            result['row_centers'] = row_centers
            logger.info(f"[F2] row_centers生成: {len(row_centers)}行（密集tokensから・汎用モード）")
        else:
            row_centers = self._generate_row_centers_from_lines(
                h_solid, h_dashed, data_rect, page_size
            )
            result['row_centers'] = row_centers
            logger.info(f"[F2] row_centers生成: {len(row_centers)}行（F1線から）")

            if len(row_centers) == 0:
                result['warnings'].append("NO_ROW_CENTERS: 密集条件不成立かつF1線不足")

        # ============================================
        # 6. 行境界の生成（全panel共通・中心間中点）
        # ============================================
        row_boundaries = self._generate_row_boundaries(
            result['row_centers'],
            h_solid,
            data_rect,
            page_size
        )
        result['row_boundaries'] = row_boundaries
        logger.info(f"[F2] row_boundaries生成: {len(row_boundaries)}本")

        # ============================================
        # 7. Panelごとにcol_boundaries + gridを構築
        # ============================================
        panels_output = []
        grids_output = []

        for panel_idx, panel in enumerate(panels_input):
            panel_bbox = panel.get('bbox')
            if not panel_bbox or len(panel_bbox) != 4:
                continue

            px0, py0, px1, py1 = panel_bbox
            logger.info(f"[F2] panel[{panel_idx}]: bbox=[{px0:.1f}, {py0:.1f}, {px1:.1f}, {py1:.1f}]")

            # Panel内の縦線を抽出
            panel_v_solid = self._filter_lines_in_bbox(v_solid, panel_bbox, 'v')

            # Panel内のtokensを抽出
            panel_tokens = self._filter_tokens_in_bbox(tokens, panel_bbox)

            # col_boundariesを生成（縦線 + separator候補 + token x_center統合）
            col_boundaries, col_boundaries_candidates = self._extract_col_boundaries_for_panel(
                panel_v_solid, panel_bbox, page_size, panel_tokens=panel_tokens
            )
            logger.info(f"[F2]   col_boundaries: {len(col_boundaries)}本 (candidates={len(col_boundaries_candidates)})")

            # gridを構築
            grid = self._build_grid_for_f3(
                row_boundaries,
                col_boundaries,
                {'x0': px0, 'y0': py0, 'x1': px1, 'y1': py1, 'score': 0.8, 'source': 'panel'},
                page_size
            )

            # Panel内のtokens数をカウント（panel_tokensは上で取得済み）
            panel_token_count = len(panel_tokens)

            panel_result = {
                'panel_id': panel_idx,
                'panel_bbox': panel_bbox,
                'col_boundaries': col_boundaries,
                'col_boundaries_candidates': col_boundaries_candidates,
                'grid': grid,
                'tokens_count': panel_token_count,
                'confidence': panel.get('score', 0.5)
            }
            panels_output.append(panel_result)

            if grid:
                grids_output.append(grid)
                logger.info(f"[F2]   grid: {grid['row_count']}行 x {grid['col_count']}列 = {grid['cell_count']}セル")
            else:
                logger.info(f"[F2]   grid: None（条件不足）")

        result['panels'] = panels_output
        result['grids'] = grids_output
        result['metadata']['panel_count'] = len(panels_output)

        # has_table: 物理条件のみ（rows>=3 & cols>=2）
        # 意味判定は一切しない
        if grids_output:
            result['grid'] = grids_output[0]
            result['has_table'] = True
        elif result['row_centers'] and len(result['row_centers']) >= self.MIN_GRID_ROWS:
            # gridが構築できなくてもrow_centersが十分あればhas_table=True
            # （col_boundariesが不足でgrid構築に至らなかったケース）
            result['grid'] = None
            result['has_table'] = True
            result['warnings'].append(
                f"GRID_BUILD_FAILED_BUT_ROWS_EXIST: row_centers={len(result['row_centers'])}"
            )
            logger.info(f"[F2] has_table=True（gridなしだがrow_centers={len(result['row_centers'])}行あり）")
        else:
            result['grid'] = None
            result['has_table'] = False

        if panels_output:
            result['col_boundaries'] = panels_output[0].get('col_boundaries', [])

        logger.info(f"[F2] panel_detected: {len(panels_output)} panels")

        # ============================================
        # 8. 参考情報（bypass_metaがあれば記録）
        # ============================================
        if bypass_meta:
            result['metadata']['bypass_meta'] = bypass_meta
            if bypass_meta.get('format_id'):
                result['format_id'] = bypass_meta.get('format_id')
                result['confidence'] = bypass_meta.get('score', 0.5)

        # 完了
        elapsed = time.time() - f2_start
        result['metadata']['elapsed'] = elapsed
        self._log_result(result)

        return result

    # ============================================================
    # Panel検出（tokensの空白帯から）
    # ============================================================

    def _detect_panels_from_tokens(
        self,
        tokens: List[Dict[str, Any]],
        table_bbox_candidate: Optional[List[float]],
        page_size: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """
        tokensのx分布（空白帯）からパネルを検出する

        F1からpanel_candidatesが来なかった場合のフォールバック
        """
        if not tokens:
            return []

        page_w = page_size.get('w', 1275)
        page_h = page_size.get('h', 1800)

        # データ領域を決定
        if table_bbox_candidate and len(table_bbox_candidate) == 4:
            x0, y0, x1, y1 = table_bbox_candidate
        else:
            x0, y0, x1, y1 = 0, 0, page_w, page_h

        rect_width = x1 - x0
        if rect_width <= 0:
            return []

        # 領域内のtokensを収集
        filtered_tokens = []
        for t in tokens:
            bbox = t.get('bbox') or t.get('coords', {}).get('bbox')
            if not bbox or len(bbox) != 4:
                continue

            tx0, ty0, tx1, ty1 = bbox
            cx = (tx0 + tx1) / 2
            cy = (ty0 + ty1) / 2

            if x0 <= cx <= x1 and y0 <= cy <= y1:
                filtered_tokens.append({
                    'bbox': bbox,
                    'cx': cx
                })

        if len(filtered_tokens) < 10:
            # tokensが少ない → 1パネル
            return [{
                'bbox': [x0, y0, x1, y1],
                'score': 0.5,
                'source': 'single_from_tokens',
                'evidence': {'token_count': len(filtered_tokens)}
            }]

        # x軸ヒストグラムを作成
        bins = min(self.HISTOGRAM_BINS, int(rect_width / 2))
        bin_width = rect_width / bins
        histogram = [0] * bins

        for t in filtered_tokens:
            bin_idx = int((t['cx'] - x0) / bin_width)
            bin_idx = max(0, min(bin_idx, bins - 1))
            histogram[bin_idx] += 1

        # 空白帯を検出（histogram値が0または非常に小さい連続区間）
        threshold = max(1, len(filtered_tokens) / bins * 0.1)  # 平均の10%以下
        gaps = []
        in_gap = False
        gap_start = 0

        for i, count in enumerate(histogram):
            if count <= threshold:
                if not in_gap:
                    in_gap = True
                    gap_start = i
            else:
                if in_gap:
                    gap_end = i
                    gap_width = (gap_end - gap_start) * bin_width
                    if gap_width >= rect_width * self.GAP_WIDTH_RATIO:
                        gap_x = x0 + (gap_start + gap_end) / 2 * bin_width
                        gaps.append({
                            'x': gap_x,
                            'width': gap_width
                        })
                    in_gap = False

        logger.info(f"[F2] token空白帯検出: {len(gaps)}個")

        if not gaps:
            return [{
                'bbox': [x0, y0, x1, y1],
                'score': 0.5,
                'source': 'single_no_gaps',
                'evidence': {'histogram_bins': bins}
            }]

        # 空白帯でパネルを分割
        boundaries = [x0]
        for gap in gaps:
            boundaries.append(gap['x'])
        boundaries.append(x1)

        panels = []
        for i in range(len(boundaries) - 1):
            px0 = boundaries[i]
            px1 = boundaries[i + 1]

            panel_width = px1 - px0
            if panel_width < rect_width * 0.08:  # 8%未満は除外
                continue

            panels.append({
                'bbox': [px0, y0, px1, y1],
                'score': 0.6,
                'source': 'token_gap_split',
                'evidence': {
                    'panel_index': i,
                    'total_panels': len(boundaries) - 1
                }
            })

        return panels if panels else [{
            'bbox': [x0, y0, x1, y1],
            'score': 0.5,
            'source': 'fallback_single',
            'evidence': {}
        }]

    # ============================================================
    # ヘルパー: bbox内の線/tokensをフィルタ
    # ============================================================

    def _filter_lines_in_bbox(
        self,
        lines: List[Dict],
        bbox: List[float],
        orientation: str  # 'h' or 'v'
    ) -> List[Dict]:
        """bbox内の線をフィルタ"""
        x0, y0, x1, y1 = bbox
        tol = 5  # 許容誤差

        filtered = []
        for line in lines:
            pos = line.get('position', 0)

            if orientation == 'v':
                # 縦線: x座標がbbox内
                if x0 - tol <= pos <= x1 + tol:
                    filtered.append(line)
            else:
                # 水平線: y座標がbbox内
                if y0 - tol <= pos <= y1 + tol:
                    filtered.append(line)

        return filtered

    def _filter_tokens_in_bbox(
        self,
        tokens: List[Dict],
        bbox: List[float]
    ) -> List[Dict]:
        """bbox内のtokensをフィルタ"""
        x0, y0, x1, y1 = bbox

        filtered = []
        for t in tokens:
            t_bbox = t.get('bbox') or t.get('coords', {}).get('bbox')
            if not t_bbox or len(t_bbox) != 4:
                continue

            tx0, ty0, tx1, ty1 = t_bbox
            cx = (tx0 + tx1) / 2
            cy = (ty0 + ty1) / 2

            if x0 <= cx <= x1 and y0 <= cy <= y1:
                filtered.append(t)

        return filtered

    # ============================================================
    # col_boundaries の抽出（panelごと）
    # ============================================================

    def _extract_col_boundaries_for_panel(
        self,
        v_solid: List[Dict],
        panel_bbox: List[float],
        page_size: Dict[str, int],
        panel_tokens: Optional[List[Dict]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Panel内の列境界を統合的に抽出（Ver 10.6: 3ソース統合）

        材料:
        1. F1の v_solid（縦実線）
        2. F1の separator_candidates（ガター含む）
        3. panel内 tokens の x_center 分布の密度谷

        Returns:
            (col_boundaries, col_boundaries_candidates)
            - col_boundaries: 採用した境界
            - col_boundaries_candidates: 不採用含む全候補
        """
        px0, py0, px1, py1 = panel_bbox
        panel_width = px1 - px0

        all_candidates = []

        # ソース1: Panel内の縦線
        for line in v_solid:
            x = line.get('position', 0)
            score = line.get('score', 0.5)
            all_candidates.append({
                'x': x,
                'score': score,
                'source': 'f1_solid'
            })

        # ソース2: F1の separator候補（panel内のもの）
        for sep in self._separator_candidates_all:
            sep_x = sep.get('x', 0)
            # panel内にあるか
            if px0 + 5 < sep_x < px1 - 5:
                # 既に v_solid で拾っている可能性があるので重複チェック
                is_dup = any(abs(c['x'] - sep_x) < 5 for c in all_candidates)
                if not is_dup:
                    score = sep.get('separator_strength', 0.3) * 0.8  # separatorとしてのscoreを少し下げる
                    all_candidates.append({
                        'x': sep_x,
                        'score': score,
                        'source': f"separator_{sep.get('source', 'unknown')}"
                    })

        # ソース3: token x_center の密度谷（panel内）
        if panel_tokens and len(panel_tokens) >= 5 and panel_width > 0:
            token_gap_boundaries = self._detect_token_x_gaps(
                panel_tokens, px0, px1, panel_width
            )
            for tgb in token_gap_boundaries:
                # 既存候補と重複チェック
                is_dup = any(abs(c['x'] - tgb['x']) < max(8, panel_width * 0.01) for c in all_candidates)
                if not is_dup:
                    all_candidates.append(tgb)

        # 近傍統合（同一クラスタの候補を代表に縮約）
        merge_tol = max(5, panel_width * 0.008)
        boundaries = self._merge_col_candidates(all_candidates, merge_tol)

        # panel端を確保
        if not boundaries:
            boundaries.append({'x': px0, 'score': 0.3, 'source': 'panel_edge_left'})
            boundaries.append({'x': px1, 'score': 0.3, 'source': 'panel_edge_right'})
        else:
            min_x = min(b['x'] for b in boundaries)
            if min_x > px0 + 5:
                boundaries.append({'x': px0, 'score': 0.3, 'source': 'panel_edge_left'})
            max_x = max(b['x'] for b in boundaries)
            if max_x < px1 - 5:
                boundaries.append({'x': px1, 'score': 0.3, 'source': 'panel_edge_right'})

        boundaries.sort(key=lambda b: b['x'])

        logger.info(f"[F2]   col_boundaries統合: candidates={len(all_candidates)}, merged={len(boundaries)}")

        return boundaries, all_candidates

    def _detect_token_x_gaps(
        self,
        tokens: List[Dict],
        px0: float,
        px1: float,
        panel_width: float
    ) -> List[Dict[str, Any]]:
        """
        token x_center 分布の密度谷から列境界候補を検出

        ガターと同じ思想: x_center のヒストグラムを作り、
        トークンが疎な領域（谷）を列境界とする
        """
        # x_center を収集
        x_centers = []
        for t in tokens:
            bbox = t.get('bbox') or t.get('coords', {}).get('bbox')
            if not bbox or len(bbox) != 4:
                continue
            cx = (bbox[0] + bbox[2]) / 2
            if px0 <= cx <= px1:
                x_centers.append(cx)

        if len(x_centers) < 5:
            return []

        # ヒストグラム作成
        bins = min(200, max(20, int(panel_width / 3)))
        bin_width = panel_width / bins
        histogram = [0] * bins

        for xc in x_centers:
            bin_idx = int((xc - px0) / bin_width)
            bin_idx = max(0, min(bin_idx, bins - 1))
            histogram[bin_idx] += 1

        # 平滑化
        hist_array = np.array(histogram, dtype=float)
        window = max(3, bins // 30)
        if len(hist_array) > window:
            kernel = np.ones(window) / window
            hist_smooth = np.convolve(hist_array, kernel, mode='same')
        else:
            hist_smooth = hist_array

        # 谷（低密度領域）を検出
        mean_density = np.mean(hist_smooth)
        threshold = mean_density * 0.2  # 平均の20%以下

        gap_boundaries = []
        in_gap = False
        gap_start = 0

        for i, count in enumerate(hist_smooth):
            if count <= threshold:
                if not in_gap:
                    in_gap = True
                    gap_start = i
            else:
                if in_gap:
                    gap_end = i
                    gap_width = (gap_end - gap_start) * bin_width
                    # 最小幅チェック
                    if gap_width >= panel_width * 0.015:
                        gap_center_x = px0 + (gap_start + gap_end) / 2 * bin_width
                        # scoreはgap幅に比例
                        score = min(0.6, gap_width / (panel_width * 0.05) * 0.3)
                        gap_boundaries.append({
                            'x': gap_center_x,
                            'score': score,
                            'source': 'token_x_gap'
                        })
                    in_gap = False

        return gap_boundaries

    def _merge_col_candidates(
        self,
        candidates: List[Dict],
        merge_tol: float
    ) -> List[Dict]:
        """
        列境界候補を近傍統合して代表を選ぶ

        同じクラスタ内では score 最大のものを代表とする
        """
        if not candidates:
            return []

        sorted_cands = sorted(candidates, key=lambda c: c['x'])
        clusters = []
        current = [sorted_cands[0]]

        for c in sorted_cands[1:]:
            if c['x'] - current[-1]['x'] <= merge_tol:
                current.append(c)
            else:
                clusters.append(current)
                current = [c]
        clusters.append(current)

        merged = []
        for cluster in clusters:
            best = max(cluster, key=lambda c: c['score'])
            # 代表のxをscore加重平均にする（安定化）
            total_score = sum(c['score'] for c in cluster)
            if total_score > 0:
                weighted_x = sum(c['x'] * c['score'] for c in cluster) / total_score
            else:
                weighted_x = best['x']

            sources = list(set(c['source'] for c in cluster))
            merged.append({
                'x': weighted_x,
                'score': best['score'],
                'source': best['source'] if len(sources) == 1 else f"merged({','.join(sources)})",
                'cluster_size': len(cluster)
            })

        return merged

    # ============================================================
    # 2. データ領域の推定
    # ============================================================

    def _estimate_data_rect(
        self,
        h_solid: List[Dict],
        v_solid: List[Dict],
        table_bbox_candidate: Optional[List[float]],
        page_size: Dict[str, int]
    ) -> Tuple[Optional[Dict], Optional[Dict], List[Dict]]:
        """データ領域とヘッダー領域を推定する"""
        note_areas = []

        if table_bbox_candidate and len(table_bbox_candidate) == 4:
            x0, y0, x1, y1 = table_bbox_candidate

            header_bottom = None
            h_positions = sorted([l['position'] for l in h_solid])

            if len(h_positions) >= 2:
                header_threshold = y0 + (y1 - y0) * 0.3
                for pos in h_positions[1:]:
                    if pos < header_threshold:
                        header_bottom = pos
                        break

            if header_bottom:
                header_rect = {
                    'x0': x0, 'y0': y0, 'x1': x1, 'y1': header_bottom,
                    'score': 0.8, 'source': 'f1_lines'
                }
                data_rect = {
                    'x0': x0, 'y0': header_bottom, 'x1': x1, 'y1': y1,
                    'score': 0.8, 'source': 'f1_lines'
                }
            else:
                header_rect = None
                data_rect = {
                    'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                    'score': 0.6, 'source': 'f1_bbox'
                }

            return data_rect, header_rect, note_areas

        if h_solid and v_solid:
            h_positions = sorted([l['position'] for l in h_solid])
            v_positions = sorted([l['position'] for l in v_solid])

            data_rect = {
                'x0': v_positions[0],
                'y0': h_positions[0],
                'x1': v_positions[-1],
                'y1': h_positions[-1],
                'score': 0.5,
                'source': 'f1_lines_only'
            }
            return data_rect, None, note_areas

        return None, None, note_areas

    # ============================================================
    # 3. 密集数値行の検出（複数panel対応）
    # ============================================================

    def _detect_dense_rows_generic(
        self,
        tokens: List[Dict[str, Any]],
        data_rect: Optional[Dict],
        panel_bboxes: List[List[float]],
        page_size: Dict[str, int]
    ) -> Dict[str, Any]:
        """
        汎用密集行検出（数値に限定しない物理条件）

        判定条件（物理のみ）:
        1. 同一y帯にtokensが帯状に並ぶ（行ごとのtoken数が多い）
        2. y_center間隔が規則的（CV小）
        3. 十分な行数がある

        Returns:
            {is_dense, row_tokens, evidence}
        """
        result = {
            'is_dense': False,
            'row_tokens': [],
            'evidence': ''
        }

        if not tokens:
            return result

        # 全panelのtokensを収集
        all_tokens = []
        for panel_bbox in panel_bboxes:
            if not panel_bbox or len(panel_bbox) != 4:
                continue
            px0, py0, px1, py1 = panel_bbox

            for t in tokens:
                bbox = t.get('bbox') or t.get('coords', {}).get('bbox')
                if not bbox or len(bbox) != 4:
                    continue
                tx0, ty0, tx1, ty1 = bbox
                cx = (tx0 + tx1) / 2
                cy = (ty0 + ty1) / 2
                height = ty1 - ty0

                if px0 <= cx <= px1 and py0 <= cy <= py1:
                    all_tokens.append({
                        'text': (t.get('text') or '').strip(),
                        'bbox': bbox,
                        'center_y': cy,
                        'center_x': cx,
                        'height': height
                    })

        if len(all_tokens) < self.MIN_DENSE_ROWS * 2:
            result['evidence'] = f'too_few_tokens:{len(all_tokens)}'
            return result

        # token高さの中央値
        heights = [t['height'] for t in all_tokens if t['height'] > 0]
        if not heights:
            return result
        median_height = np.median(heights)
        merge_threshold = median_height * self.CLUSTER_MERGE_RATIO

        # y_center でクラスタリング（行の検出）
        sorted_tokens = sorted(all_tokens, key=lambda t: t['center_y'])
        rows = []
        current_row = [sorted_tokens[0]]

        for t in sorted_tokens[1:]:
            if t['center_y'] - current_row[-1]['center_y'] <= merge_threshold:
                current_row.append(t)
            else:
                rows.append(current_row)
                current_row = [t]
        rows.append(current_row)

        # 行ごとのtoken数を確認
        row_sizes = [len(r) for r in rows]

        if len(rows) < self.MIN_DENSE_ROWS:
            result['evidence'] = f'too_few_rows:{len(rows)}'
            return result

        # 行ごとのtoken数の中央値
        median_row_size = np.median(row_sizes)

        # 多くの行がtoken数2以上であること（帯状に並んでいる）
        rows_with_multiple = sum(1 for s in row_sizes if s >= 2)
        multi_ratio = rows_with_multiple / len(rows)

        if multi_ratio < 0.5:
            result['evidence'] = f'sparse_rows:multi_ratio={multi_ratio:.2f}'
            return result

        # y_center間隔の規則性チェック
        row_centers_y = [np.median([t['center_y'] for t in r]) for r in rows]
        if len(row_centers_y) >= 3:
            intervals = [row_centers_y[i+1] - row_centers_y[i] for i in range(len(row_centers_y)-1)]
            mean_interval = np.mean(intervals)
            std_interval = np.std(intervals)
            cv = std_interval / mean_interval if mean_interval > 0 else 1.0

            # CVが閾値以下、またはtoken数が多い行が十分ある
            if cv <= self.Y_INTERVAL_CV_THRESHOLD * 1.5 or (multi_ratio >= 0.7 and len(rows) >= 8):
                # 全tokensをrow_tokensとして返す
                result['is_dense'] = True
                result['row_tokens'] = all_tokens
                result['evidence'] = (
                    f'generic_dense:rows={len(rows)},cv={cv:.2f},'
                    f'multi_ratio={multi_ratio:.2f},median_row_size={median_row_size:.1f}'
                )
                logger.info(
                    f"[F2] 汎用密集行検出成功: rows={len(rows)}, cv={cv:.2f}, "
                    f"multi_ratio={multi_ratio:.2f}, median_row_size={median_row_size:.1f}"
                )
                return result

            result['evidence'] = f'high_cv:{cv:.2f},multi_ratio={multi_ratio:.2f}'

        return result

    # ============================================================
    # 4. 行中心候補の生成
    # ============================================================

    def _generate_row_centers_from_tokens(
        self,
        numeric_tokens: List[Dict],
        h_dashed: List[Dict],
        data_rect: Optional[Dict],
        page_size: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """数値tokensからrow_centersを生成する（クラスタリング）"""
        if not numeric_tokens:
            return []

        heights = [t['height'] for t in numeric_tokens if t['height'] > 0]
        if not heights:
            return []

        median_height = np.median(heights)
        merge_threshold = median_height * self.CLUSTER_MERGE_RATIO

        logger.info(f"[F2] row_centers生成: median_height={median_height:.1f}, merge_threshold={merge_threshold:.1f}")

        centers_y = sorted([t['center_y'] for t in numeric_tokens])

        clusters = []
        current_cluster = [centers_y[0]]

        for y in centers_y[1:]:
            if y - current_cluster[-1] <= merge_threshold:
                current_cluster.append(y)
            else:
                clusters.append(current_cluster)
                current_cluster = [y]
        clusters.append(current_cluster)

        dashed_positions = [l['position'] for l in h_dashed]

        row_centers = []
        for i, cluster in enumerate(clusters):
            center_y = np.median(cluster)
            cluster_size = len(cluster)

            score = min(1.0, cluster_size / 5.0)

            evidence = []
            for dash_y in dashed_positions:
                if abs(center_y - dash_y) <= merge_threshold:
                    evidence.append(f'guide_match:{dash_y:.1f}')
                    score = min(1.0, score + 0.2)
                    break

            row_centers.append({
                'y': center_y,
                'score': score,
                'source': 'token_cluster',
                'evidence': evidence,
                'cluster_size': cluster_size
            })

        logger.info(f"[F2] クラスタリング結果: {len(clusters)}クラスタ → {len(row_centers)}行")

        return row_centers

    def _generate_row_centers_from_lines(
        self,
        h_solid: List[Dict],
        h_dashed: List[Dict],
        data_rect: Optional[Dict],
        page_size: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """F1の線からrow_centersを生成する"""
        row_centers = []

        for line in h_solid:
            y = line.get('position', 0)
            score = line.get('score', 0.5)
            row_centers.append({
                'y': y,
                'score': score,
                'source': 'f1_solid',
                'evidence': ['solid_line']
            })

        for line in h_dashed:
            y = line.get('position', 0)
            score = line.get('score', 0.3) * 0.8
            row_centers.append({
                'y': y,
                'score': score,
                'source': 'f1_dashed',
                'evidence': ['dashed_guide']
            })

        row_centers.sort(key=lambda r: r['y'])
        return row_centers

    # ============================================================
    # 5. 行境界の生成（中心間中点）
    # ============================================================

    def _generate_row_boundaries(
        self,
        row_centers: List[Dict],
        h_solid: List[Dict],
        data_rect: Optional[Dict],
        page_size: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """row_centersから行境界を生成する"""
        if not row_centers:
            return []

        sorted_centers = sorted(row_centers, key=lambda r: r['y'])
        solid_y_positions = sorted([l['position'] for l in h_solid])

        if len(sorted_centers) >= 2:
            pitches = [sorted_centers[i+1]['y'] - sorted_centers[i]['y']
                      for i in range(len(sorted_centers)-1)]
            median_pitch = np.median(pitches)
        else:
            median_pitch = 20

        logger.info(f"[F2] row_boundaries生成: {len(sorted_centers)}行, median_pitch={median_pitch:.1f}")

        boundaries = []

        top_center_y = sorted_centers[0]['y']
        top_boundary = self._find_edge_boundary(
            top_center_y, median_pitch, solid_y_positions, 'top', data_rect
        )
        boundaries.append(top_boundary)

        for i in range(len(sorted_centers) - 1):
            y1 = sorted_centers[i]['y']
            y2 = sorted_centers[i+1]['y']
            midpoint = (y1 + y2) / 2

            boundaries.append({
                'y': midpoint,
                'score': 0.8,
                'source': 'center_midpoint',
                'evidence': [f'between:{y1:.1f}-{y2:.1f}']
            })

        bottom_center_y = sorted_centers[-1]['y']
        bottom_boundary = self._find_edge_boundary(
            bottom_center_y, median_pitch, solid_y_positions, 'bottom', data_rect
        )
        boundaries.append(bottom_boundary)

        return boundaries

    def _find_edge_boundary(
        self,
        center_y: float,
        pitch: float,
        solid_y_positions: List[float],
        edge_type: str,
        data_rect: Optional[Dict]
    ) -> Dict[str, Any]:
        """端境界を決定する（実線優先）"""
        if edge_type == 'top':
            candidates = [y for y in solid_y_positions if y < center_y]
            if candidates:
                nearest = max(candidates)
                if abs(center_y - nearest) <= pitch * 1.5:
                    return {
                        'y': nearest,
                        'score': 0.9,
                        'source': 'f1_solid_edge',
                        'evidence': ['top_solid_line']
                    }

            estimated_y = center_y - pitch / 2
            if data_rect:
                estimated_y = max(estimated_y, data_rect['y0'])
            return {
                'y': estimated_y,
                'score': 0.5,
                'source': 'estimated',
                'evidence': ['top_estimated']
            }

        else:
            candidates = [y for y in solid_y_positions if y > center_y]
            if candidates:
                nearest = min(candidates)
                if abs(nearest - center_y) <= pitch * 1.5:
                    return {
                        'y': nearest,
                        'score': 0.9,
                        'source': 'f1_solid_edge',
                        'evidence': ['bottom_solid_line']
                    }

            estimated_y = center_y + pitch / 2
            if data_rect:
                estimated_y = min(estimated_y, data_rect['y1'])
            return {
                'y': estimated_y,
                'score': 0.5,
                'source': 'estimated',
                'evidence': ['bottom_estimated']
            }

    # ============================================================
    # 6. F3向けgridの構築
    # ============================================================

    def _build_grid_for_f3(
        self,
        row_boundaries: List[Dict],
        col_boundaries: List[Dict],
        data_rect: Optional[Dict],
        page_size: Dict[str, int]
    ) -> Optional[Dict[str, Any]]:
        """F3向けのgridを構築する"""
        if len(row_boundaries) < 2 or len(col_boundaries) < 2:
            return None

        rows = sorted(row_boundaries, key=lambda r: r['y'])
        cols = sorted(col_boundaries, key=lambda c: c['x'])

        row_count = len(rows) - 1
        col_count = len(cols) - 1

        if row_count < self.MIN_GRID_ROWS or col_count < self.MIN_GRID_COLS:
            return None

        cells = []
        for r in range(row_count):
            for c in range(col_count):
                y0 = rows[r]['y']
                y1 = rows[r + 1]['y']
                x0 = cols[c]['x']
                x1 = cols[c + 1]['x']

                cells.append({
                    'row': r,
                    'col': c,
                    'bbox': [x0, y0, x1, y1]
                })

        grid = {
            'row_count': row_count,
            'col_count': col_count,
            'cell_count': len(cells),
            'cells': cells,
            'row_boundaries': rows,
            'col_boundaries': cols,
            'page_size': page_size,
            'source': 'f2_geometry'
        }

        if data_rect:
            grid['data_rect'] = data_rect

        return grid

    # ============================================================
    # ログ出力
    # ============================================================

    def _log_result(self, result: Dict[str, Any]):
        """結果をログ出力"""
        logger.info("[F2] ===== 生成物ログ開始 =====")
        logger.info(f"[F2] has_table: {result['has_table']}")
        logger.info(f"[F2] panel_count: {result['metadata'].get('panel_count', 0)}")

        if result['data_rect_candidate']:
            dr = result['data_rect_candidate']
            logger.info(f"[F2] data_rect_candidate: ({dr['x0']:.1f}, {dr['y0']:.1f}) - ({dr['x1']:.1f}, {dr['y1']:.1f})")

        logger.info(f"[F2] row_centers: {len(result['row_centers'])}行（全panel共通）")
        for i, rc in enumerate(result['row_centers'][:10]):
            logger.info(f"[F2]   row[{i}]: y={rc['y']:.1f}, score={rc['score']:.2f}, source={rc['source']}")
        if len(result['row_centers']) > 10:
            logger.info(f"[F2]   ... and {len(result['row_centers']) - 10} more")

        logger.info(f"[F2] row_boundaries: {len(result['row_boundaries'])}本（全panel共通）")

        logger.info(f"[F2] panels: {len(result['panels'])}パネル")
        for panel in result['panels']:
            pb = panel.get('panel_bbox', [0, 0, 0, 0])
            col_count = len(panel.get('col_boundaries', []))
            grid = panel.get('grid')
            grid_info = f"{grid['row_count']}x{grid['col_count']}" if grid else "None"
            logger.info(f"[F2]   panel[{panel['panel_id']}]: bbox=[{pb[0]:.1f},{pb[1]:.1f},{pb[2]:.1f},{pb[3]:.1f}], cols={col_count}, grid={grid_info}")

        if result['warnings']:
            logger.info(f"[F2] warnings: {result['warnings']}")

        logger.info(f"[F2] elapsed: {result['metadata'].get('elapsed', 0):.2f}s")
        logger.info("[F2] ===== 生成物ログ終了 =====")

    # ============================================================
    # 互換性のために残すメソッド
    # ============================================================

    def extract_headers_from_structure(
        self,
        grid: Dict[str, Any],
        structure: Dict[str, Any],
        tokens: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """F3への橋渡し用（互換性維持）"""
        table_bbox = None
        if grid and grid.get('cells'):
            cells = grid['cells']
            x0 = min(c['bbox'][0] for c in cells)
            y0 = min(c['bbox'][1] for c in cells)
            x1 = max(c['bbox'][2] for c in cells)
            y1 = max(c['bbox'][3] for c in cells)
            table_bbox = [x0, y0, x1, y1]

        x_headers = [''] * grid.get('col_count', 0) if grid else []
        y_headers = []

        return {
            'x_headers': x_headers,
            'y_headers': y_headers,
            'header_coords': {},
            'table_bbox': table_bbox,
            'structure': structure
        }
