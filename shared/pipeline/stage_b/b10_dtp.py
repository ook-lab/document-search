"""
B-10: DTP Processor（InDesign由来PDF専用）

pdfplumber を使用して、InDesign由来PDFから構造化データを抽出。
テキストボックス単位で抽出し、近接ボックスをマージ。
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B10DtpProcessor:
    """B-10: DTP Processor（InDesign由来PDF専用）"""

    # 近接ボックスのマージ閾値（pt）
    MERGE_THRESHOLD = 5.0

    def process(self, file_path: Path) -> Dict[str, Any]:
        """
        InDesign由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'is_structured': bool,
                'text_with_tags': str,           # 全ボックスのテキスト
                'logical_blocks': [...],         # マージ済みテキストボックス
                'structured_tables': [...],      # 表構造データ
                'tags': {...},                   # メタ情報
                'purged_image_path': str         # テキスト消去後の画像
            }
        """
        logger.info(f"[B-10] DTP処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-10] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                # 全ページを処理
                logical_blocks = []
                all_tables = []

                for page_num, page in enumerate(pdf.pages):
                    # テキストボックス単位で抽出
                    textboxes = self._extract_textboxes(page, page_num)

                    # 近接ボックスをマージ
                    merged_boxes = self._merge_nearby_boxes(textboxes)

                    logical_blocks.extend(merged_boxes)

                    # 表を抽出
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)

                # テキストを生成
                text_with_tags = self._build_text(logical_blocks)

                # メタ情報
                tags = {
                    'page_count': len(pdf.pages),
                    'textbox_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'is_dtp': True
                }

                logger.info(f"[B-10] 抽出完了: テキストボックス={len(logical_blocks)}, 表={len(all_tables)}")

                return {
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'purged_image_path': ''  # TODO: Layer Purge
                }

        except Exception as e:
            logger.error(f"[B-10] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _extract_textboxes(self, page, page_num: int) -> List[Dict[str, Any]]:
        """
        テキストボックス単位で抽出（Object単位）

        Args:
            page: pdfplumberのPageオブジェクト
            page_num: ページ番号

        Returns:
            [{
                'page': int,
                'bbox': tuple,
                'text': str,
                'chars': [...]
            }, ...]
        """
        # pdfplumberでは、charsをグループ化してボックスを推定
        # ここでは簡易的に、Y座標とX座標が近い文字をまとめる
        chars = page.chars
        if not chars:
            return []

        # X座標でソート
        chars_sorted = sorted(chars, key=lambda c: (c['top'], c['x0']))

        # グループ化（Y座標が10pt以内、X座標が20pt以内なら同じボックス）
        textboxes = []
        current_box_chars = []
        prev_y = None
        prev_x = None

        for char in chars_sorted:
            if prev_y is None:
                current_box_chars.append(char)
            else:
                # Y座標が近く、X座標も連続している
                if abs(char['top'] - prev_y) < 10 and abs(char['x0'] - prev_x) < 20:
                    current_box_chars.append(char)
                else:
                    # 新しいボックスを開始
                    if current_box_chars:
                        textboxes.append(self._create_textbox(current_box_chars, page_num))
                    current_box_chars = [char]

            prev_y = char['top']
            prev_x = char['x1']

        if current_box_chars:
            textboxes.append(self._create_textbox(current_box_chars, page_num))

        return textboxes

    def _create_textbox(self, chars: List[Dict], page_num: int) -> Dict[str, Any]:
        """
        文字リストからテキストボックスを生成

        Args:
            chars: 文字リスト
            page_num: ページ番号

        Returns:
            {
                'page': int,
                'bbox': tuple,
                'text': str,
                'chars': [...]
            }
        """
        text = ''.join([c['text'] for c in chars])
        bbox = (
            min(c['x0'] for c in chars),
            min(c['top'] for c in chars),
            max(c['x1'] for c in chars),
            max(c['bottom'] for c in chars)
        )

        return {
            'page': page_num,
            'bbox': bbox,
            'text': text,
            'chars': chars
        }

    def _merge_nearby_boxes(self, textboxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        近接するテキストボックスをマージ

        Args:
            textboxes: テキストボックスリスト

        Returns:
            マージ済みテキストボックスリスト
        """
        if not textboxes:
            return []

        # 距離行列を計算
        merged = []
        used = set()

        for i, box1 in enumerate(textboxes):
            if i in used:
                continue

            # box1に近いボックスを探す
            nearby = [box1]
            used.add(i)

            for j, box2 in enumerate(textboxes):
                if j in used or j <= i:
                    continue

                # 距離を計算
                if self._is_nearby(box1['bbox'], box2['bbox']):
                    nearby.append(box2)
                    used.add(j)

            # 近接ボックスをマージ
            merged_box = self._merge_boxes(nearby)
            merged.append(merged_box)

        logger.debug(f"[B-10] マージ: {len(textboxes)}個 → {len(merged)}個")
        return merged

    def _is_nearby(self, bbox1: Tuple, bbox2: Tuple) -> bool:
        """
        2つのbboxが近接しているか判定

        Args:
            bbox1, bbox2: (x0, y0, x1, y1)

        Returns:
            近接している場合True
        """
        x0_1, y0_1, x1_1, y1_1 = bbox1
        x0_2, y0_2, x1_2, y1_2 = bbox2

        # 水平方向の距離
        h_dist = min(abs(x0_1 - x1_2), abs(x0_2 - x1_1))
        # 垂直方向の距離
        v_dist = min(abs(y0_1 - y1_2), abs(y0_2 - y1_1))

        # いずれかが閾値以内なら近接
        return h_dist < self.MERGE_THRESHOLD or v_dist < self.MERGE_THRESHOLD

    def _merge_boxes(self, boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        複数のボックスを1つにマージ

        Args:
            boxes: ボックスリスト

        Returns:
            マージ済みボックス
        """
        texts = [box['text'] for box in boxes]
        merged_text = ' '.join(texts)

        all_bboxes = [box['bbox'] for box in boxes]
        merged_bbox = (
            min(b[0] for b in all_bboxes),
            min(b[1] for b in all_bboxes),
            max(b[2] for b in all_bboxes),
            max(b[3] for b in all_bboxes)
        )

        return {
            'page': boxes[0]['page'],
            'bbox': merged_bbox,
            'text': merged_text,
            'merged_count': len(boxes)
        }

    def _extract_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        """
        ページ内の表を抽出

        Args:
            page: pdfplumberのPageオブジェクト
            page_num: ページ番号

        Returns:
            [{
                'page': int,
                'index': int,
                'bbox': tuple,
                'data': [[...], [...], ...]
            }, ...]
        """
        tables = []
        for idx, table in enumerate(page.find_tables()):
            tables.append({
                'page': page_num,
                'index': idx,
                'bbox': table.bbox,
                'data': table.extract()
            })
        return tables

    def _build_text(self, logical_blocks: List[Dict[str, Any]]) -> str:
        """
        全ボックスのテキストを生成

        Args:
            logical_blocks: テキストボックスリスト

        Returns:
            [TEXTBOX page=X]...[/TEXTBOX] 形式
        """
        result = []

        for block in logical_blocks:
            header = f"[TEXTBOX page={block['page']}]"
            footer = "[/TEXTBOX]"
            result.append(f"{header}\n{block['text']}\n{footer}")

        return "\n\n".join(result)

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'is_structured': False,
            'error': error_message,
            'text_with_tags': '',
            'logical_blocks': [],
            'structured_tables': [],
            'tags': {},
            'purged_image_path': ''
        }
