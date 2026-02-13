"""
B-30: DTP Processor（InDesign由来PDF専用）

pdfplumber を使用して、InDesign由来PDFから構造化データを抽出。
テキストボックス単位で抽出し、近接ボックスをマージ。
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B30DtpProcessor:
    """B-30: DTP Processor（InDesign由来PDF専用）"""

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
        logger.info(f"[B-30] DTP処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-30] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                # 全ページを処理
                logical_blocks = []
                all_tables = []
                all_words = []  # 削除対象の全単語

                for page_num, page in enumerate(pdf.pages):
                    # テキストボックス単位で抽出
                    textboxes = self._extract_textboxes(page, page_num)

                    # 近接ボックスをマージ
                    merged_boxes = self._merge_nearby_boxes(textboxes)

                    logical_blocks.extend(merged_boxes)

                    # 表を抽出
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)

                    # 削除用：ページ全体の単語を収集
                    page_words = page.extract_words(
                        x_tolerance=3,
                        y_tolerance=3,
                        keep_blank_chars=False
                    )
                    for word in page_words:
                        all_words.append({
                            'page': page_num,
                            'text': word['text'],
                            'bbox': (word['x0'], word['top'], word['x1'], word['bottom'])
                        })

                # テキストを生成
                text_with_tags = self._build_text(logical_blocks)

                # メタ情報
                tags = {
                    'page_count': len(pdf.pages),
                    'textbox_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'is_dtp': True
                }

                logger.info(f"[B-30] 抽出完了: テキストボックス={len(logical_blocks)}, 表={len(all_tables)}, 単語（削除対象）={len(all_words)}")

                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-30] テキスト削除完了: {purged_pdf_path}")

                return {
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'all_words': all_words,
                    'purged_pdf_path': str(purged_pdf_path)
                }

        except Exception as e:
            logger.error(f"[B-30] 処理エラー: {e}", exc_info=True)
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

        logger.debug(f"[B-30] マージ: {len(textboxes)}個 → {len(merged)}個")
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

    def _purge_extracted_text(
        self,
        file_path: Path,
        all_words: List[Dict[str, Any]],
        structured_tables: List[Dict[str, Any]] = None
    ) -> Path:
        """
        抽出したテキストを PDF から直接削除

        フェーズ1: テキスト（words）を常に削除
        フェーズ2: 表の罫線（graphics）を条件付きで削除
          - structured_tables が抽出済み -> 削除（Stage D の二重検出を防ぐ）
          - structured_tables が空 -> 保持（Stage D が検出できるよう残す）
        """
        try:
            import fitz
        except ImportError:
            logger.error("[B-30] PyMuPDF がインストールされていません")
            return file_path

        try:
            doc = fitz.open(str(file_path))

            words_by_page: Dict[int, List[Dict]] = {}
            for word in all_words:
                words_by_page.setdefault(word['page'], []).append(word)

            tables_by_page: Dict[int, List[Dict]] = {}
            if structured_tables:
                for table in structured_tables:
                    pn = table.get('page', 0)
                    tables_by_page.setdefault(pn, []).append(table)

            deleted_words = 0
            deleted_table_graphics = 0

            for page_num in range(len(doc)):
                page = doc[page_num]
                page_words = words_by_page.get(page_num, [])

                # フェーズ1: テキスト削除（常時）
                if page_words:
                    for word in page_words:
                        page.add_redact_annot(fitz.Rect(word['bbox']))
                        deleted_words += 1
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=False
                    )

                # フェーズ2: 表罫線削除（表構造抽出済みの場合のみ）
                page_tables = tables_by_page.get(page_num, [])
                if page_tables:
                    for table in page_tables:
                        bbox = table.get('bbox')
                        if bbox:
                            page.add_redact_annot(fitz.Rect(bbox))
                            deleted_table_graphics += 1
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=True
                    )

            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b30_{file_path.stem}_purged.pdf"

            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-30] テキスト削除: {deleted_words}語")
            if deleted_table_graphics > 0:
                logger.info(f"[B-30] 表罫線削除: {deleted_table_graphics}表（抽出済みのため）")
            else:
                logger.info(f"[B-30] 表罫線: 保持（Stage D 検出用）")
            logger.info(f"[B-30] purged PDF 保存: {purged_pdf_path.name}")

            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-30] テキスト削除エラー: {e}", exc_info=True)
            return file_path
    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'is_structured': False,
            'error': error_message,
            'text_with_tags': '',
            'logical_blocks': [],
            'structured_tables': [],
            'tags': {},
            'all_words': [],
            'purged_pdf_path': ''
        }
