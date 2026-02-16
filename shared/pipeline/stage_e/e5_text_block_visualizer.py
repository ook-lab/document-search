"""
E-5: Text Block Visualizer（視覚的ブロック認識）

画像内の文字の密集度から「テキストブロック」を特定し、
AIが順序を間違えないためのインデックスを作成する。

目的:
1. 意味的な塊（段落、セクション、カラム）を抽出
2. 空間インデックス（座標メタデータ）を作成
3. Gemini へのプロンプト拡張（座標ヒント）
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from loguru import logger
import numpy as np

try:
    import cv2
    from PIL import Image, ImageDraw, ImageFont
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("[E-5] OpenCV/PIL がインストールされていません")


class E5TextBlockVisualizer:
    """E-5: Text Block Visualizer（視覚的ブロック認識）"""

    def __init__(
        self,
        min_block_size: int = 50,      # 最小ブロックサイズ（ピクセル）
        merge_threshold: int = 30,     # ブロック統合の閾値（ピクセル）
        draw_overlay: bool = False     # デバッグ用オーバーレイを描画するか
    ):
        """
        Text Block Visualizer 初期化

        Args:
            min_block_size: 最小ブロックサイズ（ピクセル）
            merge_threshold: ブロック統合の閾値（ピクセル）
            draw_overlay: デバッグ用オーバーレイを描画するか
        """
        self.min_block_size = min_block_size
        self.merge_threshold = merge_threshold
        self.draw_overlay = draw_overlay

        if not CV2_AVAILABLE:
            logger.error("[E-5] OpenCV/PIL が必要です")

    def detect_blocks(
        self,
        image_path: Path,
        ocr_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        画像からテキストブロックを検出

        Args:
            image_path: 画像ファイルパス
            ocr_text: E-1で抽出されたテキスト（オプション）

        Returns:
            {
                'blocks': [
                    {
                        'block_id': int,
                        'type': str,  # 'paragraph', 'title', 'list'
                        'bbox': [x0, y0, x1, y1],
                        'bbox_normalized': [x0, y0, x1, y1],
                        'text_hint': str  # OCRテキストの一部
                    },
                    ...
                ],
                'image_size': (width, height),
                'overlay_image_path': str  # デバッグ用
            }
        """
        if not CV2_AVAILABLE:
            logger.error("[E-5] OpenCV/PIL が利用できません")
            return self._empty_result()

        logger.info("=" * 80)
        logger.info(f"[E-5] ブロック検出開始: {image_path.name}")
        logger.info("=" * 80)

        try:
            # 画像を読み込み
            image = cv2.imread(str(image_path))
            if image is None:
                logger.error(f"[E-5] 画像読み込み失敗: {image_path}")
                return self._empty_result()

            height, width = image.shape[:2]
            logger.info(f"[E-5] 画像サイズ: {width}x{height} pixels")

            # グレースケール変換
            logger.info("[E-5] グレースケール変換実行")
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # 二値化
            logger.info("[E-5] 二値化実行（Otsu法）")
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # モルフォロジー変換（テキストを繋げる）
            logger.info(f"[E-5] モルフォロジー変換実行（kernel=15x15, iterations=2）")
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
            dilated = cv2.dilate(binary, kernel, iterations=2)

            # 輪郭抽出
            logger.info("[E-5] 輪郭抽出実行")
            contours, _ = cv2.findContours(
                dilated,
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )
            logger.info(f"[E-5] 輪郭数: {len(contours)}")

            # ブロックを抽出
            blocks = []
            filtered_out = 0

            for idx, contour in enumerate(contours):
                x, y, w, h = cv2.boundingRect(contour)

                # 最小サイズフィルタ
                if w < self.min_block_size or h < self.min_block_size:
                    filtered_out += 1
                    logger.debug(f"[E-5] 輪郭#{idx}: サイズ不足でスキップ (w={w}, h={h})")
                    continue

                # 正規化座標
                bbox_normalized = [
                    x / width,
                    y / height,
                    (x + w) / width,
                    (y + h) / height
                ]

                # ブロックタイプの簡易判定
                block_type = self._classify_block_type(w, h, width, height)

                blocks.append({
                    'block_id': idx + 1,
                    'type': block_type,
                    'bbox': [x, y, x + w, y + h],
                    'bbox_normalized': bbox_normalized,
                    'text_hint': ''  # TODO: OCRテキストを割り当て
                })

                logger.debug(f"[E-5] ブロック追加: type={block_type}, bbox=({x},{y})-({x+w},{y+h})")

            logger.info(f"[E-5] フィルタ結果: {len(blocks)}個採用, {filtered_out}個除外")

            # Y座標（上から下）でソート
            blocks.sort(key=lambda b: b['bbox'][1])
            logger.info("[E-5] ブロックをY座標順にソート")

            # ブロックIDを振り直し
            for idx, block in enumerate(blocks):
                block['block_id'] = idx + 1

            logger.info(f"[E-5] 検出完了: {len(blocks)}ブロック")

            # ブロックの詳細出力
            logger.info("[E-5] ===== 検出ブロック詳細 =====")
            for block in blocks:
                logger.info(f"Block {block['block_id']}: type={block['type']}, "
                          f"bbox={block['bbox']}, bbox_norm={block['bbox_normalized']}")
            logger.info("[E-5] ===== ブロック詳細終了 =====")

            # デバッグ用オーバーレイ
            overlay_path = None
            if self.draw_overlay and blocks:
                logger.info("[E-5] デバッグオーバーレイ描画開始")
                overlay_path = self._draw_overlay(image_path, blocks)

            logger.info("=" * 80)
            logger.info(f"[E-5] ブロック検出完了: {len(blocks)}個")
            if overlay_path:
                logger.info(f"  └─ オーバーレイ: {overlay_path}")
            logger.info("=" * 80)

            return {
                'blocks': blocks,
                'image_size': (width, height),
                'overlay_image_path': overlay_path if overlay_path else ''
            }

        except Exception as e:
            logger.error(f"[E-5] 検出エラー: {e}", exc_info=True)
            return self._empty_result()

    def _classify_block_type(
        self,
        width: int,
        height: int,
        image_width: int,
        image_height: int
    ) -> str:
        """
        ブロックタイプを簡易的に分類

        Args:
            width: ブロック幅
            height: ブロック高さ
            image_width: 画像幅
            image_height: 画像高さ

        Returns:
            'title', 'paragraph', 'list'
        """
        # 幅が画像の50%以上かつ高さが小さい → タイトル
        if width > image_width * 0.5 and height < image_height * 0.1:
            return 'title'
        # 幅が狭い → リスト
        elif width < image_width * 0.3:
            return 'list'
        # デフォルト → 段落
        else:
            return 'paragraph'

    def _draw_overlay(
        self,
        image_path: Path,
        blocks: List[Dict[str, Any]]
    ) -> Optional[str]:
        """
        デバッグ用のオーバーレイ画像を描画

        Args:
            image_path: 元画像パス
            blocks: ブロックリスト

        Returns:
            オーバーレイ画像パス
        """
        try:
            image = Image.open(str(image_path))
            draw = ImageDraw.Draw(image)

            # ブロックごとに枠と番号を描画
            for block in blocks:
                bbox = block['bbox']
                block_id = block['block_id']

                # 枠線
                draw.rectangle(bbox, outline='red', width=3)

                # ブロック番号
                try:
                    font = ImageFont.truetype("arial.ttf", 30)
                except:
                    font = ImageFont.load_default()

                text = f"Block {block_id}"
                draw.text((bbox[0] + 5, bbox[1] + 5), text, fill='red', font=font)

            # 保存
            overlay_path = image_path.parent / f"e5_{image_path.stem}_blocks.png"
            image.save(str(overlay_path))
            logger.info(f"[E-5] オーバーレイ保存: {overlay_path.name}")

            return str(overlay_path)

        except Exception as e:
            logger.warning(f"[E-5] オーバーレイ描画エラー: {e}")
            return None

    def generate_prompt_hint(
        self,
        blocks: List[Dict[str, Any]]
    ) -> str:
        """
        Gemini へのプロンプトヒントを生成

        Args:
            blocks: ブロックリスト

        Returns:
            プロンプトヒント文字列
        """
        if not blocks:
            logger.info("[E-5] ブロックなし → プロンプトヒント空")
            return ""

        hint_lines = ["画像内のテキストブロック構造:"]

        for block in blocks:
            block_id = block['block_id']
            block_type = block['type']
            bbox = block['bbox_normalized']

            hint_lines.append(
                f"- Block {block_id} ({block_type}): "
                f"y={bbox[1]:.2f}-{bbox[3]:.2f}, x={bbox[0]:.2f}-{bbox[2]:.2f}"
            )

        hint_lines.append("\nこのブロック順序に従って情報を抽出してください。")

        hint = "\n".join(hint_lines)

        logger.info(f"[E-5] プロンプトヒント生成完了: {len(blocks)}ブロック, {len(hint)}文字")
        logger.info("[E-5] ===== プロンプトヒント =====")
        logger.info(hint)
        logger.info("[E-5] ===== ヒント終了 =====")

        return hint

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'blocks': [],
            'image_size': (0, 0),
            'overlay_image_path': ''
        }
