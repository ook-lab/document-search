"""
F7〜F10: 新パイプライン（検出OCR版）
【Ver 7.0】LLMによるOCRを完全排除

設計原則:
1. OCRは検出器でやる（Vision API）
2. LLMは解釈だけ（F9.5の選択問題のみ）
3. 座標はプログラムで検証して落とす

ライン構成:
  F7   : Vision API → tokens確定 (word固定)
  F7.5 : Surya block へ IoU優先マッピング + 異常検知
  F8   : page_type判定 → 表なら物理グリッド確定
  F9   : Programでタグ付け（正規表現・近傍ルール）
  F9.5 : LLM救済（少数・選択問題のみ）
  F10  : 異常排除 + 正本化 + anomaly_report
"""
import re
import time
import math
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger

from .vision_api_extractor import VisionAPIExtractor, VISION_API_AVAILABLE


# ============================================
# データクラス定義
# ============================================

@dataclass
class Token:
    """OCRトークン"""
    text: str
    bbox: List[int]  # [x0, y0, x1, y1]
    conf: float
    block_id: Optional[str] = None
    match_score: Optional[float] = None
    tag: Optional[str] = None

@dataclass
class Block:
    """Suryaブロック"""
    id: str
    bbox: List[int]  # [x0, y0, x1, y1]
    label: Optional[str] = None
    tokens: List[Token] = field(default_factory=list)

@dataclass
class TableCell:
    """表セル"""
    row: int
    col: int
    text: str
    bbox: List[int]
    source_ids: List[str] = field(default_factory=list)

@dataclass
class Table:
    """表構造"""
    table_bbox: List[int]
    x_headers: List[Dict] = field(default_factory=list)
    y_headers: List[Dict] = field(default_factory=list)
    cells: List[TableCell] = field(default_factory=list)

@dataclass
class AnomalyReport:
    """異常レポート"""
    type: str
    count: int
    examples: List[Dict] = field(default_factory=list)


# ============================================
# F7: Vision API OCR（検出OCR）
# ============================================

class F7VisionOCR:
    """
    F7: 検出OCRでtokens確定

    入力: page_image
    出力: tokens + tokens_low_conf + stats
    """

    def __init__(self):
        if not VISION_API_AVAILABLE:
            raise ImportError("google-cloud-vision required")
        self.extractor = VisionAPIExtractor()

    def process(
        self,
        image_path: Path,
        image_width: Optional[int] = None,
        image_height: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        F7実行: 画像からtokens抽出

        Args:
            image_path: 画像パス
            image_width: 画像幅（省略時は自動）
            image_height: 画像高さ（省略時は自動）

        Returns:
            {
                "ocr_provider": "vision_api",
                "page_size": {"w": int, "h": int},
                "tokens": [...],
                "tokens_low_conf": [...],
                "stats": {...}
            }
        """
        # DOCUMENT_TEXT_DETECTION を使用（confidence取得可能）
        return self.extractor.extract_with_document_detection(
            image_path, image_width, image_height
        )

    def process_bytes(
        self,
        image_bytes: bytes,
        image_width: int,
        image_height: int
    ) -> Dict[str, Any]:
        """バイトデータから処理"""
        return self.extractor.extract_from_bytes(
            image_bytes, image_width, image_height
        )


# ============================================
# F7.5: Suryaブロックへのマッピング（IoU優先）
# ============================================

class F75CoordinateMapper:
    """
    F7.5: OCR tokens を Surya block_id に機械的に紐付け

    マッチスコア = 0.6*IoU + 0.3*center_distance_score + 0.1*containment
    """

    # 閾値
    MATCH_THRESHOLD = 0.3         # これ未満はunmapped
    BLOCK_OVERFLOW_LIMIT = 500    # ブロック内token上限
    UNMAPPED_RATIO_ALERT = 0.5    # unmapped比率がこれ以上で警告

    def process(
        self,
        tokens: List[Dict],
        blocks: Dict[str, Dict],
        page_size: Dict[str, int]
    ) -> Dict[str, Any]:
        """
        F7.5実行: トークンをブロックにマッピング

        Args:
            tokens: F7出力の tokens
            blocks: Suryaブロック {block_id: {"bbox": [x0,y0,x1,y1]}}
            page_size: {"w": int, "h": int}

        Returns:
            {
                "mapped_tokens": [...],
                "unmapped_tokens": [...],
                "block_coverage": {...},
                "anomalies": [...]
            }
        """
        f75_start = time.time()
        logger.info(f"[F7.5] マッピング開始: {len(tokens)}tokens × {len(blocks)}blocks")

        if not tokens or not blocks:
            logger.warning("[F7.5] 入力が空")
            return {
                "mapped_tokens": [],
                "unmapped_tokens": [],
                "block_coverage": {},
                "anomalies": []
            }

        mapped_tokens = []
        unmapped_tokens = []
        block_coverage = {bid: {"tokens": [], "scores": []} for bid in blocks}
        anomalies = []

        # 画像対角線長（正規化用）
        diag = math.sqrt(page_size["w"]**2 + page_size["h"]**2)

        for token in tokens:
            t_bbox = token.get("bbox", [0, 0, 0, 0])
            text = token.get("text", "")
            conf = token.get("conf", 1.0)

            best_block_id = None
            best_score = 0.0

            for block_id, block_info in blocks.items():
                b_bbox = block_info.get("bbox", [0, 0, 0, 0])

                # スコア計算
                score = self._calc_match_score(t_bbox, b_bbox, diag)

                if score > best_score:
                    best_score = score
                    best_block_id = block_id

            # 閾値判定
            if best_score >= self.MATCH_THRESHOLD and best_block_id:
                mapped_tokens.append({
                    "id": best_block_id,
                    "text": text,
                    "bbox": t_bbox,
                    "conf": conf,
                    "score": round(best_score, 3)
                })
                block_coverage[best_block_id]["tokens"].append(text)
                block_coverage[best_block_id]["scores"].append(best_score)
            else:
                unmapped_tokens.append({
                    "text": text,
                    "bbox": t_bbox,
                    "conf": conf,
                    "reason": "no_block_over_threshold",
                    "best_score": round(best_score, 3) if best_score > 0 else None
                })

        # ブロックカバレッジ統計
        for bid in block_coverage:
            tokens_list = block_coverage[bid]["tokens"]
            scores_list = block_coverage[bid]["scores"]
            block_coverage[bid] = {
                "token_count": len(tokens_list),
                "avg_score": round(sum(scores_list) / len(scores_list), 3) if scores_list else 0
            }

            # ブロック内token過多チェック
            if len(tokens_list) > self.BLOCK_OVERFLOW_LIMIT:
                anomalies.append({
                    "type": "block_overflow",
                    "block_id": bid,
                    "token_count": len(tokens_list),
                    "limit": self.BLOCK_OVERFLOW_LIMIT
                })

        # unmapped比率チェック
        total = len(tokens)
        unmapped_ratio = len(unmapped_tokens) / total if total > 0 else 0
        if unmapped_ratio > self.UNMAPPED_RATIO_ALERT:
            anomalies.append({
                "type": "high_unmapped_ratio",
                "ratio": round(unmapped_ratio, 3),
                "unmapped_count": len(unmapped_tokens),
                "total": total
            })

        elapsed = time.time() - f75_start
        logger.info(f"[F7.5完了] mapped={len(mapped_tokens)}, unmapped={len(unmapped_tokens)}, {elapsed:.2f}秒")

        return {
            "mapped_tokens": mapped_tokens,
            "unmapped_tokens": unmapped_tokens,
            "block_coverage": block_coverage,
            "anomalies": anomalies
        }

    def _calc_match_score(
        self,
        t_bbox: List[int],
        b_bbox: List[int],
        diag: float
    ) -> float:
        """
        マッチスコア計算

        score = 0.6*IoU + 0.3*center_distance_score + 0.1*containment
        """
        # IoU
        iou = self._calc_iou(t_bbox, b_bbox)

        # 中心距離スコア（距離が近いほど1に近い）
        t_cx = (t_bbox[0] + t_bbox[2]) / 2
        t_cy = (t_bbox[1] + t_bbox[3]) / 2
        b_cx = (b_bbox[0] + b_bbox[2]) / 2
        b_cy = (b_bbox[1] + b_bbox[3]) / 2
        dist = math.sqrt((t_cx - b_cx)**2 + (t_cy - b_cy)**2)
        center_score = max(0, 1 - dist / diag)

        # containment（tokenがblockに収まる割合）
        containment = self._calc_containment(t_bbox, b_bbox)

        # 重み付け合計
        score = 0.6 * iou + 0.3 * center_score + 0.1 * containment
        return score

    def _calc_iou(self, bbox1: List[int], bbox2: List[int]) -> float:
        """IoU計算"""
        x0 = max(bbox1[0], bbox2[0])
        y0 = max(bbox1[1], bbox2[1])
        x1 = min(bbox1[2], bbox2[2])
        y1 = min(bbox1[3], bbox2[3])

        if x0 >= x1 or y0 >= y1:
            return 0.0

        intersection = (x1 - x0) * (y1 - y0)
        area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
        area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0

    def _calc_containment(self, t_bbox: List[int], b_bbox: List[int]) -> float:
        """tokenがblockに収まる割合"""
        x0 = max(t_bbox[0], b_bbox[0])
        y0 = max(t_bbox[1], b_bbox[1])
        x1 = min(t_bbox[2], b_bbox[2])
        y1 = min(t_bbox[3], b_bbox[3])

        if x0 >= x1 or y0 >= y1:
            return 0.0

        intersection = (x1 - x0) * (y1 - y0)
        token_area = (t_bbox[2] - t_bbox[0]) * (t_bbox[3] - t_bbox[1])

        return intersection / token_area if token_area > 0 else 0.0


# ============================================
# F8: 構造化（表/非表の分岐点）
# ============================================

class F8Structuring:
    """
    F8: page_type判定 + 表の物理グリッド確定

    LLMは使わない。プログラムのみ。
    """

    # 表判定閾値
    TABLE_LIKELIHOOD_THRESHOLD = 0.5
    MIN_COLUMNS = 3  # 3列以上で表の可能性
    MIN_ROWS = 3     # 3行以上で表の可能性

    def process(
        self,
        mapped_tokens: List[Dict],
        blocks: Dict[str, Dict],
        page_size: Dict[str, int]
    ) -> Dict[str, Any]:
        """
        F8実行: ページタイプ判定と構造化

        Args:
            mapped_tokens: F7.5出力
            blocks: Suryaブロック
            page_size: {"w": int, "h": int}

        Returns:
            {
                "page_type": "table|mixed|text",
                "table_likelihood": float,
                "tables": [...],  # 表がある場合
                "text_blocks": [...]  # テキストブロック
            }
        """
        f8_start = time.time()
        logger.info(f"[F8] 構造化開始: {len(mapped_tokens)}tokens")

        # ページタイプ判定
        page_type, table_likelihood = self._detect_page_type(
            mapped_tokens, blocks, page_size
        )

        result = {
            "page_type": page_type,
            "table_likelihood": round(table_likelihood, 3),
            "tables": [],
            "text_blocks": []
        }

        if page_type in ["table", "mixed"]:
            # 表構造を検出
            tables = self._detect_tables(mapped_tokens, page_size)
            result["tables"] = tables

        # 非表ブロックをテキストブロックとして収集
        result["text_blocks"] = self._collect_text_blocks(
            mapped_tokens, blocks, result.get("tables", [])
        )

        elapsed = time.time() - f8_start
        logger.info(f"[F8完了] type={page_type}, tables={len(result['tables'])}, {elapsed:.2f}秒")

        return result

    def _detect_page_type(
        self,
        tokens: List[Dict],
        blocks: Dict[str, Dict],
        page_size: Dict[str, int]
    ) -> Tuple[str, float]:
        """
        ページタイプ判定（ヒューリスティック）

        Returns:
            (page_type, table_likelihood)
        """
        if not tokens:
            return "text", 0.0

        # X方向・Y方向のクラスタリング
        x_coords = [(t["bbox"][0] + t["bbox"][2]) / 2 for t in tokens]
        y_coords = [(t["bbox"][1] + t["bbox"][3]) / 2 for t in tokens]

        # X方向のクラスタ数（列数の近似）
        x_clusters = self._count_clusters(x_coords, page_size["w"] * 0.05)

        # Y方向のクラスタ数（行数の近似）
        y_clusters = self._count_clusters(y_coords, page_size["h"] * 0.02)

        # 同一Y帯に複数X座標がある割合
        grid_ratio = self._calc_grid_ratio(tokens, page_size)

        # 表らしさスコア
        likelihood = 0.0
        if x_clusters >= self.MIN_COLUMNS:
            likelihood += 0.4
        if y_clusters >= self.MIN_ROWS:
            likelihood += 0.2
        if grid_ratio > 0.3:
            likelihood += 0.4

        # 判定
        if likelihood >= self.TABLE_LIKELIHOOD_THRESHOLD:
            if likelihood >= 0.8:
                return "table", likelihood
            else:
                return "mixed", likelihood
        else:
            return "text", likelihood

    def _count_clusters(self, coords: List[float], tolerance: float) -> int:
        """座標のクラスタ数をカウント"""
        if not coords:
            return 0

        sorted_coords = sorted(coords)
        clusters = 1
        prev = sorted_coords[0]

        for c in sorted_coords[1:]:
            if c - prev > tolerance:
                clusters += 1
            prev = c

        return clusters

    def _calc_grid_ratio(
        self,
        tokens: List[Dict],
        page_size: Dict[str, int]
    ) -> float:
        """グリッド状配置の割合"""
        if not tokens:
            return 0.0

        # Y座標でグループ化
        y_tolerance = page_size["h"] * 0.02
        rows = {}

        for t in tokens:
            y = (t["bbox"][1] + t["bbox"][3]) / 2
            row_key = int(y / y_tolerance)
            if row_key not in rows:
                rows[row_key] = []
            rows[row_key].append(t)

        # 2つ以上のトークンを持つ行の割合
        multi_token_rows = sum(1 for r in rows.values() if len(r) >= 2)
        return multi_token_rows / len(rows) if rows else 0.0

    def _detect_tables(
        self,
        tokens: List[Dict],
        page_size: Dict[str, int]
    ) -> List[Dict]:
        """
        表構造を検出（プログラムのみ）

        tokensを行クラスタ(y)→列クラスタ(x)に分けて表を構築
        """
        if not tokens:
            return []

        # Y座標でソート
        y_tolerance = page_size["h"] * 0.015

        # 行にグループ化
        rows = []
        sorted_tokens = sorted(tokens, key=lambda t: (t["bbox"][1] + t["bbox"][3]) / 2)

        current_row = [sorted_tokens[0]]
        current_y = (sorted_tokens[0]["bbox"][1] + sorted_tokens[0]["bbox"][3]) / 2

        for t in sorted_tokens[1:]:
            t_y = (t["bbox"][1] + t["bbox"][3]) / 2
            if abs(t_y - current_y) < y_tolerance:
                current_row.append(t)
            else:
                rows.append(sorted(current_row, key=lambda x: x["bbox"][0]))
                current_row = [t]
                current_y = t_y

        if current_row:
            rows.append(sorted(current_row, key=lambda x: x["bbox"][0]))

        if len(rows) < self.MIN_ROWS:
            return []

        # 表のbbox計算
        all_x0 = min(t["bbox"][0] for row in rows for t in row)
        all_y0 = min(t["bbox"][1] for row in rows for t in row)
        all_x1 = max(t["bbox"][2] for row in rows for t in row)
        all_y1 = max(t["bbox"][3] for row in rows for t in row)

        # ヘッダー推定（最上段 = x_headers、最左列 = y_headers）
        x_headers = []
        y_headers = []

        if rows:
            # 最上段をx_headersに
            for t in rows[0]:
                x_headers.append({
                    "text": t["text"],
                    "bbox": t["bbox"]
                })

            # 各行の最左をy_headersに
            for row in rows[1:]:  # 最上段を除く
                if row:
                    y_headers.append({
                        "text": row[0]["text"],
                        "bbox": row[0]["bbox"]
                    })

        # セル構築
        cells = []
        for row_idx, row in enumerate(rows):
            for col_idx, t in enumerate(row):
                cells.append({
                    "row": row_idx,
                    "col": col_idx,
                    "text": t["text"],
                    "bbox": t["bbox"],
                    "source_ids": [t.get("id", "")]
                })

        return [{
            "table_bbox": [all_x0, all_y0, all_x1, all_y1],
            "x_headers": x_headers,
            "y_headers": y_headers,
            "cells": cells,
            "row_count": len(rows),
            "col_count": max(len(row) for row in rows) if rows else 0
        }]

    def _collect_text_blocks(
        self,
        tokens: List[Dict],
        blocks: Dict[str, Dict],
        tables: List[Dict]
    ) -> List[Dict]:
        """表以外のテキストブロックを収集"""
        # 表に含まれるトークンを除外
        table_bboxes = [t["table_bbox"] for t in tables]

        text_tokens = []
        for t in tokens:
            in_table = False
            t_cx = (t["bbox"][0] + t["bbox"][2]) / 2
            t_cy = (t["bbox"][1] + t["bbox"][3]) / 2

            for tb in table_bboxes:
                if tb[0] <= t_cx <= tb[2] and tb[1] <= t_cy <= tb[3]:
                    in_table = True
                    break

            if not in_table:
                text_tokens.append(t)

        return text_tokens


# ============================================
# F9: 物理ソート + タグ付け
# ============================================

class F9PhysicalTagger:
    """
    F9: Programでタグ付け・物理ソート

    正規表現 + 近傍ルールで住所/郵便番号/電話などを検出
    """

    # 正規表現パターン
    PATTERNS = {
        "postal_code": re.compile(r'〒?\d{3}[-ー]\d{4}'),
        "phone": re.compile(r'[\d\-ー()（）]{10,}'),
        "date": re.compile(r'\d{4}[年/\-]\d{1,2}[月/\-]\d{1,2}日?'),
        "time": re.compile(r'\d{1,2}:\d{2}'),
        "email": re.compile(r'[\w\.\-]+@[\w\.\-]+'),
        "url": re.compile(r'https?://[\w\./\-]+'),
        "address": re.compile(r'(東京都|北海道|(?:京都|大阪)府|.{2,3}県).{2,}'),
        "money": re.compile(r'[¥￥]\s*[\d,]+|[\d,]+\s*円'),
    }

    def process(
        self,
        mapped_tokens: List[Dict],
        f8_result: Dict[str, Any],
        page_size: Dict[str, int]
    ) -> Dict[str, Any]:
        """
        F9実行: タグ付けと物理ソート

        Args:
            mapped_tokens: F7.5出力
            f8_result: F8出力
            page_size: ページサイズ

        Returns:
            {
                "tagged_texts": [...],
                "tables": [...],
                "low_confidence": [...]
            }
        """
        f9_start = time.time()
        logger.info(f"[F9] タグ付け開始: {len(mapped_tokens)}tokens")

        tagged_texts = []
        low_confidence = []

        for t in mapped_tokens:
            text = t.get("text", "")
            conf = t.get("conf", 1.0)
            bbox = t.get("bbox", [])
            block_id = t.get("id", "")

            # タグ検出
            tag = self._detect_tag(text)

            tagged = {
                "id": block_id,
                "text": text,
                "tag": tag,
                "bbox": bbox,
                "conf": conf,
                "score": t.get("score", 0)
            }

            # 低信頼度判定
            # Vision APIのconf（OCR信頼度）が高ければ、マッチスコアが低くてもOK
            # マッチスコアはブロックとの位置関係であり、OCR精度とは別
            match_score = t.get("score", 1.0)
            is_low_conf = conf < 0.5  # OCR信頼度が0.5未満
            is_very_low_match = match_score < 0.2  # マッチスコアが極端に低い

            if is_low_conf or is_very_low_match:
                tagged["why"] = "low_ocr_conf" if is_low_conf else "very_low_match"
                low_confidence.append(tagged)
            else:
                tagged_texts.append(tagged)

        # 物理ソート（Y座標優先 → X座標）
        tagged_texts.sort(key=lambda x: (
            x["bbox"][1] if x["bbox"] else 0,
            x["bbox"][0] if x["bbox"] else 0
        ))

        elapsed = time.time() - f9_start
        logger.info(f"[F9完了] tagged={len(tagged_texts)}, low_conf={len(low_confidence)}, {elapsed:.2f}秒")

        return {
            "tagged_texts": tagged_texts,
            "tables": f8_result.get("tables", []),
            "low_confidence": low_confidence
        }

    def _detect_tag(self, text: str) -> Optional[str]:
        """正規表現でタグ検出"""
        for tag_name, pattern in self.PATTERNS.items():
            if pattern.search(text):
                return tag_name
        return None


# ============================================
# F9.5: AIレスキュー（選択問題のみ）
# ============================================

class F95AIRescue:
    """
    F9.5: 低信頼度データのLLM救済

    ルール:
    - OCRはしない（文字起こし依頼禁止）
    - 選択問題にする（候補タグを2〜5個に絞って「どれ？」だけ聞く）
    """

    # 救済対象の最大件数
    MAX_RESCUE_COUNT = 20

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMクライアント（Noneなら救済スキップ）
        """
        self.llm_client = llm_client

    def process(
        self,
        low_confidence: List[Dict],
        context_tokens: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        F9.5実行: 少数の低信頼度データを救済

        Args:
            low_confidence: F9の低信頼度リスト
            context_tokens: 周辺コンテキスト（参考情報）

        Returns:
            {
                "patches": [...],
                "skipped": int,
                "rescued": int
            }
        """
        f95_start = time.time()

        if not self.llm_client:
            logger.info("[F9.5] LLMクライアントなし - スキップ")
            return {"patches": [], "skipped": len(low_confidence), "rescued": 0}

        # 件数制限
        targets = low_confidence[:self.MAX_RESCUE_COUNT]
        skipped = len(low_confidence) - len(targets)

        if not targets:
            return {"patches": [], "skipped": 0, "rescued": 0}

        logger.info(f"[F9.5] AI救済開始: {len(targets)}件（{skipped}件スキップ）")

        patches = []

        for item in targets:
            text = item.get("text", "")
            # 選択問題形式でタグを問う
            patch = self._rescue_single(text, item)
            if patch:
                patches.append(patch)

        elapsed = time.time() - f95_start
        logger.info(f"[F9.5完了] rescued={len(patches)}, {elapsed:.2f}秒")

        return {
            "patches": patches,
            "skipped": skipped,
            "rescued": len(patches)
        }

    def _rescue_single(self, text: str, item: Dict) -> Optional[Dict]:
        """
        単一アイテムの救済（選択問題）

        LLMに「このテキストのタグは？」を選択肢で問う
        """
        if not text or len(text) < 2:
            return None

        # 候補タグ
        candidates = ["address", "phone", "date", "postal_code", "other", "unclear"]

        prompt = f"""以下のテキストに最も適切なタグを1つ選んでください。

テキスト: "{text}"

選択肢:
1. address（住所）
2. phone（電話番号）
3. date（日付）
4. postal_code（郵便番号）
5. other（その他）
6. unclear（判別不能）

回答は番号のみ（例: 1）で答えてください。"""

        try:
            response = self.llm_client.call_model(
                tier="fast",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.0
            )

            # 番号を抽出
            answer = response.get("content", "").strip()
            num = int(re.search(r'\d', answer).group()) if re.search(r'\d', answer) else 6

            if 1 <= num <= len(candidates):
                tag = candidates[num - 1]
                if tag not in ["other", "unclear"]:
                    return {
                        "id": item.get("id", ""),
                        "text": text,
                        "tag": tag,
                        "confidence": "rescued"
                    }
        except Exception as e:
            logger.warning(f"[F9.5] 救済失敗: {e}")

        return None


# ============================================
# F10: 正本化 + 異常レポート
# ============================================

class F10Scrubbing:
    """
    F10: 異常排除 + 正本化 + anomaly_report

    役割:
    1. 異常排除（同一bbox重複、微小bbox、密度異常など）
    2. Stage Eテキストでの洗い替え（オプション）
    3. 異常レポート生成
    """

    # 閾値
    MIN_BBOX_AREA = 25           # 面積がこれ未満は微小
    DENSITY_THRESHOLD = 100      # 100px四方あたりのtoken上限

    def process(
        self,
        f9_result: Dict[str, Any],
        f75_anomalies: List[Dict],
        stage_e_text: str = None
    ) -> Dict[str, Any]:
        """
        F10実行: 正本化と異常レポート

        Args:
            f9_result: F9出力
            f75_anomalies: F7.5の異常リスト
            stage_e_text: Stage Eのテキスト（洗い替え用）

        Returns:
            {
                "final_tokens": [...],
                "final_tables": [...],
                "anomaly_report": [...],
                "stop_reason": "OK|COORD_MISMATCH|ANOMALY_FLOOD"
            }
        """
        f10_start = time.time()
        logger.info("[F10] 正本化開始")

        tagged_texts = f9_result.get("tagged_texts", [])
        tables = f9_result.get("tables", [])
        low_conf = f9_result.get("low_confidence", [])

        anomaly_report = list(f75_anomalies)  # F7.5の異常を引き継ぐ

        # 1. 異常排除
        final_tokens, new_anomalies = self._remove_anomalies(tagged_texts)
        anomaly_report.extend(new_anomalies)

        # 2. 低信頼度トークンの異常チェック
        low_conf_anomalies = self._check_low_confidence(low_conf)
        anomaly_report.extend(low_conf_anomalies)

        # 3. 停止理由判定
        stop_reason = self._determine_stop_reason(anomaly_report)

        # 4. Stage Eテキストでの洗い替え（オプション）
        if stage_e_text and stop_reason == "OK":
            final_tokens = self._scrub_with_stage_e(final_tokens, stage_e_text)

        elapsed = time.time() - f10_start
        logger.info(f"[F10完了] tokens={len(final_tokens)}, anomalies={len(anomaly_report)}, {elapsed:.2f}秒")

        return {
            "final_tokens": final_tokens,
            "final_tables": tables,
            "anomaly_report": anomaly_report,
            "stop_reason": stop_reason
        }

    def _remove_anomalies(
        self,
        tokens: List[Dict]
    ) -> Tuple[List[Dict], List[Dict]]:
        """異常トークンを除去"""
        clean_tokens = []
        anomalies = []

        seen_bboxes = set()
        duplicate_count = 0
        tiny_count = 0

        for t in tokens:
            bbox = tuple(t.get("bbox", [0, 0, 0, 0]))

            # 重複bbox
            if bbox in seen_bboxes:
                duplicate_count += 1
                continue
            seen_bboxes.add(bbox)

            # 微小bbox
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area < self.MIN_BBOX_AREA:
                tiny_count += 1
                continue

            clean_tokens.append(t)

        if duplicate_count > 0:
            anomalies.append({
                "type": "duplicate_bbox",
                "count": duplicate_count
            })

        if tiny_count > 0:
            anomalies.append({
                "type": "tiny_bbox",
                "count": tiny_count
            })

        return clean_tokens, anomalies

    def _check_low_confidence(self, low_conf: List[Dict]) -> List[Dict]:
        """低信頼度トークンの異常チェック"""
        anomalies = []

        if len(low_conf) > 50:
            anomalies.append({
                "type": "many_low_confidence",
                "count": len(low_conf)
            })

        return anomalies

    def _determine_stop_reason(self, anomalies: List[Dict]) -> str:
        """停止理由を判定"""
        # 重大な異常があればアラート
        for a in anomalies:
            if a.get("type") == "high_unmapped_ratio":
                return "COORD_MISMATCH"
            if a.get("type") == "block_overflow":
                return "ANOMALY_FLOOD"

        # 異常が多すぎる
        if len(anomalies) > 10:
            return "ANOMALY_FLOOD"

        return "OK"

    def _scrub_with_stage_e(
        self,
        tokens: List[Dict],
        stage_e_text: str
    ) -> List[Dict]:
        """Stage Eテキストで洗い替え（将来実装）"""
        # TODO: Stage Eの座標付きテキストとのマッチング
        return tokens


# ============================================
# 統合パイプライン
# ============================================

class F7toF10Pipeline:
    """
    F7〜F10 統合パイプライン

    検出OCR版の新ライン
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMクライアント（F9.5用、Noneならスキップ）
        """
        self.f7 = F7VisionOCR()
        self.f75 = F75CoordinateMapper()
        self.f8 = F8Structuring()
        self.f9 = F9PhysicalTagger()
        self.f95 = F95AIRescue(llm_client)
        self.f10 = F10Scrubbing()

    def process(
        self,
        image_path: Path,
        surya_blocks: Dict[str, Dict],
        stage_e_text: str = None,
        image_width: int = None,
        image_height: int = None
    ) -> Dict[str, Any]:
        """
        F7〜F10 全パイプライン実行

        Args:
            image_path: 画像パス
            surya_blocks: Suryaブロック {block_id: {"bbox": [x0,y0,x1,y1]}}
            stage_e_text: Stage Eテキスト（F10洗い替え用）
            image_width: 画像幅
            image_height: 画像高さ

        Returns:
            最終出力
        """
        total_start = time.time()
        logger.info(f"[Pipeline] F7-F10開始: {image_path.name}")

        # F7: Vision API OCR
        f7_result = self.f7.process(image_path, image_width, image_height)
        page_size = f7_result["page_size"]

        # F7.5: Suryaマッピング
        f75_result = self.f75.process(
            f7_result["tokens"],
            surya_blocks,
            page_size
        )

        # F8: 構造化
        f8_result = self.f8.process(
            f75_result["mapped_tokens"],
            surya_blocks,
            page_size
        )

        # F9: タグ付け
        f9_result = self.f9.process(
            f75_result["mapped_tokens"],
            f8_result,
            page_size
        )

        # F9.5: AI救済
        f95_result = self.f95.process(
            f9_result["low_confidence"]
        )

        # パッチ適用
        if f95_result["patches"]:
            for patch in f95_result["patches"]:
                for t in f9_result["tagged_texts"]:
                    if t.get("id") == patch.get("id"):
                        t["tag"] = patch["tag"]
                        t["conf"] = "rescued"

        # F10: 正本化
        f10_result = self.f10.process(
            f9_result,
            f75_result.get("anomalies", []),
            stage_e_text
        )

        total_elapsed = time.time() - total_start
        logger.info(f"[Pipeline完了] {total_elapsed:.2f}秒, stop={f10_result['stop_reason']}")

        return {
            "f7": {
                "token_count": len(f7_result["tokens"]),
                "low_conf_count": len(f7_result["tokens_low_conf"]),
                "stats": f7_result["stats"]
            },
            "f75": {
                "mapped": len(f75_result["mapped_tokens"]),
                "unmapped": len(f75_result["unmapped_tokens"]),
                "anomalies": f75_result["anomalies"]
            },
            "f8": {
                "page_type": f8_result["page_type"],
                "table_count": len(f8_result["tables"])
            },
            "f9": {
                "tagged_count": len(f9_result["tagged_texts"]),
                "low_conf_count": len(f9_result["low_confidence"])
            },
            "f95": {
                "rescued": f95_result["rescued"],
                "skipped": f95_result["skipped"]
            },
            "f10": f10_result,
            "elapsed": round(total_elapsed, 2)
        }
