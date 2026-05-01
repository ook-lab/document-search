"""
E-25: Coordinate Fitter（座標フィッター） - E21テキスト尊重版

目的:
  - E21（Gemini）が作成した意味段落（text + bbox）の
    bbox を正規化・クランプするだけ。
  - テキストは一切触らない（再OCRなし、word再収集なし、union再生成なし）。

役割（縮小済み）:
  1. E21 bbox を必ず xyxy（x0,y0,x1,y1）に正規化
  2. page キーを付与
  3. bbox_source / matched_words / debug_note を付与（下流互換）

削除済みロジック（E21のまとまりを壊していたため撤廃）:
  - Tesseract words 再収集
  - 膨張bbox内word収集
  - dedupe（word → 最近傍segment割当）
  - nearest-k 近傍探索
  - union_bbox 再生成
  - text 再結合

ログ（必須）:
  - 段落ごとに bbox / テキスト全文 を必ず出す
"""

from typing import Dict, Any, List, Tuple, Optional


from loguru import logger


# -------------------------
# bbox utilities
# -------------------------

def _as_xyxy_from_xywh(b: List[float]) -> Optional[Tuple[float, float, float, float]]:
    """[x,y,w,h] -> xyxy"""
    if not b or len(b) < 4:
        return None
    x, y, w, h = float(b[0]), float(b[1]), float(b[2]), float(b[3])
    if w is None or h is None:
        return None
    x0, y0, x1, y1 = x, y, x + w, y + h
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _as_xyxy_from_xyxy(b: List[float]) -> Optional[Tuple[float, float, float, float]]:
    """[x0,y0,x1,y1] -> xyxy (normalize)"""
    if not b or len(b) < 4:
        return None
    x0, y0, x1, y1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _safe_text(x: Any) -> str:
    return (x or "").strip()


# -------------------------
# main
# -------------------------

class E25ParagraphGrouper:
    """
    E-25: Coordinate Fitter（E21テキスト尊重版）

    E21 が返した blocks の text はそのまま保持し、
    bbox を xyxy に正規化するだけ。
    """

    def __init__(
        self,
        e21_bbox_is_xywh: bool = False,
    ):
        """
        Args:
            e21_bbox_is_xywh: True なら E21 bbox を [x,y,w,h] として扱い xyxy へ変換
                              False なら E21 bbox は [x0,y0,x1,y1] として扱う（Gemini実態に合わせた正規値）
        """
        self.e21_bbox_is_xywh = e21_bbox_is_xywh

    def _convert_e21_bbox_to_xyxy(self, bbox: List[float]) -> Optional[Tuple[float, float, float, float]]:
        if self.e21_bbox_is_xywh:
            return _as_xyxy_from_xywh(bbox)
        return _as_xyxy_from_xyxy(bbox)

    def fit(
        self,
        e21_blocks: List[Dict[str, Any]],
        tesseract_words: List[Dict[str, Any]],
        page: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        E21 blocks の bbox を正規化して返す。
        text は一切変更しない。
        tesseract_words は受け取るが使用しない（API互換性維持のため残す）。

        Args:
            e21_blocks:      E21 が返した blocks
            tesseract_words: E1 Tesseract words（未使用・互換維持）
            page:            ページ番号

        Returns:
            各 block に page / bbox(xyxy) / bbox_source / matched_words / debug_note を付与したリスト
        """
        logger.info("[E-25] 座標正規化開始（E21テキスト尊重版）")
        logger.info(f"[E-25]   ├─ E21 段落数: {len(e21_blocks)}")
        logger.info(
            f"[E-25]   └─ 設定: e21_bbox_is_xywh={self.e21_bbox_is_xywh}"
        )

        if not e21_blocks:
            logger.info("[E-25] E21 段落なし → 空リスト返却")
            return []

        fitted: List[Dict[str, Any]] = []
        ok = 0
        fallback = 0

        for bi, blk in enumerate(e21_blocks):
            out = dict(blk)
            out["page"] = page

            raw_bbox = blk.get("bbox") or []
            bb_xyxy = self._convert_e21_bbox_to_xyxy(raw_bbox)
            ptxt = _safe_text(out.get("text"))

            if bb_xyxy:
                x0, y0, x1, y1 = bb_xyxy
                out["bbox"] = [x0, y0, x1, y1]
                out["bbox_source"] = "e21_xyxy"
                out["matched_words"] = 0
                out["debug_note"] = "e21_text_respected"
                ok += 1
                logger.info(
                    f"[E-25] 段落{bi}: bbox=[{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}]"
                )
            else:
                out["bbox"] = [0.0, 0.0, 0.0, 0.0]
                out["bbox_source"] = "e21_approx"
                out["matched_words"] = 0
                out["debug_note"] = "bbox_missing_or_invalid"
                fallback += 1
                logger.info(f"[E-25] 段落{bi}: bbox無効 → [0,0,0,0]")

            logger.info(f"[E-25] 段落{bi} テキスト全文: 「{ptxt}」")

            fitted.append(out)

        logger.info(
            f"[E-25] 正規化完了: ok={ok}, fallback={fallback} (total={len(fitted)})"
        )
        return fitted
