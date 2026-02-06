"""
E-8: bbox座標維持（重複排除機能付き）

【Ver 10.7】1000x1000正規化を廃止。OCR生ピクセル座標を維持し
F1/F2との物理整合性を確保する。
E7をすり抜けた重複トークン（ゴーストトークン）を
幾何学的なIoU判定で排除する。

入力: merged_tokens (E7出力), page_size
出力: normalized_tokens = [{text, bbox, x, y}, ...]
"""

from typing import Dict, Any, List
from loguru import logger


class E8BboxNormalizer:
    """E-8: bbox座標維持（重複排除機能付き）"""

    def normalize(
        self,
        merged_tokens: List[Dict[str, Any]],
        page_size: Dict[str, int]
    ) -> List[Dict[str, Any]]:
        """
        bbox座標維持（生ピクセル）

        Args:
            merged_tokens: E7の出力
            page_size: {"w": width, "h": height}

        Returns:
            normalized_tokens: 生ピクセル座標のトークンリスト
        """
        if not merged_tokens:
            return []

        img_w = page_size.get('w', 1000)
        img_h = page_size.get('h', 1000)

        logger.info(f"[E-8] 座標維持モード: {len(merged_tokens)}トークン, 原寸={img_w}x{img_h}")

        normalized = []

        for token in merged_tokens:
            text = token.get('text', '')
            bbox = token.get('bbox', [0, 0, 0, 0])

            if not text:
                continue

            # 生ピクセル座標をそのまま維持（Ver 10.7: 1000-grid廃止）
            raw_bbox = [int(v) for v in bbox]

            cx = (raw_bbox[0] + raw_bbox[2]) / 2
            cy = (raw_bbox[1] + raw_bbox[3]) / 2

            normalized.append({
                'text': text,
                'bbox': raw_bbox,
                'x': cx,
                'y': cy,
                '_merged_from': token.get('_merged_from', [])
            })

        # --- 重複トークンの掃除 ---
        cleaned = self._remove_redundant_overlaps(normalized)

        # 【全文字ログ出力】
        logger.info(f"[E-8] ===== 生成物ログ開始 =====")
        logger.info(f"[E-8] normalized_tokens数: {len(cleaned)}")
        for i, token in enumerate(cleaned):
            text = token.get('text', '')
            bbox = token.get('bbox', [])
            x = token.get('x', 0)
            y = token.get('y', 0)
            logger.info(f"[E-8]   [{i+1}] bbox={bbox}, x={x:.0f}, y={y:.0f}, text='{text}'")
        logger.info(f"[E-8] ===== 生成物ログ終了 =====")

        logger.info(f"[E-8] 座標維持完了: {len(cleaned)}トークン")
        return cleaned

    def _remove_redundant_overlaps(self, tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        物理的に高度に重なっているトークンのうち、短い方を削除する

        「2026年」と「2026年度用」のようなゴーストトークンを排除するため、
        小さいトークンの面積の80%以上が大きいトークンと重なっている場合、
        小さい方を削除する。

        Args:
            tokens: 正規化済みトークンリスト

        Returns:
            重複排除後のトークンリスト
        """
        if not tokens:
            return []

        # 面積の大きい順にソート（大きい方を優先的に残すため）
        sorted_tokens = sorted(
            tokens,
            key=lambda t: (t["bbox"][2] - t["bbox"][0]) * (t["bbox"][3] - t["bbox"][1]),
            reverse=True
        )

        keep_flags = [True] * len(sorted_tokens)

        for i in range(len(sorted_tokens)):
            if not keep_flags[i]:
                continue

            for j in range(i + 1, len(sorted_tokens)):
                if not keep_flags[j]:
                    continue

                # 重なり度合い（交差面積）を計算
                b1 = sorted_tokens[i]["bbox"]
                b2 = sorted_tokens[j]["bbox"]

                inter_x0 = max(b1[0], b2[0])
                inter_y0 = max(b1[1], b2[1])
                inter_x1 = min(b1[2], b2[2])
                inter_y1 = min(b1[3], b2[3])

                if inter_x1 > inter_x0 and inter_y1 > inter_y0:
                    inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
                    area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])

                    # 小さい方のトークン(j)の面積の80%以上が重なっていたら重複とみなす
                    if area2 > 0 and (inter_area / area2) > 0.8:
                        logger.warning(
                            f"[E-8] 重複排除: '{sorted_tokens[j]['text']}' を削除"
                            f"（'{sorted_tokens[i]['text']}' と重なり大）"
                        )
                        keep_flags[j] = False

        result = [sorted_tokens[i] for i in range(len(sorted_tokens)) if keep_flags[i]]

        # 元の読み順（Y座標、次にX座標）に戻す
        result.sort(key=lambda t: (t['bbox'][1], t['bbox'][0]))

        if len(tokens) != len(result):
            logger.info(f"[E-8] 重複排除完了: {len(tokens)} -> {len(result)} トークン")

        return result
