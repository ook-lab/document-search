"""
B-30: Illustrator Processor（Adobe Illustrator 由来 PDF 専用）

Adobe Illustrator で作成された PDF を処理する。
Illustrator PDF の特徴:
- テキストがベクターパス（グラフィクス）として埋め込まれている場合がある
- 単ページ〜少数ページ構成
- pdfplumber での文字抽出は可能だが、テキストボックス境界が曖昧になりやすい

処理方針:
- pdfplumber で文字を抽出し、テキストボックス単位でログ出力
- purge 時は graphics=True で確実にベクターパス文字も消去
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B30IllustratorProcessor:
    """B-30: Illustrator Processor（Adobe Illustrator 由来 PDF 専用）"""

    MERGE_THRESHOLD = 5.0

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        Illustrator 由来 PDF から構造化データを抽出

        Returns:
            {
                'is_structured': bool,
                'text_with_tags': str,
                'logical_blocks': [...],
                'structured_tables': [...],
                'tags': {...},
                'all_words': [...],
                'purged_pdf_path': str,
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-30]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )

        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-30] Illustrator PDF処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-30] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logger.info(f"[B-30] PDF情報:")
                logger.info(f"[B-30]   ├─ ページ数: {len(pdf.pages)}")
                logger.info(f"[B-30]   ├─ メタデータ: {pdf.metadata}")

                logical_blocks = []
                all_tables = []
                all_words = []

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-30] ページ{page_num+1}: マスク → スキップ")
                        continue

                    logger.info(f"[B-30] ========== ページ {page_num+1}/{len(pdf.pages)} ==========")
                    logger.info(
                        f"[B-30]   ├─ ページサイズ: {page.width:.1f} x {page.height:.1f} pt"
                    )
                    logger.info(
                        f"[B-30]   ├─ chars={len(page.chars or [])} "
                        f"lines={len(page.lines or [])} rects={len(page.rects or [])}"
                    )

                    # 表を先に検出（表領域をテキスト抽出から除外するため）
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)
                    table_bboxes = [t['bbox'] for t in tables]

                    # テキストボックス単位で抽出（表領域を除外）
                    textboxes = self._extract_textboxes(page, page_num, table_bboxes)

                    # 近接ボックスをマージ
                    merged_boxes = self._merge_nearby_boxes(textboxes)
                    logical_blocks.extend(merged_boxes)

                    # purge 用に全単語を収集
                    page_words = page.extract_words(
                        x_tolerance=3, y_tolerance=3, keep_blank_chars=True
                    )
                    for word in page_words:
                        all_words.append({
                            'page': page_num,
                            'text': word['text'],
                            'bbox': (word['x0'], word['top'], word['x1'], word['bottom'])
                        })

                    logger.info(f"[B-30]   └─ テキストボックス={len(merged_boxes)} 表={len(tables)} 単語={len(page_words)}")

                text_with_tags = self._build_text(logical_blocks)

                tags = {
                    'page_count': len(pdf.pages),
                    'textbox_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'processor': 'b30_illustrator',
                }

                logger.info(f"[B-30] ========== 抽出テキスト全文（位置情報付き） ==========")
                for idx, block in enumerate(logical_blocks):
                    bbox = block.get('bbox', (0, 0, 0, 0))
                    logger.info(
                        f"[B-30] Block #{idx+1} | Page:{block.get('page', 0)} | "
                        f"Bbox:({bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f})"
                    )
                    logger.info(f"[B-30] Text: {block.get('text', '')!r}")

                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-30] purged PDF 生成完了: {purged_pdf_path.name}")

                return {
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'all_words': all_words,
                    'purged_pdf_path': str(purged_pdf_path),
                }

        except Exception as e:
            logger.error(f"[B-30] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _extract_textboxes(
        self, page, page_num: int,
        table_bboxes: List[Tuple[float, float, float, float]] = None
    ) -> List[Dict[str, Any]]:
        """chars をグループ化してテキストボックスを構築（表領域を除外）"""
        chars = page.chars
        if not chars:
            return []

        if table_bboxes is None:
            table_bboxes = []

        # 表領域外の文字のみ
        non_table_chars = [
            c for c in chars
            if not self._is_inside_any_table(
                (c['x0'], c['top'], c['x1'], c['bottom']), table_bboxes
            )
        ]

        chars_sorted = sorted(non_table_chars, key=lambda c: (c['top'], c['x0']))

        textboxes = []
        current_box_chars = []
        prev_y = None
        prev_x = None

        for char in chars_sorted:
            if prev_y is None:
                current_box_chars.append(char)
            else:
                if abs(char['top'] - prev_y) < 10 and abs(char['x0'] - prev_x) < 20:
                    current_box_chars.append(char)
                else:
                    if current_box_chars:
                        textboxes.append(self._create_textbox(current_box_chars, page_num))
                    current_box_chars = [char]
            prev_y = char['top']
            prev_x = char['x1']

        if current_box_chars:
            textboxes.append(self._create_textbox(current_box_chars, page_num))

        return textboxes

    def _create_textbox(self, chars: List[Dict], page_num: int) -> Dict[str, Any]:
        text = ''.join(c['text'] for c in chars)
        bbox = (
            min(c['x0'] for c in chars),
            min(c['top'] for c in chars),
            max(c['x1'] for c in chars),
            max(c['bottom'] for c in chars),
        )
        return {'page': page_num, 'bbox': bbox, 'text': text, 'chars': chars}

    def _merge_nearby_boxes(self, textboxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not textboxes:
            return []

        merged = []
        used = set()

        for i, box1 in enumerate(textboxes):
            if i in used:
                continue
            nearby = [box1]
            used.add(i)
            for j, box2 in enumerate(textboxes):
                if j in used or j <= i:
                    continue
                if self._is_nearby(box1['bbox'], box2['bbox']):
                    nearby.append(box2)
                    used.add(j)
            merged.append(self._merge_boxes(nearby))

        logger.debug(f"[B-30] マージ: {len(textboxes)}個 → {len(merged)}個")
        return merged

    def _is_nearby(self, bbox1: Tuple, bbox2: Tuple) -> bool:
        x0_1, y0_1, x1_1, y1_1 = bbox1
        x0_2, y0_2, x1_2, y1_2 = bbox2
        h_dist = min(abs(x0_1 - x1_2), abs(x0_2 - x1_1))
        v_dist = min(abs(y0_1 - y1_2), abs(y0_2 - y1_1))
        return h_dist < self.MERGE_THRESHOLD or v_dist < self.MERGE_THRESHOLD

    def _merge_boxes(self, boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
        texts = [b['text'] for b in boxes]
        all_bboxes = [b['bbox'] for b in boxes]
        return {
            'page': boxes[0]['page'],
            'bbox': (
                min(b[0] for b in all_bboxes),
                min(b[1] for b in all_bboxes),
                max(b[2] for b in all_bboxes),
                max(b[3] for b in all_bboxes),
            ),
            'text': ' '.join(texts),
            'merged_count': len(boxes),
        }

    def _is_inside_any_table(
        self,
        char_bbox: Tuple[float, float, float, float],
        table_bboxes: List[Tuple[float, float, float, float]]
    ) -> bool:
        if not table_bboxes:
            return False
        cx0, cy0, cx1, cy1 = char_bbox
        for tx0, ty0, tx1, ty1 in table_bboxes:
            if cx0 >= tx0 and cx1 <= tx1 and cy0 >= ty0 and cy1 <= ty1:
                return True
        return False

    def _extract_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        tables = []
        for idx, table in enumerate(page.find_tables()):
            data = table.extract()
            rows = len(data) if data else 0
            cols = len(data[0]) if data and data[0] else 0
            logger.info(f"[B-30] Table {idx} (Page {page_num}): {rows}行×{cols}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-30] Table {idx} 行{row_idx}: {row}")
            tables.append({
                'page': page_num,
                'index': idx,
                'bbox': table.bbox,
                'data': data,
                'rows': rows,
                'cols': cols,
                'source': 'stage_b',
            })
        return tables

    def _build_text(self, logical_blocks: List[Dict[str, Any]]) -> str:
        result = []
        for block in logical_blocks:
            result.append(
                f"[TEXTBOX page={block['page']}]\n{block['text']}\n[/TEXTBOX]"
            )
        return "\n\n".join(result)

    def _purge_extracted_text(
        self,
        file_path: Path,
        all_words: List[Dict[str, Any]],
        structured_tables: List[Dict[str, Any]] = None,
    ) -> Path:
        """
        テキストと表罫線を PDF から削除。
        Illustrator PDF はベクターパス文字があるため graphics=True を必ず使用する。
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
                    tables_by_page.setdefault(table.get('page', 0), []).append(table)

            deleted_words = 0
            deleted_graphics = 0

            for page_num in range(len(doc)):
                page = doc[page_num]

                # フェーズ1: テキスト削除（graphics=True でベクターパス文字も消去）
                page_words = words_by_page.get(page_num, [])
                if page_words:
                    logger.info(f"[B-30] ページ {page_num+1}: {len(page_words)}単語を削除")
                    for word in page_words:
                        page.add_redact_annot(fitz.Rect(word['bbox']))
                        deleted_words += 1
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=True,
                    )

                # フェーズ2: 表罫線削除
                page_tables = tables_by_page.get(page_num, [])
                if page_tables:
                    logger.info(f"[B-30] ページ {page_num+1}: {len(page_tables)}表の罫線を削除")
                    for table in page_tables:
                        bbox = table.get('bbox')
                        if bbox:
                            page.add_redact_annot(fitz.Rect(bbox))
                            deleted_graphics += 1
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=True,
                    )

            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b30_{file_path.stem}_purged.pdf"
            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-30] 削除した単語: {deleted_words}個")
            logger.info(f"[B-30] 削除した表罫線: {deleted_graphics}個")
            logger.info(f"[B-30] purged PDF 保存先: {purged_pdf_path}")
            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-30] テキスト削除エラー: {e}", exc_info=True)
            return file_path

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        return {
            'is_structured': False,
            'error': error_message,
            'text_with_tags': '',
            'logical_blocks': [],
            'structured_tables': [],
            'tags': {},
            'all_words': [],
            'purged_pdf_path': '',
        }
