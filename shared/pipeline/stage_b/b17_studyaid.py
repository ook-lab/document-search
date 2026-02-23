"""
B-17: Studyaid Processor（Studyaid D.B. 由来 PDF 専用）

Studyaid D.B. で作成された教材 PDF を処理する。
Studyaid PDF の特徴:
- 教材作成システム（テスト・ワークシート・問題集）由来
- PDFsharp で生成されるため標準的なテキスト埋め込み
- 問題番号（①②③ / 1. 2. 3.）がアンカーになる縦方向テキストフロー
- 解答スペース（罫線）や表（採点表・答案用紙）が混在することが多い
- 1〜2 カラム構成が一般的

処理方針:
- B3（Word）と同じテキストフロー + 縦スライス方式
- 表検出は積極的に行う（採点表・答案グリッド対策）
- purge は標準テキスト消去（ベクターパス不要）
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B17StudyaidProcessor:
    """B-17: Studyaid Processor（Studyaid D.B. 由来 PDF 専用）"""

    RUBY_SIZE_THRESHOLD = 6.0
    GAP_MIN_WIDTH = 10.0

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        Studyaid D.B. 由来 PDF から構造化データを抽出

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
                filter=lambda r: "[B-17]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-17] Studyaid PDF処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-17] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logical_blocks = []
                all_tables = []
                all_words = []

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-17] ページ{page_num+1}: マスク → スキップ")
                        continue

                    logger.info(f"[B-17] ページ{page_num+1}: 処理開始")

                    # 表を先に検出（採点表・答案グリッドを先に分離）
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)
                    table_bboxes = [t['bbox'] for t in tables]

                    # 縦スライス検出（2カラム問題集に対応）
                    slices = self._detect_slices(page)
                    logger.info(f"[B-17] ページ{page_num+1}: {len(slices)}スライス / {len(tables)}表")

                    # 各スライスからテキスト抽出
                    for slice_idx, slice_bbox in enumerate(slices):
                        block = self._extract_block(page, slice_bbox, page_num, slice_idx, table_bboxes)
                        if block['text'].strip():
                            logical_blocks.append(block)

                    # purge 用全文字収集
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
                    'processor': 'b17_studyaid',
                }

                logger.info(f"[B-17] 抽出完了: ブロック={len(logical_blocks)} 表={len(all_tables)}")
                for idx, block in enumerate(logical_blocks):
                    logger.info(f"[B-17] block{idx} (page={block.get('page')}, slice={block.get('slice')}): {block.get('text', '')}")

                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-17] purged PDF 生成完了: {purged_pdf_path.name}")

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
            logger.error(f"[B-17] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _detect_slices(self, page) -> List[Tuple[float, float, float, float]]:
        """縦方向スライスを検出（文字が存在しない空白列を境界とする）"""
        page_width = float(page.width)
        page_height = float(page.height)
        chars = page.chars

        if not chars:
            return [(0, 0, page_width, page_height)]

        occupied = [False] * int(page_width)
        for char in chars:
            for x in range(max(0, int(char['x0'])), min(len(occupied), int(char['x1']))):
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

        # ルビ除外
        body_chars = [
            ch for ch in chars
            if not (0 < float(ch.get('size') or 0) <= self.RUBY_SIZE_THRESHOLD)
        ]

        text = self._chars_to_text_lines(body_chars)

        return {
            'page': page_num,
            'slice': slice_idx,
            'bbox': bbox,
            'text': text,
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
            result_lines.append(''.join(c['text'] for c in line_chars_sorted))

        return "\n".join(result_lines)

    def _is_inside_any_table(
        self,
        bbox: Tuple[float, float, float, float],
        table_bboxes: List[Tuple[float, float, float, float]]
    ) -> bool:
        if not table_bboxes:
            return False
        wx0, wy0, wx1, wy1 = bbox
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
            logger.info(f"[B-17] Table {idx} (Page {page_num}): {rows}行×{cols}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-17] Table {idx} 行{row_idx}: {row}")
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
            result.append(f"{header}\n{block['text']}\n[/BLOCK]")
        return "\n\n".join(result)

    def _purge_extracted_text(
        self,
        file_path: Path,
        all_words: List[Dict[str, Any]],
        structured_tables: List[Dict[str, Any]] = None,
    ) -> Path:
        """標準テキスト消去（PDFsharp 生成なのでベクターパス不要）"""
        try:
            import fitz
        except ImportError:
            logger.error("[B-17] PyMuPDF がインストールされていません")
            return file_path

        try:
            doc = fitz.open(str(file_path))
            page_count = len(doc)

            words_by_page: Dict[int, List[Dict]] = {}
            for w in all_words:
                words_by_page.setdefault(w['page'], []).append(w)

            # フェーズ1: 抽出した文字 bbox のみ redaction
            deleted_chars = 0
            for page_num in range(page_count):
                page = doc[page_num]
                for w in words_by_page.get(page_num, []):
                    page.add_redact_annot(fitz.Rect(w['bbox']))
                    deleted_chars += 1
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_NONE,
                    graphics=False,
                )
            logger.info(f"[B-17] フェーズ1: 抽出文字を削除 {deleted_chars}文字")

            # フェーズ2: 残存テキスト検査 → 追いredact（最大5回）
            for attempt in range(1, 6):
                remaining_pages = [
                    pn for pn in range(page_count)
                    if doc[pn].get_text("text").strip()
                ]
                if not remaining_pages:
                    break
                logger.info(f"[B-17] フェーズ2 試行{attempt}: 残存テキストあり {remaining_pages}")
                for pn in remaining_pages:
                    page = doc[pn]
                    for block in page.get_text("rawdict").get("blocks", []):
                        if block.get("type") != 0:
                            continue
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                if span.get("text", "").strip():
                                    page.add_redact_annot(fitz.Rect(span["bbox"]))
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=False,
                    )

            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b17_{file_path.stem}_purged.pdf"
            doc.save(str(purged_pdf_path))
            doc.close()

            # フェーズ3: 表罫線削除
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
                    logger.info(f"[B-17] フェーズ3: 表罫線削除 {deleted_table_graphics}表")
                except Exception as e:
                    logger.warning(f"[B-17] 表罫線削除エラー: {e}")

            logger.info(f"[B-17] purged PDF 保存: {purged_pdf_path.name}")
            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-17] purge エラー: {e}", exc_info=True)
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
