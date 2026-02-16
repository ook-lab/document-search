"""
E-27: Position Merger（位置順マージ）

E20（Vision OCR）とE25（段落グループ化）の座標付きブロックを
位置順（ページ → Y座標 → X座標）にマージする。

目的:
1. E20のVision OCRブロックとE25の段落ブロックを統合
2. 座標に基づいて位置順にソート
3. 重複を除去（同一位置の場合はE25を優先）
"""

from typing import Dict, Any, List
from loguru import logger


class E27PositionMerger:
    """E-27: Position Merger（位置順マージ）"""

    def __init__(
        self,
        overlap_threshold: float = 0.5
    ):
        """
        Position Merger 初期化

        Args:
            overlap_threshold: 重複と判定する重なり率（0.0-1.0）
        """
        self.overlap_threshold = overlap_threshold

    def merge(
        self,
        e20_result: Dict[str, Any] = None,
        e21_result: Dict[str, Any] = None,
        e25_result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        E20、E21、E25の結果を位置順にマージ

        Args:
            e20_result: E20の結果（Vision OCR座標付きブロック）
            e21_result: E21の結果（Gemini AI座標付きブロック）
            e25_result: E25の結果（段落ブロック）

        Returns:
            {
                'success': bool,
                'merged_blocks': [
                    {
                        'page': int,
                        'type': 'paragraph',
                        'text': str,
                        'bbox': [x0, y0, x1, y1],
                        'source': 'e20' | 'e21' | 'e25'
                    }
                ]
            }
        """
        logger.info("[E-27] ========================================")
        logger.info("[E-27] 位置順マージ開始")
        logger.info("[E-27] ========================================")

        try:
            # ------------------------------------------------------------
            # 入力制御:
            # E21が「成功」かつ「最終正本」role かつ blocks がある場合は、E20は統合しない。
            # （E20はE21の材料であり、最終出力はE21とみなす）
            # ------------------------------------------------------------
            e21_has_blocks = bool(
                e21_result
                and e21_result.get('success')
                and e21_result.get('role') == 'NON_TABLE_FINAL_TEXT'
                and e21_result.get('blocks')
                and len(e21_result.get('blocks')) > 0
            )
            if e21_has_blocks:
                logger.info("[E-27] E21が最終正本として成功したため、E20は統合対象から除外します")
                e20_result = None

            # E20のブロックを取得
            e20_blocks = []
            if e20_result and e20_result.get('success'):
                e20_blocks = e20_result.get('blocks', [])
            logger.info(f"[E-27] E20（Vision OCR）ブロック数: {len(e20_blocks)}")

            # E21のブロックを取得
            e21_blocks = []
            if e21_result and e21_result.get('success'):
                e21_blocks = e21_result.get('blocks', [])
            logger.info(f"[E-27] E21（Gemini AI）ブロック数: {len(e21_blocks)}")

            # E25のブロックを取得
            e25_blocks = []
            if e25_result and e25_result.get('success'):
                e25_blocks = e25_result.get('paragraphs', [])
            logger.info(f"[E-27] E25（段落グループ）ブロック数: {len(e25_blocks)}")

            # ソース情報を追加
            for block in e20_blocks:
                block['source'] = 'e20'
            for block in e21_blocks:
                block['source'] = 'e21'
            for block in e25_blocks:
                block['source'] = 'e25'

            # 全ブロックを統合
            all_blocks = e20_blocks + e21_blocks + e25_blocks

            # 位置順にソート（ページ → Y座標 → X座標）
            sorted_blocks = sorted(
                all_blocks,
                key=lambda b: (
                    b.get('page', 0),
                    b.get('bbox', [0, 0, 0, 0])[1],  # y0
                    b.get('bbox', [0, 0, 0, 0])[0]   # x0
                )
            )

            logger.info(f"[E-27] ソート後ブロック数: {len(sorted_blocks)}")

            # 重複除去（同一位置の場合はE25を優先）
            merged_blocks = self._remove_duplicates(sorted_blocks)

            logger.info(f"[E-27] 重複除去後ブロック数: {len(merged_blocks)}")
            logger.info("")
            logger.info("[E-27] ソース別統計:")
            e20_count = sum(1 for b in merged_blocks if b.get('source') == 'e20')
            e21_count = sum(1 for b in merged_blocks if b.get('source') == 'e21')
            e25_count = sum(1 for b in merged_blocks if b.get('source') == 'e25')
            logger.info(f"  ├─ E20（Vision OCR）: {e20_count}ブロック")
            logger.info(f"  ├─ E21（Gemini AI）: {e21_count}ブロック")
            logger.info(f"  └─ E25（段落グループ）: {e25_count}ブロック")

            # サンプル出力
            logger.info("")
            logger.info("[E-27] ===== マージ結果サンプル（先頭5ブロック） =====")
            for idx, block in enumerate(merged_blocks[:5], 1):
                source = block.get('source', 'unknown')
                text = block.get('text', '')[:50]
                page = block.get('page', 0)
                bbox = block.get('bbox', [0, 0, 0, 0])
                logger.info(f"ブロック{idx} [{source}] (p{page}, y={bbox[1]:.0f}):")
                logger.info(f"  {text}...")

            logger.info("")
            logger.info(f"[E-27] 位置順マージ完了: {len(merged_blocks)}ブロック")
            logger.info("[E-27] ========================================")

            return {
                'success': True,
                'merged_blocks': merged_blocks
            }

        except Exception as e:
            logger.error(f"[E-27] 位置順マージエラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'merged_blocks': []
            }

    def _remove_duplicates(
        self,
        blocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        重複ブロックを除去（同一位置の場合はE25を優先）

        Args:
            blocks: ソート済みブロックリスト

        Returns:
            重複除去後のブロックリスト
        """
        if not blocks:
            return []

        unique_blocks = []
        skip_indices = set()

        for i, block1 in enumerate(blocks):
            if i in skip_indices:
                continue

            # このブロックと重なる他のブロックを探す
            overlapping = [block1]
            for j in range(i + 1, len(blocks)):
                if j in skip_indices:
                    continue

                block2 = blocks[j]

                # ページが異なる、またはY座標が大きく離れている場合はスキップ
                if block1.get('page') != block2.get('page'):
                    break
                if abs(block1.get('bbox', [0, 0, 0, 0])[1] - block2.get('bbox', [0, 0, 0, 0])[1]) > 50:
                    break

                # 重なり率を計算
                if self._calculate_overlap(block1.get('bbox'), block2.get('bbox')) > self.overlap_threshold:
                    overlapping.append(block2)
                    skip_indices.add(j)

            # 重複がある場合、E25を優先
            if len(overlapping) > 1:
                e25_blocks = [b for b in overlapping if b.get('source') == 'e25']
                if e25_blocks:
                    unique_blocks.append(e25_blocks[0])
                else:
                    unique_blocks.append(overlapping[0])
            else:
                unique_blocks.append(block1)

        return unique_blocks

    def _calculate_overlap(
        self,
        bbox1: List[float],
        bbox2: List[float]
    ) -> float:
        """
        2つのbboxの重なり率を計算

        Args:
            bbox1: [x0, y0, x1, y1]
            bbox2: [x0, y0, x1, y1]

        Returns:
            重なり率（0.0-1.0）
        """
        if not bbox1 or not bbox2:
            return 0.0

        x0_1, y0_1, x1_1, y1_1 = bbox1
        x0_2, y0_2, x1_2, y1_2 = bbox2

        # 交差領域を計算
        x0_i = max(x0_1, x0_2)
        y0_i = max(y0_1, y0_2)
        x1_i = min(x1_1, x1_2)
        y1_i = min(y1_1, y1_2)

        if x0_i >= x1_i or y0_i >= y1_i:
            return 0.0

        intersection = (x1_i - x0_i) * (y1_i - y0_i)

        # 各bboxの面積
        area1 = (x1_1 - x0_1) * (y1_1 - y0_1)
        area2 = (x1_2 - x0_2) * (y1_2 - y0_2)

        # 小さい方の面積に対する重なり率
        min_area = min(area1, area2)
        if min_area == 0:
            return 0.0

        return intersection / min_area
