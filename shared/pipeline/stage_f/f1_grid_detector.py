"""
F1: 罫線観測（Line Observer）

【Ver 10.7】候補全件保持 + 座標系ピクセル統一 + data_rectマスク
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
F1は「線を観測する」だけ。grid構築・表判定はF2以降の仕事。
**短い線も破棄せず、is_short フラグで残す**
**separator候補も弾かず全件保持（is_strong フラグで強弱を区別）**

【Ver 10.7 変更点】
- separator候補を全件保持（弾かない）
- ガター検出: 弱い谷もscoreを下げて残す
- data_rect: 解析対象領域外を物理マスクしてから線検出
- Aルート: PDF座標を即座にピクセル座標へ変換（E8との座標統一）
- 全出力がピクセル座標系で統一される

入力:
  - pdf_path: PDFファイルパス（Aルート用）
  - page_image: ページ画像（Bルート用）
  - page_num: ページ番号
  - page_size: {'w': int, 'h': int}

出力（観測事実のみ）:
  - line_candidates: {horizontal: [...], vertical: [...]}
  - table_bbox_candidate: [x0, y0, x1, y1] or None
  - panel_candidates: [{bbox, score, source, evidence}, ...]
  - separator_candidates_all: [全separator候補（弱いものも含む）]
  - separator_candidates_ranked: [score降順]
  - panel_split_hypotheses: [パネル分割仮説（複数案）]
  - page_size: {'w': int, 'h': int}
  - source: 'vector' | 'image' | 'none'
  - warnings: List[str]
  - metadata: {page_num, elapsed, scale_x, scale_y}

【禁止事項】
  - grid構築（F2の仕事）
  - has_table判定（F2の仕事）
  - 意味付け（内容やドメインに基づく分類）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


class F1GridDetector:
    """F1: 罫線観測（観測専用・grid構築しない）- Ver 10.7: ピクセル座標統一 + data_rectマスク"""

    # 最小線分長（ページ幅/高さの割合）- is_short判定の閾値
    MIN_LINE_RATIO = 0.03
    # 線分のグループ化閾値（ピクセル）
    LINE_GROUP_THRESHOLD = 5
    # solid/dashed 判定の密度閾値
    SOLID_DENSITY_THRESHOLD = 0.75

    # 絶対最小線長（これ未満は本当にノイズ）
    ABSOLUTE_MIN_LINE_LENGTH = 10  # px

    # ガイド線候補の判定閾値（Ver 10.4）
    GUIDE_LINE_DASHINESS_THRESHOLD = 0.3  # dashiness >= 0.3 でガイド候補
    GUIDE_LINE_COVERAGE_MAX = 0.85        # coverage < 0.85 でガイド候補

    # Panel検出用閾値（Ver 10.6: 候補全件保持）
    SEPARATOR_MIN_SPAN_RATIO = 0.45      # 高スコア判定の span_ratio 閾値
    SEPARATOR_MIN_STRENGTH = 0.55        # 高スコア判定の separator_strength 閾値
    SEPARATOR_WEAK_SPAN_RATIO = 0.15     # 弱候補の最低 span_ratio（これ未満は本当にノイズ）
    SEPARATOR_WEAK_STRENGTH = 0.10       # 弱候補の最低 strength（これ未満は本当にノイズ）
    SEPARATOR_SEGMENT_BONUS = 0.10       # segment_count >= 3 のボーナス
    SEPARATOR_CV_THRESHOLD = 0.25        # 等間隔判定の CV 閾値
    SEPARATOR_CV_BONUS = 0.10            # 等間隔ボーナス
    MIN_PANEL_WIDTH_RATIO = 0.12         # パネル最小幅（表幅の割合）

    # Whitespace/Gutter検出用閾値（Ver 10.6）
    GUTTER_MIN_WIDTH_RATIO = 0.015       # ガター最小幅（表幅の割合）
    GUTTER_MAX_DENSITY = 0.05            # ガター内最大インク密度（高スコア谷判定）
    GUTTER_WEAK_MAX_DENSITY = 0.12       # 弱候補の最大インク密度（これ以下で弱候補に残す）
    GUTTER_DEPTH_MIN = 0.15              # 谷の深さ最小値（高スコア判定）
    GUTTER_WEAK_DEPTH_MIN = 0.05         # 弱候補の谷の深さ最小値
    GUTTER_BORDER_MARGIN_RATIO = 0.08    # 外枠マージン（表幅の割合）

    # 座標系チェック閾値
    SCALE_SKEW_WARN_THRESHOLD = 0.15     # |scale_x - scale_y| / max がこれ以上で警告

    def __init__(self, document_ai_client=None):
        """
        Args:
            document_ai_client: 未使用（互換性のため残す）
        """
        pass

    def detect(
        self,
        pdf_path: Optional[Path] = None,
        page_image: Optional[Any] = None,
        page_num: int = 0,
        page_size: Optional[Dict[str, int]] = None,
        data_rect: Optional[Dict] = None,
        use_form_parser: bool = False  # 未使用（互換性）
    ) -> Dict[str, Any]:
        """
        罫線を観測して line_candidates を返す（Ver 10.7: ピクセル座標統一）

        Args:
            pdf_path: PDFファイルパス（Aルート用）
            page_image: ページ画像（PIL Image or numpy array）（Bルート用）
            page_num: ページ番号（0始まり）
            page_size: ページサイズ {'w': int, 'h': int}（ピクセル）
            data_rect: 解析対象領域 {'x0','y0','x1','y1'}（ピクセル座標）

        Returns:
            観測結果（全てピクセル座標系）
        """
        f1_start = time.time()
        logger.info(f"[F1] 罫線観測開始 (page={page_num})")

        result = {
            'line_candidates': {
                'horizontal': [],
                'vertical': []
            },
            'table_bbox_candidate': None,
            'panel_candidates': [],  # パネル候補
            'separator_candidates_all': [],    # 全separator候補（弱いものも含む）
            'separator_candidates_ranked': [], # score順にソート済み
            'panel_split_hypotheses': [],      # パネル分割仮説（複数案）
            'page_size': page_size,
            'source': 'none',
            'warnings': [],
            'metadata': {
                'page_num': page_num,
                'elapsed': 0,
                'scale_x': None,
                'scale_y': None
            }
        }

        # ============================================
        # Aルート: PDF vector抽出（最優先）
        # ============================================
        if pdf_path and pdf_path.exists():
            try:
                vector_result = self._observe_from_pdf_vector(pdf_path, page_num, page_size)
                if vector_result:
                    result.update(vector_result)
                    result['source'] = 'vector'
                    logger.info(f"[F1] Aルート成功: vector観測")

                    # Ver 10.7: PDF座標 → ピクセル座標に即時変換
                    pdf_ps = result.get('pdf_page_size')
                    sx, sy = None, None
                    if pdf_ps and pdf_ps.get('w') and pdf_ps.get('h'):
                        # 画像サイズの取得（page_image > page_size の優先順）
                        iw, ih = None, None
                        if page_image is not None:
                            try:
                                if hasattr(page_image, 'size'):
                                    iw, ih = page_image.size
                                elif hasattr(page_image, 'shape'):
                                    ih, iw = page_image.shape[:2]
                            except Exception:
                                pass
                        if iw is None and page_size:
                            iw = page_size.get('w')
                            ih = page_size.get('h')

                        if iw and ih:
                            sx = iw / pdf_ps['w']
                            sy = ih / pdf_ps['h']
                            result['metadata']['scale_x'] = sx
                            result['metadata']['scale_y'] = sy
                            max_s = max(sx, sy)
                            if max_s > 0 and abs(sx - sy) / max_s > self.SCALE_SKEW_WARN_THRESHOLD:
                                result['warnings'].append(
                                    f"SCALE_SKEW: scale_x={sx:.3f}, scale_y={sy:.3f}"
                                )

                            # PDF座標 → ピクセル座標に変換
                            self._convert_to_pixel_coords(result, sx, sy)
                            logger.info(f"[F1] Aルート: PDF→ピクセル変換完了 (scale_x={sx:.3f}, scale_y={sy:.3f})")

                    # Panel候補を生成（Ver 10.7: 既にピクセル座標系なのでpdf_page_size=None）
                    panel_detection = self._detect_panel_candidates(
                        result['line_candidates'],
                        result['table_bbox_candidate'],
                        result['page_size'],
                        page_image=page_image,
                        pdf_page_size=None  # 既にピクセル座標
                    )
                    result['panel_candidates'] = panel_detection['panels']
                    result['separator_candidates_all'] = panel_detection['separator_candidates_all']
                    result['separator_candidates_ranked'] = panel_detection['separator_candidates_ranked']
                    result['panel_split_hypotheses'] = panel_detection['panel_split_hypotheses']

                    result['metadata']['elapsed'] = time.time() - f1_start
                    self._log_observation(result)
                    return result
            except Exception as e:
                logger.warning(f"[F1] Aルート失敗: {e}")
                result['warnings'].append(f"VECTOR_ERROR: {str(e)}")

        # ============================================
        # Bルート: 画像罫線検出（フォールバック）
        # ============================================
        if page_image is not None:
            try:
                # Ver 10.7: data_rect外を物理マスク
                masked_image = self._mask_outside_rect(page_image, data_rect)
                image_result = self._observe_from_image(masked_image, page_size)
                if image_result:
                    result.update(image_result)
                    result['source'] = 'image'
                    logger.info(f"[F1] Bルート成功: image観測")

                    # Panel候補を生成（Bルートは画像座標系なのでpdf_page_size=None）
                    panel_detection = self._detect_panel_candidates(
                        result['line_candidates'],
                        result['table_bbox_candidate'],
                        result['page_size'],
                        page_image=page_image,
                        pdf_page_size=None
                    )
                    result['panel_candidates'] = panel_detection['panels']
                    result['separator_candidates_all'] = panel_detection['separator_candidates_all']
                    result['separator_candidates_ranked'] = panel_detection['separator_candidates_ranked']
                    result['panel_split_hypotheses'] = panel_detection['panel_split_hypotheses']

                    result['metadata']['elapsed'] = time.time() - f1_start
                    self._log_observation(result)
                    return result
            except Exception as e:
                logger.warning(f"[F1] Bルート失敗: {e}")
                result['warnings'].append(f"IMAGE_ERROR: {str(e)}")

        # 全ルート失敗
        logger.info(f"[F1] 罫線観測: 線なし")
        result['metadata']['elapsed'] = time.time() - f1_start
        return result

    # ============================================================
    # Ver 10.7: 座標変換・マスクユーティリティ
    # ============================================================

    def _mask_outside_rect(self, image: Any, rect: Optional[Dict]) -> Any:
        """指定領域(data_rect)外を黒塗りする物理マスク（Ver 10.7）"""
        if rect is None:
            return image
        if cv2 is None:
            return image

        # numpy配列に変換
        if hasattr(image, 'convert'):
            img = np.array(image.convert('RGB'))
        elif isinstance(image, np.ndarray):
            img = image.copy()
        else:
            return image

        h, w = img.shape[:2]
        x0 = max(0, int(rect.get('x0', 0)))
        y0 = max(0, int(rect.get('y0', 0)))
        x1 = min(w, int(rect.get('x1', w)))
        y1 = min(h, int(rect.get('y1', h)))

        # マスク作成: 領域内を白(255)、外を黒(0)
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[y0:y1, x0:x1] = 255

        # 領域外を黒塗り
        if len(img.shape) == 3:
            img[mask == 0] = 0
        else:
            img[mask == 0] = 0

        logger.info(f"[F1] data_rect マスク適用: rect=[{x0},{y0},{x1},{y1}], img={w}x{h}")
        return img

    def _convert_to_pixel_coords(self, result: Dict[str, Any], scale_x: float, scale_y: float):
        """
        Aルート結果のPDF座標をピクセル座標に変換（in-place）（Ver 10.7）

        Args:
            result: detect()の結果dict
            scale_x: img_w / pdf_w
            scale_y: img_h / pdf_h
        """
        # line_candidates の変換
        for direction in ['horizontal', 'vertical']:
            for line in result.get('line_candidates', {}).get(direction, []):
                if direction == 'horizontal':
                    # 水平線: position=Y座標, span_start/span_end=X座標
                    line['position'] *= scale_y
                    line['span_start'] *= scale_x
                    line['span_end'] *= scale_x
                    line['span'] = line['span_end'] - line['span_start']
                else:
                    # 垂直線: position=X座標, span_start/span_end=Y座標
                    line['position'] *= scale_x
                    line['span_start'] *= scale_y
                    line['span_end'] *= scale_y
                    line['span'] = line['span_end'] - line['span_start']

        # table_bbox_candidate の変換
        bbox = result.get('table_bbox_candidate')
        if bbox and len(bbox) == 4:
            result['table_bbox_candidate'] = [
                bbox[0] * scale_x,
                bbox[1] * scale_y,
                bbox[2] * scale_x,
                bbox[3] * scale_y
            ]

    # ============================================================
    # Panel候補検出
    # ============================================================

    def _detect_panel_candidates(
        self,
        line_candidates: Dict[str, List[Dict]],
        table_bbox_candidate: Optional[List[float]],
        page_size: Optional[Dict[str, int]],
        page_image: Optional[Any] = None,
        pdf_page_size: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        """
        「横並びのパネル（折り返し表）」を切り出す（Ver 10.6: 候補全件保持）

        【戦略】
        2系統の separator を統合:
        1. Line-based separators: 縦線の separator_strength ベース（従来）
        2. Whitespace-based separators: インク密度の谷（ガター）で検出

        【変更点 Ver 10.6】
        - separator候補を弾かず全件保持（separator_candidates_all）
        - score順にランク付け（separator_candidates_ranked）
        - パネル分割仮説を複数生成（panel_split_hypotheses）

        Returns:
            {
                'panels': [{bbox, score, source, evidence}, ...],
                'separator_candidates_all': [...],
                'separator_candidates_ranked': [...],
                'panel_split_hypotheses': [...]
            }
        """
        empty_result = {
            'panels': [],
            'separator_candidates_all': [],
            'separator_candidates_ranked': [],
            'panel_split_hypotheses': []
        }

        if not page_size:
            return empty_result

        page_w = page_size.get('w', 1000)
        page_h = page_size.get('h', 1414)

        v_lines = line_candidates.get('vertical', [])
        h_lines = line_candidates.get('horizontal', [])

        # table_bbox がなければ線分から推定
        if table_bbox_candidate and len(table_bbox_candidate) == 4:
            x0, y0, x1, y1 = table_bbox_candidate
        else:
            # 水平線の範囲から推定、なければページ全体
            if h_lines:
                h_solid = [l for l in h_lines if l.get('style') == 'solid']
                if h_solid:
                    y0 = min(l['position'] for l in h_solid)
                    y1 = max(l['position'] for l in h_solid)
                else:
                    y0, y1 = 0, page_h
            else:
                y0, y1 = 0, page_h

            if v_lines:
                v_solid = [l for l in v_lines if l.get('style') == 'solid']
                if v_solid:
                    x0 = min(l['position'] for l in v_solid)
                    x1 = max(l['position'] for l in v_solid)
                else:
                    x0, x1 = 0, page_w
            else:
                x0, x1 = 0, page_w

        table_height = y1 - y0
        table_width = x1 - x0

        if table_height <= 0 or table_width <= 0:
            return empty_result

        # ============================================
        # 外枠除外用マージンの動的計算（Ver 10.4: ヒストグラム方式）
        # ============================================
        border_margin = self._estimate_border_margin(v_lines, x0, x1, table_width)
        logger.debug(f"[F1] 動的 border_margin: {border_margin:.1f}")

        # separator クラスタリング許容値（動的計算）
        merge_tol = max(6, table_width * 0.005)

        # ============================================
        # 1a. Line-based separator候補を全件抽出（弾かない）
        # ============================================
        line_separators = []

        for line in v_lines:
            span = line.get('span', 0)
            coverage = line.get('coverage', 0)
            segment_count = line.get('segment_count', 1)
            style = line.get('style', 'unknown')
            pos_x = line.get('position', 0)
            span_start = line.get('span_start', 0)
            span_end = line.get('span_end', page_h)

            # span_ratio を計算
            span_ratio = span / table_height if table_height > 0 else 0

            # 弱候補の最低ライン（これ未満は本当にノイズ）
            if span_ratio < self.SEPARATOR_WEAK_SPAN_RATIO:
                continue

            # y範囲がオーバーラップしているか
            if span_end < y0 or span_start > y1:
                continue

            # 表の x 範囲内にあるか
            if pos_x < x0 - 5 or pos_x > x1 + 5:
                continue

            # 外枠（左端/右端）判定 → 除外せずフラグで記録
            is_border = False
            if pos_x <= x0 + border_margin:
                is_border = True
            if pos_x >= x1 - border_margin:
                is_border = True

            # separator_strength を計算
            separator_strength = span_ratio * coverage

            # segment_count >= 3 のボーナス（途切れ線の救済）
            if segment_count >= 3:
                separator_strength += self.SEPARATOR_SEGMENT_BONUS

            # 強い候補かどうかのフラグ
            is_strong = (
                span_ratio >= self.SEPARATOR_MIN_SPAN_RATIO and
                separator_strength >= self.SEPARATOR_MIN_STRENGTH and
                not is_border
            )

            line_separators.append({
                'x': pos_x,
                'span': span,
                'span_ratio': span_ratio,
                'coverage': coverage,
                'segment_count': segment_count,
                'style': style,
                'separator_strength': separator_strength,
                'source': 'vertical_line',
                'is_strong': bool(is_strong),
                'is_border': bool(is_border),
                'line': line
            })

        # ============================================
        # 1b. Whitespace-based separator候補を抽出（ガター検出）
        # ============================================
        gutter_separators = []
        if page_image is not None:
            # PDF座標系のサイズを取得（座標変換用）
            pdf_w = pdf_page_size.get('w') if pdf_page_size else None
            pdf_h = pdf_page_size.get('h') if pdf_page_size else None
            gutter_separators = self._detect_gutter_separators(
                page_image, x0, y0, x1, y1, border_margin,
                pdf_width=pdf_w, pdf_height=pdf_h
            )

        # ============================================
        # 1c. 全候補を保存（separator_candidates_all）
        # ============================================
        all_separators = line_separators + gutter_separators

        # separator_candidates_all: 全件（弱いものも含む）
        separator_candidates_all = []
        for sep in all_separators:
            candidate = {
                'x': sep['x'],
                'separator_strength': sep['separator_strength'],
                'source': sep.get('source', 'unknown'),
                'is_strong': sep.get('is_strong', False),
                'is_border': sep.get('is_border', False),
                'span_ratio': sep.get('span_ratio', 0),
                'coverage': sep.get('coverage', 0),
                'segment_count': sep.get('segment_count', 0),
                'style': sep.get('style', 'unknown'),
                'evidence': sep.get('evidence', {})
            }
            separator_candidates_all.append(candidate)

        # separator_candidates_ranked: score降順
        separator_candidates_ranked = sorted(
            separator_candidates_all,
            key=lambda s: s['separator_strength'],
            reverse=True
        )

        logger.info(f"[F1] separators_all: line={len(line_separators)}, gutter={len(gutter_separators)}, total={len(all_separators)}")
        for sep in line_separators:
            strong_flag = ' [STRONG]' if sep.get('is_strong') else ' [weak]'
            border_flag = ' [BORDER]' if sep.get('is_border') else ''
            logger.info(
                f"[F1]   line_sep: x={sep['x']:.1f}, span_ratio={sep.get('span_ratio', 0):.2f}, "
                f"coverage={sep.get('coverage', 0):.2f}, segments={sep.get('segment_count', 0)}, "
                f"strength={sep['separator_strength']:.2f}{strong_flag}{border_flag}"
            )
        for sep in gutter_separators:
            ev = sep.get('evidence', {})
            strong_flag = ' [STRONG]' if sep.get('is_strong') else ' [weak]'
            logger.info(
                f"[F1]   gutter_sep: x={sep['x']:.1f}, strength={sep['separator_strength']:.2f}, "
                f"width={ev.get('valley_width', 0)}, depth={ev.get('valley_depth', 0):.3f}{strong_flag}"
            )

        # ============================================
        # 2. 強い候補のみでパネル分割（メイン仮説）
        # ============================================
        strong_separators = [s for s in all_separators if s.get('is_strong') and not s.get('is_border')]

        # パネル分割仮説を複数生成
        panel_split_hypotheses = []

        # 仮説1: 強い候補のみ
        if strong_separators:
            hypothesis_1 = self._build_panel_hypothesis(
                strong_separators, x0, y0, x1, y1, table_width, table_height,
                merge_tol, 'strong_only'
            )
            panel_split_hypotheses.append(hypothesis_1)

        # 仮説2: 全候補（弱いものも含む、borderは除外）
        non_border_separators = [s for s in all_separators if not s.get('is_border')]
        if non_border_separators and len(non_border_separators) != len(strong_separators):
            hypothesis_2 = self._build_panel_hypothesis(
                non_border_separators, x0, y0, x1, y1, table_width, table_height,
                merge_tol, 'all_non_border'
            )
            panel_split_hypotheses.append(hypothesis_2)

        # メイン仮説からパネルを採用（強い候補優先）
        if panel_split_hypotheses:
            main_hypothesis = panel_split_hypotheses[0]
            panels = main_hypothesis['panels']
        else:
            # separator候補がゼロでも、単一パネルとして返す
            logger.info("[F1] separator なし → 単一パネル（候補は separator_candidates_all に記録済み）")
            panels = [{
                'bbox': [x0, y0, x1, y1],
                'score': 0.5,
                'source': 'single_table',
                'evidence': {
                    'no_separators': True,
                    'reason': 'no_strong_separators_found',
                    'all_candidates_count': len(all_separators)
                }
            }]

        logger.info(f"[F1] panel_candidates: {len(panels)}パネル, hypotheses={len(panel_split_hypotheses)}")
        for i, p in enumerate(panels):
            pb = p['bbox']
            logger.info(
                f"[F1]   panel[{i}]: bbox=[{pb[0]:.1f}, {pb[1]:.1f}, {pb[2]:.1f}, {pb[3]:.1f}], "
                f"score={p['score']:.2f}, reasons={p['evidence'].get('reason', [])}"
            )

        return {
            'panels': panels,
            'separator_candidates_all': separator_candidates_all,
            'separator_candidates_ranked': separator_candidates_ranked,
            'panel_split_hypotheses': panel_split_hypotheses
        }

    def _build_panel_hypothesis(
        self,
        separators: List[Dict],
        x0: float, y0: float, x1: float, y1: float,
        table_width: float, table_height: float,
        merge_tol: float,
        hypothesis_name: str
    ) -> Dict[str, Any]:
        """
        separator群からパネル分割仮説を1つ生成する

        Returns:
            {'name': str, 'panels': [...], 'separator_reps': [...], 'interval_cv': float|None}
        """
        # クラスタリング
        sorted_seps = sorted(separators, key=lambda s: s['x'])
        clustered = []
        current_cluster = [sorted_seps[0]]

        for sep in sorted_seps[1:]:
            if sep['x'] - current_cluster[-1]['x'] <= merge_tol:
                current_cluster.append(sep)
            else:
                clustered.append(current_cluster)
                current_cluster = [sep]
        clustered.append(current_cluster)

        # クラスタの代表（separator_strength 最大）を選択
        separator_reps = []
        for cluster in clustered:
            best = max(cluster, key=lambda s: s['separator_strength'])
            sources_in_cluster = list(set(s.get('source', 'unknown') for s in cluster))
            separator_reps.append({
                'x': best['x'],
                'span_ratio': best.get('span_ratio', 0),
                'coverage': best.get('coverage', 0),
                'segment_count': best.get('segment_count', 0),
                'separator_strength': best['separator_strength'],
                'style': best.get('style', 'gutter'),
                'source': best.get('source', 'unknown'),
                'cluster_size': len(cluster),
                'cluster_sources': sources_in_cluster,
                'evidence': best.get('evidence', {})
            })

        # 等間隔ボーナス（CV計算）
        interval_cv = None
        cv_bonus_applied = False

        if len(separator_reps) >= 2:
            sep_xs = [x0] + [s['x'] for s in separator_reps] + [x1]
            intervals = [sep_xs[i+1] - sep_xs[i] for i in range(len(sep_xs) - 1)]

            if len(intervals) >= 2:
                mean_interval = sum(intervals) / len(intervals)
                if mean_interval > 0:
                    variance = sum((iv - mean_interval) ** 2 for iv in intervals) / len(intervals)
                    std_dev = variance ** 0.5
                    interval_cv = std_dev / mean_interval

                    if interval_cv < self.SEPARATOR_CV_THRESHOLD:
                        cv_bonus_applied = True

        # パネル境界を生成
        boundaries = [x0]
        boundary_info = []

        for sep in separator_reps:
            sep_x = sep['x']
            if sep_x - boundaries[-1] < table_width * self.MIN_PANEL_WIDTH_RATIO:
                continue
            if x1 - sep_x < table_width * self.MIN_PANEL_WIDTH_RATIO:
                continue
            boundaries.append(sep_x)
            boundary_info.append(sep)

        boundaries.append(x1)

        # パネルを生成
        panels = []
        num_panels = len(boundaries) - 1

        for i in range(num_panels):
            px0 = boundaries[i]
            px1 = boundaries[i + 1]

            panel_width = px1 - px0
            if panel_width < table_width * self.MIN_PANEL_WIDTH_RATIO:
                continue

            expected_width = table_width / num_panels if num_panels > 0 else table_width
            width_ratio = panel_width / expected_width if expected_width > 0 else 1.0
            base_score = min(1.0, 0.5 + 0.5 * (1.0 - abs(1.0 - width_ratio)))

            if cv_bonus_applied:
                base_score = min(1.0, base_score + self.SEPARATOR_CV_BONUS)

            evidence = {
                'left_boundary': px0,
                'right_boundary': px1,
                'panel_index': i,
                'total_panels': num_panels,
                'panel_width': panel_width,
                'width_ratio': width_ratio,
                'cv_bonus_applied': cv_bonus_applied,
                'interval_cv': interval_cv,
                'reason': []
            }

            if i > 0 and i - 1 < len(boundary_info):
                sep_info = boundary_info[i - 1]
                sep_source = sep_info.get('source', 'unknown')
                evidence['left_separator'] = {
                    'x': sep_info['x'],
                    'span_ratio': sep_info.get('span_ratio', 0),
                    'coverage': sep_info.get('coverage', 0),
                    'segment_count': sep_info.get('segment_count', 0),
                    'separator_strength': sep_info['separator_strength'],
                    'source': sep_source
                }
                if sep_source == 'whitespace_gutter':
                    evidence['reason'].append('whitespace_gutter_separator')
                else:
                    evidence['reason'].append('strong_vertical_separator')

            if i < len(boundary_info):
                sep_info = boundary_info[i]
                sep_source = sep_info.get('source', 'unknown')
                evidence['right_separator'] = {
                    'x': sep_info['x'],
                    'span_ratio': sep_info.get('span_ratio', 0),
                    'coverage': sep_info.get('coverage', 0),
                    'segment_count': sep_info.get('segment_count', 0),
                    'separator_strength': sep_info['separator_strength'],
                    'source': sep_source
                }
                if sep_source == 'whitespace_gutter':
                    if 'whitespace_gutter_separator' not in evidence['reason']:
                        evidence['reason'].append('whitespace_gutter_separator')
                else:
                    if 'strong_vertical_separator' not in evidence['reason']:
                        evidence['reason'].append('strong_vertical_separator')

            if cv_bonus_applied:
                evidence['reason'].append('equal_interval_bonus')

            panels.append({
                'bbox': [px0, y0, px1, y1],
                'score': base_score,
                'source': 'separator_split',
                'evidence': evidence
            })

        return {
            'name': hypothesis_name,
            'panels': panels,
            'separator_reps': separator_reps,
            'interval_cv': interval_cv,
            'cv_bonus_applied': cv_bonus_applied
        }

    def _estimate_border_margin(
        self,
        v_lines: List[Dict],
        x0: float,
        x1: float,
        table_width: float
    ) -> float:
        """
        外枠除外マージンをヒストグラムから動的に推定（Ver 10.4）

        戦略:
        1. 左端付近 (x0〜x0+5%) の線の x 位置を収集
        2. 右端付近 (x1-5%〜x1) の線の x 位置を収集
        3. 各エリアで最も内側の線との距離を計算
        4. その距離の半分をマージンとする（外枠は除外、内側の線は残す）
        """
        default_margin = max(10, table_width * 0.02)

        if not v_lines or table_width <= 0:
            return default_margin

        # 端5%のエリアを検査
        margin_check_ratio = 0.05
        left_boundary = x0 + table_width * margin_check_ratio
        right_boundary = x1 - table_width * margin_check_ratio

        # 左端付近の線
        left_lines = [l for l in v_lines if x0 <= l.get('position', 0) <= left_boundary]
        # 右端付近の線
        right_lines = [l for l in v_lines if right_boundary <= l.get('position', 0) <= x1]

        left_margin = default_margin
        right_margin = default_margin

        if left_lines:
            # 最も外側（x0に近い）の線と、次の線との距離
            left_positions = sorted([l['position'] for l in left_lines])
            if len(left_positions) >= 2:
                # 外枠から次の線までの距離の半分
                left_margin = (left_positions[1] - left_positions[0]) / 2
            else:
                # 外枠1本のみ → 外枠からの距離
                left_margin = left_positions[0] - x0 + 2

        if right_lines:
            right_positions = sorted([l['position'] for l in right_lines], reverse=True)
            if len(right_positions) >= 2:
                right_margin = (right_positions[0] - right_positions[1]) / 2
            else:
                right_margin = x1 - right_positions[0] + 2

        # 左右の平均を使用、最小10px
        margin = max(10, (left_margin + right_margin) / 2)

        logger.debug(
            f"[F1] border_margin推定: left={left_margin:.1f}, right={right_margin:.1f}, "
            f"result={margin:.1f}"
        )

        return margin

    # ============================================================
    # Whitespace/Gutter separator 検出（Ver 10.5）
    # ============================================================

    def _detect_gutter_separators(
        self,
        page_image: Any,
        x0: float, y0: float, x1: float, y1: float,
        border_margin: float,
        pdf_width: float = None,
        pdf_height: float = None
    ) -> List[Dict]:
        """
        画像のインク密度プロファイルから縦ガター（余白区切り）を検出

        Args:
            page_image: ページ画像（PIL Image or numpy array）
            x0, y0, x1, y1: 検出対象領域（table_bbox、PDF座標系）
            border_margin: 外枠除外マージン（PDF座標系）
            pdf_width, pdf_height: PDF座標系のページサイズ（スケーリング用）

        Returns:
            gutter_separators: [{x, separator_strength, source, evidence}, ...]
            ※返り値のxはPDF座標系
        """
        if page_image is None or cv2 is None:
            return []

        try:
            # numpy配列に変換
            if hasattr(page_image, 'convert'):
                # PIL Image
                img_array = np.array(page_image.convert('L'))
            elif isinstance(page_image, np.ndarray):
                if len(page_image.shape) == 3:
                    img_array = cv2.cvtColor(page_image, cv2.COLOR_BGR2GRAY)
                else:
                    img_array = page_image
            else:
                logger.debug("[F1] gutter検出: 不明な画像形式")
                return []

            img_h, img_w = img_array.shape[:2]

            # PDF座標系 → 画像座標系のスケーリング
            # pdf_width/pdf_height が指定されていれば使用、なければ bbox の max から推定
            if pdf_width and pdf_height and pdf_width > 0 and pdf_height > 0:
                scale_x = img_w / pdf_width
                scale_y = img_h / pdf_height
            else:
                # スケーリング不要（すでに画像座標系）
                scale_x = 1.0
                scale_y = 1.0

            # PDF座標系での table サイズ（返り値で使用）
            table_width_pdf = x1 - x0
            table_height_pdf = y1 - y0

            if table_width_pdf <= 0 or table_height_pdf <= 0:
                return []

            # 画像座標系に変換
            img_x0 = x0 * scale_x
            img_y0 = y0 * scale_y
            img_x1 = x1 * scale_x
            img_y1 = y1 * scale_y

            # 座標をクリップしてint変換
            ix0 = max(0, int(img_x0))
            iy0 = max(0, int(img_y0))
            ix1 = min(img_w, int(img_x1))
            iy1 = min(img_h, int(img_y1))

            if ix1 <= ix0 or iy1 <= iy0:
                return []

            # 画像座標系での table サイズ（クロップ後の処理で使用）
            table_width_img = ix1 - ix0
            table_height_img = iy1 - iy0

            logger.debug(f"[F1] gutter検出: PDF bbox=[{x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f}], "
                        f"img bbox=[{ix0},{iy0},{ix1},{iy1}], scale=({scale_x:.2f},{scale_y:.2f})")

            # 座標系の歪み検知（scale_x と scale_y の乖離）
            max_scale = max(scale_x, scale_y)
            if max_scale > 0:
                scale_skew = abs(scale_x - scale_y) / max_scale
                if scale_skew > self.SCALE_SKEW_WARN_THRESHOLD:
                    logger.warning(
                        f"[F1] ⚠ 座標系歪み検出: scale_x={scale_x:.3f}, scale_y={scale_y:.3f}, "
                        f"skew={scale_skew:.3f} > {self.SCALE_SKEW_WARN_THRESHOLD}"
                    )

            # 対象領域をクロップ
            cropped = img_array[iy0:iy1, ix0:ix1]

            # 二値化（Otsu）
            _, binary = cv2.threshold(cropped, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # x方向のインク密度プロファイル（各x位置での黒画素比率）
            crop_h = binary.shape[0]
            ink_profile = np.sum(binary > 0, axis=0) / crop_h

            # 移動平均で平滑化（ノイズ除去）
            window_size = max(3, int(table_width_img * 0.01))
            if len(ink_profile) > window_size:
                kernel = np.ones(window_size) / window_size
                ink_profile_smooth = np.convolve(ink_profile, kernel, mode='same')
            else:
                ink_profile_smooth = ink_profile

            # 谷（低密度領域）を検出
            gutter_separators = []
            min_gutter_width = max(3, int(table_width_img * self.GUTTER_MIN_WIDTH_RATIO))
            gutter_border_margin = int(table_width_img * self.GUTTER_BORDER_MARGIN_RATIO)

            # 全体の平均密度
            mean_density = np.mean(ink_profile_smooth)

            # 谷の検出（連続する低密度領域）
            in_valley = False
            valley_start = 0
            valley_positions = []

            for i, density in enumerate(ink_profile_smooth):
                # 弱候補も拾うため、GUTTER_WEAK_MAX_DENSITY を使う
                is_low = density < self.GUTTER_WEAK_MAX_DENSITY

                if is_low and not in_valley:
                    # 谷開始
                    valley_start = i
                    in_valley = True
                elif not is_low and in_valley:
                    # 谷終了
                    valley_end = i
                    valley_width = valley_end - valley_start

                    if valley_width >= min_gutter_width:
                        valley_center = (valley_start + valley_end) / 2
                        valley_min_density = np.min(ink_profile_smooth[valley_start:valley_end])
                        valley_positions.append({
                            'center': valley_center,
                            'width': valley_width,
                            'min_density': valley_min_density,
                            'start': valley_start,
                            'end': valley_end
                        })
                    in_valley = False

            # 最後の谷が終わっていない場合
            if in_valley:
                valley_end = len(ink_profile_smooth)
                valley_width = valley_end - valley_start
                if valley_width >= min_gutter_width:
                    valley_center = (valley_start + valley_end) / 2
                    valley_min_density = np.min(ink_profile_smooth[valley_start:valley_end])
                    valley_positions.append({
                        'center': valley_center,
                        'width': valley_width,
                        'min_density': valley_min_density,
                        'start': valley_start,
                        'end': valley_end
                    })

            # 各谷を separator 候補に変換（Ver 10.6: 弱い谷も残す）
            for valley in valley_positions:
                # 画像座標 → PDF座標に変換
                # valley['center'] はクロップ画像内の相対座標
                img_sep_x = ix0 + valley['center']  # 元画像での絶対座標
                sep_x = img_sep_x / scale_x if scale_x > 0 else img_sep_x  # PDF座標系

                # 外枠マージン判定（除外せずフラグで記録）
                is_border = False
                if valley['center'] <= gutter_border_margin:
                    is_border = True
                if valley['center'] >= table_width_img - gutter_border_margin:
                    is_border = True

                # 谷の深さ（周辺との差）
                # 左右50px程度の平均密度と比較
                window = 50
                left_start = max(0, int(valley['start'] - window))
                right_end = min(len(ink_profile_smooth), int(valley['end'] + window))
                left_region = ink_profile_smooth[left_start:valley['start']] if valley['start'] > left_start else []
                right_region = ink_profile_smooth[valley['end']:right_end] if right_end > valley['end'] else []

                surrounding = np.concatenate([left_region, right_region]) if len(left_region) + len(right_region) > 0 else np.array([mean_density])
                surrounding_mean = np.mean(surrounding) if len(surrounding) > 0 else mean_density
                valley_depth = surrounding_mean - valley['min_density']

                # 弱候補の最低ライン（これ未満は本当にノイズ）
                if valley_depth < self.GUTTER_WEAK_DEPTH_MIN:
                    logger.debug(f"[F1] gutter除外(深さ極小): x={sep_x:.1f}, depth={valley_depth:.3f}")
                    continue

                # gutter_strength を計算（0〜1に正規化）
                width_ratio = min(1.0, valley['width'] / (table_width_img * 0.05))
                depth_factor = min(1.0, valley_depth / 0.5)
                gutter_strength = (1 - valley['min_density']) * width_ratio * depth_factor
                gutter_strength = min(1.0, max(0.0, gutter_strength))

                # 強い候補かどうかのフラグ
                is_strong = (
                    valley_depth >= self.GUTTER_DEPTH_MIN and
                    valley['min_density'] <= self.GUTTER_MAX_DENSITY and
                    not is_border
                )

                gutter_separators.append({
                    'x': sep_x,
                    'separator_strength': gutter_strength,
                    'source': 'whitespace_gutter',
                    'is_strong': bool(is_strong),
                    'is_border': bool(is_border),
                    'evidence': {
                        'valley_width': int(valley['width']),
                        'valley_min_density': float(valley['min_density']),
                        'valley_depth': float(valley_depth),
                        'surrounding_mean': float(surrounding_mean),
                        'width_ratio': float(width_ratio),
                        'depth_factor': float(depth_factor),
                        'scale_x': float(scale_x),
                        'scale_y': float(scale_y)
                    }
                })

            logger.debug(f"[F1] gutter候補検出: {len(gutter_separators)}個")
            for g in gutter_separators:
                logger.debug(
                    f"[F1]   gutter: x={g['x']:.1f}, strength={g['separator_strength']:.2f}, "
                    f"width={g['evidence']['valley_width']}, depth={g['evidence']['valley_depth']:.3f}"
                )

            return gutter_separators

        except Exception as e:
            logger.warning(f"[F1] gutter検出エラー: {e}")
            return []

    # ============================================================
    # 既存メソッド（変更なし）
    # ============================================================

    def _observe_from_pdf_vector(
        self,
        pdf_path: Path,
        page_num: int,
        page_size: Optional[Dict[str, int]]
    ) -> Optional[Dict[str, Any]]:
        """
        Aルート: PDFのベクター情報から罫線を観測
        """
        if pdfplumber is None:
            raise ImportError("pdfplumber is not installed")

        with pdfplumber.open(str(pdf_path)) as pdf:
            if page_num >= len(pdf.pages):
                return None

            page = pdf.pages[page_num]
            width = page.width
            height = page.height

            if page_size is None:
                page_size = {'w': int(width), 'h': int(height)}

            # 線分を収集
            all_lines = []

            # lines
            for line in (page.lines or []):
                x0, y0, x1, y1 = line['x0'], line['top'], line['x1'], line['bottom']
                all_lines.append((x0, y0, x1, y1))

            # rects（矩形の4辺）
            for rect in (page.rects or []):
                x0, y0 = rect['x0'], rect['top']
                x1, y1 = rect['x1'], rect['bottom']
                all_lines.append((x0, y0, x1, y0))  # 上辺
                all_lines.append((x0, y1, x1, y1))  # 下辺
                all_lines.append((x0, y0, x0, y1))  # 左辺
                all_lines.append((x1, y0, x1, y1))  # 右辺

            # edges
            for edge in (page.edges or []):
                x0, y0 = edge.get('x0', 0), edge.get('top', 0)
                x1, y1 = edge.get('x1', 0), edge.get('bottom', 0)
                all_lines.append((x0, y0, x1, y1))

            if not all_lines:
                return None

            # 線を観測
            h_candidates, v_candidates = self._classify_and_observe_lines(
                all_lines, width, height, source='vector'
            )

            # 外枠推定
            table_bbox = self._estimate_table_bbox(h_candidates, v_candidates, width, height)

            return {
                'pdf_page_size': {'w': width, 'h': height},  # PDF座標系のページサイズ
                'line_candidates': {
                    'horizontal': h_candidates,
                    'vertical': v_candidates
                },
                'table_bbox_candidate': table_bbox,
                'page_size': page_size
            }

    def _observe_from_image(
        self,
        page_image: Any,
        page_size: Optional[Dict[str, int]]
    ) -> Optional[Dict[str, Any]]:
        """
        Bルート: OpenCVで画像から罫線を観測
        """
        if cv2 is None:
            raise ImportError("opencv-python is not installed")

        # PIL Image -> numpy array
        if hasattr(page_image, 'convert'):
            img_array = np.array(page_image.convert('RGB'))
            img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        else:
            img = page_image

        height, width = img.shape[:2]
        if page_size is None:
            page_size = {'w': width, 'h': height}

        # グレースケール変換
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 二値化（適応的閾値処理）
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 2
        )

        # 水平線検出用カーネル
        h_kernel_length = max(width // 30, 10)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel_length, 1))
        h_lines_img = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=2)

        # 垂直線検出用カーネル
        v_kernel_length = max(height // 30, 10)
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_kernel_length))
        v_lines_img = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel, iterations=2)

        # HoughLinesP で線分を観測
        h_candidates = self._observe_lines_from_binary(h_lines_img, 'horizontal', width, height)
        v_candidates = self._observe_lines_from_binary(v_lines_img, 'vertical', width, height)

        # 外枠推定
        table_bbox = self._estimate_table_bbox(h_candidates, v_candidates, width, height)

        return {
            'line_candidates': {
                'horizontal': h_candidates,
                'vertical': v_candidates
            },
            'table_bbox_candidate': table_bbox,
            'page_size': page_size
        }

    def _observe_lines_from_binary(
        self,
        binary_img: np.ndarray,
        direction: str,
        width: int,
        height: int
    ) -> List[Dict[str, Any]]:
        """
        二値画像から線を観測（solid/dashed 分類付き）

        HoughLinesP で線分群を取得し、同一直線上にクラスタリング。
        被覆密度（coverage）で実線/破線を分類する。
        """
        # パラメータ
        MERGE_TOL_PX = max(3, int(min(width, height) * 0.003))
        HOUGH_TH = 40
        MIN_LINE_LEN = int((width if direction == "horizontal" else height) * self.MIN_LINE_RATIO * 0.5)
        MAX_LINE_GAP = max(8, int(min(width, height) * 0.015))

        # HoughLinesP
        lines_p = cv2.HoughLinesP(
            binary_img,
            rho=1,
            theta=np.pi / 180,
            threshold=HOUGH_TH,
            minLineLength=MIN_LINE_LEN,
            maxLineGap=MAX_LINE_GAP,
        )

        if lines_p is None:
            return self._observe_lines_from_contours(binary_img, direction, width, height)

        # 線分を収集
        segments = []
        for l in lines_p:
            x1, y1, x2, y2 = l[0]
            if direction == "horizontal":
                if abs(y2 - y1) > 3:
                    continue
                pos = (y1 + y2) / 2.0
                start, end = (x1, x2) if x1 <= x2 else (x2, x1)
            else:
                if abs(x2 - x1) > 3:
                    continue
                pos = (x1 + x2) / 2.0
                start, end = (y1, y2) if y1 <= y2 else (y2, y1)

            seg_len = end - start
            segments.append({'pos': pos, 'start': start, 'end': end, 'len': seg_len})

        if not segments:
            return self._observe_lines_from_contours(binary_img, direction, width, height)

        # クラスタリング
        segments.sort(key=lambda t: t['pos'])
        clusters = []
        cur = [segments[0]]
        for s in segments[1:]:
            if abs(s['pos'] - cur[-1]['pos']) <= MERGE_TOL_PX:
                cur.append(s)
            else:
                clusters.append(cur)
                cur = [s]
        clusters.append(cur)

        # 観測結果を構築（Ver 10.4: 捨てない）
        line_candidates = []
        page_span = width if direction == "horizontal" else height

        for c in clusters:
            pos = sum(t['pos'] for t in c) / len(c)
            span_start = min(t['start'] for t in c)
            span_end = max(t['end'] for t in c)
            span = span_end - span_start
            if span <= 0:
                continue

            # 絶対最小長チェック（ノイズ除去）
            if span < self.ABSOLUTE_MIN_LINE_LENGTH:
                continue

            sum_len = sum(t['len'] for t in c)
            coverage = min(1.0, sum_len / float(span))
            span_ratio = span / page_span if page_span > 0 else 0

            # スコアと style
            score = min(1.0, span_ratio * coverage)
            style = "solid" if coverage >= self.SOLID_DENSITY_THRESHOLD else "dashed"

            # Ver 10.4: dashiness と is_short
            dashiness = max(0.0, min(1.0, 1.0 - coverage))
            is_short = span_ratio < self.MIN_LINE_RATIO
            is_guide_candidate = (
                dashiness >= self.GUIDE_LINE_DASHINESS_THRESHOLD or
                coverage < self.GUIDE_LINE_COVERAGE_MAX
            )

            line_candidates.append({
                'orientation': 'h' if direction == 'horizontal' else 'v',
                'style': style,
                'position': pos,
                'span_start': span_start,
                'span_end': span_end,
                'span': span,
                'coverage': coverage,
                'score': score,
                'source': 'hough',
                'segment_count': len(c),
                # Ver 10.4 追加
                'is_short': is_short,
                'dashiness': dashiness,
                'is_guide_candidate': is_guide_candidate
            })

        # position でソート
        line_candidates.sort(key=lambda x: x['position'])

        return line_candidates

    def _observe_lines_from_contours(
        self,
        binary_img: np.ndarray,
        direction: str,
        width: int,
        height: int
    ) -> List[Dict[str, Any]]:
        """輪郭ベースの線観測（フォールバック）- Ver 10.4: 捨てない"""
        contours, _ = cv2.findContours(binary_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        line_candidates = []
        page_span = width if direction == "horizontal" else height

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)

            if direction == 'horizontal':
                pos = y + h / 2
                span = w
                span_start = x
                span_end = x + w
            else:
                pos = x + w / 2
                span = h
                span_start = y
                span_end = y + h

            # 絶対最小長チェック（ノイズ除去）
            if span < self.ABSOLUTE_MIN_LINE_LENGTH:
                continue

            span_ratio = span / page_span if page_span > 0 else 0

            # Ver 10.4: is_short（破棄しない）
            is_short = span_ratio < self.MIN_LINE_RATIO

            # 輪郭ベースは coverage 推定が難しいので solid 扱い
            line_candidates.append({
                'orientation': 'h' if direction == 'horizontal' else 'v',
                'style': 'solid',
                'position': pos,
                'span_start': span_start,
                'span_end': span_end,
                'span': span,
                'coverage': 1.0,  # 輪郭は連続なので
                'score': span_ratio,
                'source': 'contour',
                'segment_count': 1,
                # Ver 10.4 追加
                'is_short': is_short,
                'dashiness': 0.0,  # 輪郭は連続
                'is_guide_candidate': False
            })

        line_candidates.sort(key=lambda x: x['position'])
        return line_candidates

    def _classify_and_observe_lines(
        self,
        lines: List[Tuple[float, float, float, float]],
        width: float,
        height: float,
        source: str = 'vector'
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        線分を水平/垂直に分類して観測（Ver 10.4: 捨てない）

        【変更点】
        - 個別セグメントの最小長閾値を緩和（絶対最小のみ）
        - クラスタリング後に is_short が判定される
        """
        h_segments = []
        v_segments = []

        # 個別セグメントは絶対最小長のみでフィルタ（クラスタ後に is_short 判定）
        min_segment_length = self.ABSOLUTE_MIN_LINE_LENGTH

        for x0, y0, x1, y1 in lines:
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)

            if dy < 3 and dx > min_segment_length:
                # 水平線
                pos = (y0 + y1) / 2
                start, end = (x0, x1) if x0 <= x1 else (x1, x0)
                h_segments.append({'pos': pos, 'start': start, 'end': end, 'len': dx})
            elif dx < 3 and dy > min_segment_length:
                # 垂直線
                pos = (x0 + x1) / 2
                start, end = (y0, y1) if y0 <= y1 else (y1, y0)
                v_segments.append({'pos': pos, 'start': start, 'end': end, 'len': dy})

        h_candidates = self._cluster_segments_to_candidates(h_segments, width, 'h', source)
        v_candidates = self._cluster_segments_to_candidates(v_segments, height, 'v', source)

        return h_candidates, v_candidates

    def _cluster_segments_to_candidates(
        self,
        segments: List[Dict],
        page_span: float,
        orientation: str,
        source: str
    ) -> List[Dict]:
        """
        線分群をクラスタリングして候補に変換（Ver 10.4: 捨てない）

        【変更点】
        - 短い線も破棄せず is_short=True で残す
        - dashiness（0.0-1.0）を計算
        - is_guide_candidate を判定
        """
        if not segments:
            return []

        MERGE_TOL = self.LINE_GROUP_THRESHOLD

        segments.sort(key=lambda t: t['pos'])
        clusters = []
        cur = [segments[0]]
        for s in segments[1:]:
            if abs(s['pos'] - cur[-1]['pos']) <= MERGE_TOL:
                cur.append(s)
            else:
                clusters.append(cur)
                cur = [s]
        clusters.append(cur)

        candidates = []
        for c in clusters:
            pos = sum(t['pos'] for t in c) / len(c)
            span_start = min(t['start'] for t in c)
            span_end = max(t['end'] for t in c)
            span = span_end - span_start

            # span <= 0 は物理的にありえない（同じ点）ので除外
            if span <= 0:
                continue

            # 絶対最小長チェック（ノイズ除去）
            if span < self.ABSOLUTE_MIN_LINE_LENGTH:
                continue

            sum_len = sum(t['len'] for t in c)
            coverage = min(1.0, sum_len / float(span))
            span_ratio = span / page_span if page_span > 0 else 0
            score = min(1.0, span_ratio * coverage)

            # style と dashiness
            style = "solid" if coverage >= self.SOLID_DENSITY_THRESHOLD else "dashed"
            # dashiness: 0.0（完全な実線）〜 1.0（完全な点線）
            dashiness = max(0.0, min(1.0, 1.0 - coverage))

            # is_short: span_ratio が MIN_LINE_RATIO 未満
            is_short = span_ratio < self.MIN_LINE_RATIO

            # is_guide_candidate: 破線っぽい or coverage が低い
            is_guide_candidate = (
                dashiness >= self.GUIDE_LINE_DASHINESS_THRESHOLD or
                coverage < self.GUIDE_LINE_COVERAGE_MAX
            )

            candidates.append({
                'orientation': orientation,
                'style': style,
                'position': pos,
                'span_start': span_start,
                'span_end': span_end,
                'span': span,
                'coverage': coverage,
                'score': score,
                'source': source,
                'segment_count': len(c),
                # Ver 10.4 追加
                'is_short': is_short,
                'dashiness': dashiness,
                'is_guide_candidate': is_guide_candidate
            })

        candidates.sort(key=lambda x: x['position'])
        return candidates

    def _estimate_table_bbox(
        self,
        h_candidates: List[Dict],
        v_candidates: List[Dict],
        width: float,
        height: float
    ) -> Optional[List[float]]:
        """
        外枠から table_bbox を推定（Ver 10.4: フォールバック強化）

        戦略:
        1. 長い実線（外枠）から推定
        2. フォールバック: 線密度のパーセンタイルから推定
        """
        # ============================================
        # 方法1: 長い実線から推定（従来方式）
        # ============================================
        # is_short=False の線のみ使用（Ver 10.4）
        long_h = [c for c in h_candidates
                  if c.get('style') == 'solid'
                  and c.get('span', 0) > width * 0.5
                  and not c.get('is_short', False)]
        long_v = [c for c in v_candidates
                  if c.get('style') == 'solid'
                  and c.get('span', 0) > height * 0.3
                  and not c.get('is_short', False)]

        if len(long_h) >= 2 and len(long_v) >= 2:
            y_min = min(c['position'] for c in long_h)
            y_max = max(c['position'] for c in long_h)
            x_min = min(c['position'] for c in long_v)
            x_max = max(c['position'] for c in long_v)

            # 妥当性チェック
            if (y_max - y_min) >= height * 0.1 and (x_max - x_min) >= width * 0.1:
                logger.debug(f"[F1] table_bbox: 長い実線から推定")
                return [x_min, y_min, x_max, y_max]

        # ============================================
        # 方法2: フォールバック - 線密度パーセンタイル
        # ============================================
        # 全ての線（is_short含む）の位置から密度領域を推定
        all_h = [c for c in h_candidates if not c.get('is_short', False)]
        all_v = [c for c in v_candidates if not c.get('is_short', False)]

        # 線が少なすぎる場合は None
        if len(all_h) < 2 or len(all_v) < 2:
            logger.debug(f"[F1] table_bbox: 線が不足 (H={len(all_h)}, V={len(all_v)})")
            return None

        # パーセンタイルで境界を推定（5%〜95%）
        h_positions = sorted([c['position'] for c in all_h])
        v_positions = sorted([c['position'] for c in all_v])

        # numpy がない場合のパーセンタイル計算
        def percentile(data: List[float], p: float) -> float:
            n = len(data)
            if n == 0:
                return 0
            k = (n - 1) * p / 100
            f = int(k)
            c = f + 1 if f + 1 < n else f
            return data[f] + (k - f) * (data[c] - data[f]) if f != c else data[f]

        y_min = percentile(h_positions, 5)
        y_max = percentile(h_positions, 95)
        x_min = percentile(v_positions, 5)
        x_max = percentile(v_positions, 95)

        # 妥当性チェック
        if (y_max - y_min) < height * 0.1 or (x_max - x_min) < width * 0.1:
            logger.debug(f"[F1] table_bbox: パーセンタイル推定も失敗")
            return None

        logger.debug(f"[F1] table_bbox: 線密度パーセンタイルから推定 (fallback)")
        return [x_min, y_min, x_max, y_max]

    def _log_observation(self, result: Dict[str, Any]):
        """観測結果のログ出力（Ver 10.7: ピクセル座標統一）"""
        logger.info("[F1] ===== 観測ログ開始 (Ver 10.7) =====")
        logger.info(f"[F1] source: {result.get('source')}")

        # 座標系情報
        meta = result.get('metadata', {})
        sx, sy = meta.get('scale_x'), meta.get('scale_y')
        if sx is not None and sy is not None:
            logger.info(f"[F1] scale: x={sx:.3f}, y={sy:.3f}")

        # separator_candidates サマリ
        sep_all = result.get('separator_candidates_all', [])
        sep_ranked = result.get('separator_candidates_ranked', [])
        hypotheses = result.get('panel_split_hypotheses', [])
        strong_count = sum(1 for s in sep_all if s.get('is_strong'))
        logger.info(f"[F1] separator_candidates: all={len(sep_all)}, strong={strong_count}, hypotheses={len(hypotheses)}")
        for i, sep in enumerate(sep_ranked[:10]):
            strong_flag = ' [STRONG]' if sep.get('is_strong') else ' [weak]'
            border_flag = ' [BORDER]' if sep.get('is_border') else ''
            logger.info(
                f"[F1]   ranked[{i}]: x={sep['x']:.1f}, strength={sep['separator_strength']:.2f}, "
                f"source={sep['source']}{strong_flag}{border_flag}"
            )
        if len(sep_ranked) > 10:
            logger.info(f"[F1]   ... and {len(sep_ranked) - 10} more candidates")

        lc = result.get('line_candidates', {})
        h_lines = lc.get('horizontal', [])
        v_lines = lc.get('vertical', [])

        # 統計情報
        h_solid = [l for l in h_lines if l.get('style') == 'solid']
        h_dashed = [l for l in h_lines if l.get('style') == 'dashed']
        h_short = [l for l in h_lines if l.get('is_short', False)]
        h_guide = [l for l in h_lines if l.get('is_guide_candidate', False)]

        v_solid = [l for l in v_lines if l.get('style') == 'solid']
        v_dashed = [l for l in v_lines if l.get('style') == 'dashed']
        v_short = [l for l in v_lines if l.get('is_short', False)]
        v_guide = [l for l in v_lines if l.get('is_guide_candidate', False)]

        logger.info(
            f"[F1] horizontal: {len(h_lines)}本 "
            f"(solid={len(h_solid)}, dashed={len(h_dashed)}, "
            f"short={len(h_short)}, guide_cand={len(h_guide)})"
        )
        logger.info(
            f"[F1] vertical: {len(v_lines)}本 "
            f"(solid={len(v_solid)}, dashed={len(v_dashed)}, "
            f"short={len(v_short)}, guide_cand={len(v_guide)})"
        )

        # 詳細（上位20本ずつ - 全属性表示）
        for direction, lines in [('H', h_lines), ('V', v_lines)]:
            logger.info(f"[F1] --- {direction} 線詳細 (上位20本) ---")
            for i, l in enumerate(lines[:20]):
                is_short_mark = "S" if l.get('is_short', False) else " "
                is_guide_mark = "G" if l.get('is_guide_candidate', False) else " "
                dashiness = l.get('dashiness', 0)

                logger.info(
                    f"[F1]   {direction}[{i:02d}] [{is_short_mark}{is_guide_mark}] "
                    f"pos={l['position']:.1f}, style={l.get('style', '?'):6s}, "
                    f"cov={l.get('coverage', 0):.2f}, dash={dashiness:.2f}, "
                    f"span={l.get('span', 0):.1f} [{l.get('span_start', 0):.1f}-{l.get('span_end', 0):.1f}], "
                    f"seg={l.get('segment_count', 1)}, score={l.get('score', 0):.2f}"
                )
            if len(lines) > 20:
                logger.info(f"[F1]   ... and {len(lines) - 20} more")

        bbox = result.get('table_bbox_candidate')
        if bbox:
            logger.info(f"[F1] table_bbox_candidate: [{bbox[0]:.1f}, {bbox[1]:.1f}, {bbox[2]:.1f}, {bbox[3]:.1f}]")
        else:
            logger.info("[F1] table_bbox_candidate: None")

        # Panel候補のログ
        panels = result.get('panel_candidates', [])
        logger.info(f"[F1] panel_candidates: {len(panels)}パネル")
        for i, p in enumerate(panels):
            pb = p.get('bbox', [0, 0, 0, 0])
            evidence = p.get('evidence', {})
            logger.info(
                f"[F1]   panel[{i}]: bbox=[{pb[0]:.1f}, {pb[1]:.1f}, {pb[2]:.1f}, {pb[3]:.1f}], "
                f"score={p.get('score', 0):.2f}, reasons={evidence.get('reason', [])}"
            )

        if result.get('warnings'):
            logger.info(f"[F1] warnings: {result['warnings']}")

        logger.info("[F1] ===== 観測ログ終了 =====")
