"""
Yotsuya Domain Processor

四谷大塚偏差値表の固有処理を実装
"""
import re
from typing import Dict, Any, List
from loguru import logger

from .base_processor import BaseDomainProcessor


class YotsuyaProcessor(BaseDomainProcessor):
    """
    四谷大塚偏差値表の固有処理プロセッサー

    主な処理:
    - 肩付き日付（superscript dates）の検出と適用
    - デフォルト日付の適用（'2/4~' → '2/4'）
    """

    def process(self, cells_enriched: List[Dict]) -> None:
        """
        四谷大塚固有の前処理を実行

        Args:
            cells_enriched: G8出力の enriched セルリスト（in-place変更）
        """
        logger.info("[YotsuyaProcessor] 四谷大塚固有処理開始")

        # 肩付き日付処理
        self._apply_superscript_dates(cells_enriched)

        # デフォルト日付処理
        self._apply_default_dates(cells_enriched)

        logger.info("[YotsuyaProcessor] 四谷大塚固有処理完了")

    def _apply_superscript_dates(self, cells_enriched: List[Dict]) -> None:
        """
        ドメイン定義の superscript_dates ルールを適用

        肩付き日付を検出し、直下の学校の col_header を置き換え
        """
        superscript_rules = self.domain_def.get('table_rules', {}).get('superscript_dates', {})
        if not superscript_rules.get('enabled', False):
            return

        # 肩付き日付を検出
        superscript_dates = []
        for cell in cells_enriched:
            if cell.get('is_header', False):
                continue  # ヘッダーは対象外

            text = cell.get('text', '').strip()
            if not text:
                continue

            # M/D パターンで日付判定
            if re.match(r'^\d{1,2}/\d{1,2}$', text):
                bbox = cell.get('bbox', [])
                if bbox and len(bbox) == 4:
                    superscript_dates.append({
                        'text': text,
                        'cell': cell,
                        'bbox': bbox
                    })

        logger.info(f"[YotsuyaProcessor] 肩付き日付検出: {len(superscript_dates)}件")
        for sd in superscript_dates:
            logger.info(f"[YotsuyaProcessor]   肩付き日付: text='{sd['text']}', bbox={sd['bbox']}")

        # 各肩付き日付について、直下の学校を探して col_header 置き換え
        max_distance = superscript_rules.get('replacement', {}).get('max_vertical_distance_px', 10)
        logger.info(f"[YotsuyaProcessor] max_vertical_distance_px={max_distance}")

        for superscript in superscript_dates:
            x0_super, y0_super, x1_super, y1_super = superscript['bbox']

            # 条件を満たす最初の1校を探す
            closest_cell = None
            closest_dist = max_distance + 1

            for cell in cells_enriched:
                if cell.get('is_header', False):
                    continue

                bbox = cell.get('bbox', [])
                if not bbox or len(bbox) < 4:
                    continue

                x0_cell, y0_cell, x1_cell, y1_cell = bbox

                # 1. 垂直距離チェック: 肩付き日付の下端 から セルの上端 まで
                vertical_dist = y0_cell - y1_super
                if vertical_dist < 0 or vertical_dist > max_distance:
                    continue  # 上にある、または距離が遠い

                # 2. 水平重なりチェック: 左半分が重なっているか
                super_left = x0_super
                super_right = x1_super
                super_width = super_right - super_left
                super_left_half_end = super_left + super_width / 2

                cell_left = x0_cell
                cell_right = x1_cell

                # 肩付き日付の左半分と、セルが水平方向に重なっているか
                overlap_left = max(super_left, cell_left)
                overlap_right = min(super_left_half_end, cell_right)
                has_overlap = overlap_right > overlap_left

                if not has_overlap:
                    continue  # 水平方向に重なりなし

                # 3. 最も近いセルを選択（距離条件内で最初の1校）
                if vertical_dist < closest_dist:
                    closest_dist = vertical_dist
                    closest_cell = cell

            # 置き換え実行
            if closest_cell:
                old_header = closest_cell.get('col_header')
                closest_cell['col_header'] = superscript['text']
                cell_bbox = closest_cell.get('bbox', [])
                logger.info(
                    f"[YotsuyaProcessor] ✓ マッチ: '{superscript['text']}' → '{closest_cell.get('text', '')}' "
                    f"(垂直距離={closest_dist:.1f}px, col_header: '{old_header}' → '{superscript['text']}')"
                )

                # 肩付き日付セルを非表示フラグ
                superscript['cell']['_hidden'] = True
            else:
                logger.info(
                    f"[YotsuyaProcessor] ✗ マッチなし: '{superscript['text']}' "
                    f"bbox={superscript['bbox']} (距離または水平重なり条件を満たすセルなし)"
                )

    def _apply_default_dates(self, cells_enriched: List[Dict]) -> None:
        """
        デフォルト日付をcol_headerに適用

        肩付き日付で上書きされなかったセルに対して、
        ドメイン定義のdefault_dateを適用する。

        例: '2/4~' → '2/4' (default_date)
        """
        logger.info("[YotsuyaProcessor] デフォルト日付処理開始")

        columns = self.domain_def.get('table_rules', {}).get('format_detection', {}).get('columns', [])
        logger.info(f"[YotsuyaProcessor] columns取得: {len(columns)}件")

        # col_header → default_date のマッピング構築（正規化）
        def normalize_header(s):
            """ヘッダー文字列を正規化（スペース削除、全角チルダ→半角）"""
            return s.replace(' ', '').replace('～', '~')

        default_map = {}  # 正規化キー → default_date
        original_headers = {}  # 正規化キー → 元のheader
        for col_def in columns:
            header = col_def.get('header')
            default_date = col_def.get('default_date')
            if header and default_date:
                normalized_key = normalize_header(header)
                default_map[normalized_key] = default_date
                original_headers[normalized_key] = header

        logger.info(f"[YotsuyaProcessor] デフォルト日付マップ（正規化済み）: {default_map}")

        if not default_map:
            logger.warning("[YotsuyaProcessor] デフォルト日付マップが空（スキップ）")
            return

        # データセルのcol_headerをデフォルト値で置き換え
        replaced_count = 0
        for cell in cells_enriched:
            if cell.get('is_header', False):
                continue

            col_header = cell.get('col_header')
            if not col_header:
                continue

            # 正規化してマッチング
            normalized_col_header = normalize_header(col_header)

            if normalized_col_header in default_map:
                old_header = col_header
                cell['col_header'] = default_map[normalized_col_header]
                replaced_count += 1
                logger.debug(
                    f"[YotsuyaProcessor] col_header デフォルト適用: '{old_header}' → '{default_map[normalized_col_header]}' "
                    f"(正規化: '{normalized_col_header}')"
                )

        if replaced_count > 0:
            logger.info(f"[YotsuyaProcessor] デフォルト日付適用: {replaced_count}件")
