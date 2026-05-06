"""
B-19: PDF-Web Processor（ウェブPDF生成ライブラリ由来 専用）

対象: TCPDF, wkhtmltopdf, mPDF, FPDF, dompdf, ReportLab 等
    PHPやPythonのライブラリがコードからPDFを直接生成したもの。
    Creator が空で Producer にライブラリ名が入る。

処理方針:
- pdfplumber でスライス検出・表抽出・テキスト抽出
- ルビなし（ウェブPDF生成ライブラリはルビを使用しない）
- 標準的な Unicode マッピングなので文字化けなし
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B19PDFWebProcessor:
    """B-19: PDF-Web Processor（ウェブPDF生成ライブラリ由来 専用）"""

    # 空白列検出の最小幅（pt）
    GAP_MIN_WIDTH = 10.0

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """ウェブPDF生成ライブラリ由来PDFから構造化データを抽出"""
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-19]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-19] PDF-Web処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-19] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logger.info(f"[B-19] PDF情報:")
                logger.info(f"[B-19]   ├─ ページ数: {len(pdf.pages)}")
                logger.info(f"[B-19]   └─ Producer: {pdf.metadata.get('Producer', '（空）')}")

                logical_blocks = []
                all_tables = []
                all_words = []

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-19] ページ{page_num+1}: マスク → スキップ")
                        continue

                    logger.info(f"[B-19] ========== ページ {page_num+1}/{len(pdf.pages)} ==========")
                    logger.info(
                        f"[B-19]   ├─ chars={len(page.chars or [])} "
                        f"images={len(page.images or [])} "
                        f"rects={len(page.rects or [])}"
                    )

                    slices = self._detect_slices(page)
                    logger.info(f"[B-19]   ├─ スライス数: {len(slices)}")

                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)
                    table_bboxes = [t['bbox'] for t in tables]

                    for slice_idx, slice_bbox in enumerate(slices):
                        block = self._extract_block(page, slice_bbox, page_num, slice_idx, table_bboxes)
                        if block['text'].strip():
                            logger.info(f"[B-19]   ├─ slice{slice_idx}: 採用文字数={block['word_count']} テキスト=\n{block['text']}")
                            logical_blocks.append(block)

                    for ch in (page.chars or []):
                        if ch.get('text', '').strip():
                            all_words.append({
                                'page': page_num,
                                'text': ch['text'],
                                'bbox': (ch['x0'], ch['top'], ch['x1'], ch['bottom'])
                            })

                text_with_tags = self._build_tagged_text(logical_blocks)
                tags = {
                    'has_slices': len(logical_blocks) > len(pdf.pages),
                    'block_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'has_red_text': any(b.get('has_red', False) for b in logical_blocks),
                    'has_bold_text': any(b.get('has_bold', False) for b in logical_blocks),
                }

                logger.info(f"[B-19] 抽出完了:")
                logger.info(f"[B-19]   ├─ ブロック: {len(logical_blocks)}")
                logger.info(f"[B-19]   ├─ 表: {len(all_tables)}")
                logger.info(f"[B-19]   └─ 全単語（削除対象）: {len(all_words)}")

                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-19] テキスト削除完了: {purged_pdf_path}")

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
            logger.error(f"[B-19] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _detect_slices(self, page) -> List[Tuple[float, float, float, float]]:
        page_width = float(page.width)
        page_height = float(page.height)
        chars = page.chars
        if not chars:
            return [(0, 0, page_width, page_height)]
        gaps = self._find_text_free_columns(chars, page_width)
        if not gaps:
            return [(0, 0, page_width, page_height)]
        slices = []
        prev_x = 0
        for gap_start, gap_end in gaps:
            if gap_start > prev_x + 5:
                slices.append((prev_x, 0, gap_start, page_height))
            prev_x = gap_end
        if prev_x < page_width - 5:
            slices.append((prev_x, 0, page_width, page_height))
        return slices if slices else [(0, 0, page_width, page_height)]

    def _find_text_free_columns(self, chars: List[Dict], page_width: float) -> List[Tuple[float, float]]:
        occupied = [False] * int(page_width)
        for char in chars:
            x0 = int(char['x0'])
            x1 = int(char['x1'])
            for x in range(max(0, x0), min(len(occupied), x1)):
                occupied[x] = True
        gaps = []
        gap_start = None
        for x in range(len(occupied)):
            if not occupied[x]:
                if gap_start is None:
                    gap_start = x
            else:
                if gap_start is not None:
                    if x - gap_start >= self.GAP_MIN_WIDTH:
                        gaps.append((float(gap_start), float(x)))
                    gap_start = None
        if gap_start is not None and len(occupied) - gap_start >= self.GAP_MIN_WIDTH:
            gaps.append((float(gap_start), float(len(occupied))))
        return gaps

    def _extract_block(
        self,
        page,
        bbox: Tuple[float, float, float, float],
        page_num: int,
        slice_idx: int,
        table_bboxes: List[Tuple[float, float, float, float]] = None
    ) -> Dict[str, Any]:
        x0, y0, x1, y1 = bbox
        cropped = page.within_bbox((x0, y0, x1, y1))

        if table_bboxes is None:
            table_bboxes = []

        chars = [ch for ch in (cropped.chars or []) if ch.get('text', '').strip()]
        chars = [
            ch for ch in chars
            if not self._is_inside_any_table(
                (ch['x0'], ch['top'], ch['x1'], ch['bottom']), table_bboxes
            )
        ]

        has_bold = any('bold' in ch.get('fontname', '').lower() for ch in chars)
        text = self._chars_to_text_lines(chars)

        return {
            'page': page_num,
            'slice': slice_idx,
            'bbox': bbox,
            'text': text,
            'has_red': False,
            'has_bold': has_bold,
            'word_count': len(text.split()) if text else 0,
        }

    def _chars_to_text_lines(self, chars: List[Dict]) -> str:
        if not chars:
            return ""
        sorted_chars = sorted(chars, key=lambda c: (c['top'], c['x0']))
        tol = 2.0
        lines: List[List[Dict]] = []
        current_line = [sorted_chars[0]]
        current_top = sorted_chars[0]['top']
        for ch in sorted_chars[1:]:
            if abs(ch['top'] - current_top) <= tol:
                current_line.append(ch)
            else:
                lines.append(current_line)
                current_line = [ch]
                current_top = ch['top']
        lines.append(current_line)
        result_lines = []
        for line_chars in lines:
            line_chars_sorted = sorted(line_chars, key=lambda c: c['x0'])
            result_lines.append(self._line_chars_to_text(line_chars_sorted))
        return "\n".join(result_lines)

    def _line_chars_to_text(self, line_chars: List[Dict]) -> str:
        if not line_chars:
            return ""
        import statistics
        widths = [ch['x1'] - ch['x0'] for ch in line_chars if ch['x1'] > ch['x0']]
        gap_threshold = statistics.median(widths) * 0.8 if widths else 3.0
        result = line_chars[0]['text']
        for i in range(1, len(line_chars)):
            prev = line_chars[i - 1]
            curr = line_chars[i]
            gap = curr['x0'] - prev['x1']
            if gap > gap_threshold:
                result += " "
            result += curr['text']
        return result

    def _is_inside_any_table(
        self,
        word_bbox: Tuple[float, float, float, float],
        table_bboxes: List[Tuple[float, float, float, float]]
    ) -> bool:
        if not table_bboxes:
            return False
        wx0, wy0, wx1, wy1 = word_bbox
        for tx0, ty0, tx1, ty1 in table_bboxes:
            if wx0 >= tx0 and wx1 <= tx1 and wy0 >= ty0 and wy1 <= ty1:
                return True
        return False

    def _extract_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        tables = []
        for idx, table in enumerate(page.find_tables()):
            data = table.extract()
            rows = len(data) if data else 0
            cols = len(data[0]) if data and data[0] else 0
            logger.info(f"[B-19] Table {idx} (Page {page_num}): {rows}行×{cols}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-19] Table {idx} 行{row_idx}: {row}")
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

    def _build_tagged_text(self, logical_blocks: List[Dict[str, Any]]) -> str:
        result = []
        for block in logical_blocks:
            header = f"[BLOCK page={block['page']} slice={block['slice']}]"
            footer = "[/BLOCK]"
            text = block['text']
            if block.get('has_bold'):
                text = f"[BOLD]{text}[/BOLD]"
            if block.get('has_red'):
                text = f"[RED]{text}[/RED]"
            result.append(f"{header}\n{text}\n{footer}")
        return "\n\n".join(result)

    def _purge_extracted_text(
        self,
        file_path: Path,
        all_words: List[Dict[str, Any]],
        structured_tables: List[Dict[str, Any]] = None
    ) -> Path:
        try:
            import fitz
        except ImportError:
            logger.error("[B-19] PyMuPDF がインストールされていません")
            return file_path

        try:
            doc = fitz.open(str(file_path))
            page_count = len(doc)

            words_by_page: Dict[int, List[Dict]] = {}
            for w in all_words:
                words_by_page.setdefault(w['page'], []).append(w)

            deleted_chars = 0
            for page_num in range(page_count):
                page = doc[page_num]
                for w in words_by_page.get(page_num, []):
                    page.add_redact_annot(fitz.Rect(w['bbox']))
                    deleted_chars += 1
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=False)

            logger.info(f"[B-19] フェーズ1: 抽出文字を削除 {deleted_chars}文字")

            for attempt in range(1, 6):
                remaining_pages = [
                    pn for pn in range(page_count)
                    if doc[pn].get_text("text").strip()
                ]
                if not remaining_pages:
                    break
                logger.info(f"[B-19] フェーズ2 試行{attempt}: 残存テキストあり {remaining_pages}")
                for pn in remaining_pages:
                    page = doc[pn]
                    for block in page.get_text("rawdict").get("blocks", []):
                        if block.get("type") != 0:
                            continue
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                if span.get("text", "").strip():
                                    page.add_redact_annot(fitz.Rect(span["bbox"]))
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=False)

            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b19_{file_path.stem}_purged.pdf"
            doc.save(str(purged_pdf_path))
            doc.close()

            if structured_tables:
                try:
                    doc2 = fitz.open(str(purged_pdf_path))
                    deleted_table_graphics = 0
                    for page_index, page in enumerate(doc2):
                        page_tables = [t for t in structured_tables if t.get("page") == page_index]
                        if not page_tables:
                            continue
                        for t in page_tables:
                            bbox = t.get("bbox")
                            if bbox:
                                page.add_redact_annot(fitz.Rect(bbox))
                                deleted_table_graphics += 1
                        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=True)
                    doc2.save(str(purged_pdf_path))
                    doc2.close()
                    logger.info(f"[B-19] フェーズ3: 表罫線削除 {deleted_table_graphics}表")
                except Exception as e:
                    logger.warning(f"[B-19] 表罫線削除エラー: {e}")

            logger.info(f"[B-19] purged PDF 保存: {purged_pdf_path.name}")
            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-19] purge エラー: {e}", exc_info=True)
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
