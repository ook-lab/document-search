"""
B-39: Acrobat Processor（Adobe Acrobat 由来 PDF 専用）

Adobe Acrobat 本体で生成・再保存された PDF を処理する。
Acrobat PDF の特徴:
- Acrobat で直接作成（フォームや通知文書）または他形式を変換・再保存
- 元ファイル種別は不明だが最終出力は Acrobat 処理済み
- テキストは標準 PDF テキストオブジェクトとして埋め込まれる
- レイアウトは固定配置（テキストボックスが座標指定で配置）
- 図書室お知らせ等の通知・案内文書が代表例：見出し＋本文＋画像の混在

処理方針:
- B30（Illustrator）と同じテキストボックス抽出方式（固定配置に対応）
- Acrobat は標準テキスト埋め込みなので purge は graphics=False が基本
  ただし Acrobat は混在パターンが多いため、残存テキストを graphics=True で追いredact
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B39AcrobatProcessor:
    """B-39: Acrobat Processor（Adobe Acrobat 由来 PDF 専用）"""

    MERGE_THRESHOLD = 8.0

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        Adobe Acrobat 由来 PDF から構造化データを抽出

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
                filter=lambda r: "[B-39]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-39] Acrobat PDF処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-39] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logger.info(f"[B-39] ページ数: {len(pdf.pages)}")
                logger.info(f"[B-39] メタデータ: {pdf.metadata}")

                logical_blocks = []
                all_tables = []
                all_words = []

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-39] ページ{page_num+1}: マスク → スキップ")
                        continue

                    logger.info(f"[B-39] ========== ページ {page_num+1}/{len(pdf.pages)} ==========")
                    logger.info(
                        f"[B-39]   ├─ chars={len(page.chars or [])} "
                        f"images={len(page.images or [])} "
                        f"rects={len(page.rects or [])}"
                    )

                    # 表を先に検出
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)
                    table_bboxes = [t['bbox'] for t in tables]

                    # テキストボックス抽出（固定配置のため座標基準でグループ化）
                    textboxes = self._extract_textboxes(page, page_num, table_bboxes)
                    merged_boxes = self._merge_nearby_boxes(textboxes)
                    logical_blocks.extend(merged_boxes)

                    # purge 用全単語収集
                    page_words = page.extract_words(
                        x_tolerance=3, y_tolerance=3, keep_blank_chars=True
                    )
                    for word in page_words:
                        all_words.append({
                            'page': page_num,
                            'text': word['text'],
                            'bbox': (word['x0'], word['top'], word['x1'], word['bottom'])
                        })

                    logger.info(
                        f"[B-39]   └─ テキストボックス={len(merged_boxes)} "
                        f"表={len(tables)} 単語={len(page_words)}"
                    )

                text_with_tags = self._build_text(logical_blocks)

                tags = {
                    'page_count': len(pdf.pages),
                    'textbox_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'processor': 'b39_acrobat',
                }

                for idx, block in enumerate(logical_blocks):
                    logger.info(f"[B-39] block{idx} (page={block.get('page')}): {block.get('text', '')}")
                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-39] purged PDF 生成完了: {purged_pdf_path.name}")

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
            logger.error(f"[B-39] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _extract_textboxes(
        self, page, page_num: int,
        table_bboxes: List[Tuple[float, float, float, float]] = None
    ) -> List[Dict[str, Any]]:
        """chars をグループ化してテキストボックスを構築"""
        chars = page.chars
        if not chars:
            return []

        if table_bboxes is None:
            table_bboxes = []

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
                y_gap = abs(char['top'] - prev_y)
                x_gap = abs(char['x0'] - prev_x)
                # Acrobat 固定配置: 行内は y 近接、テキストブロック間は y が跳ぶ
                if y_gap > 10 or (y_gap > 3 and x_gap > 60):
                    if current_box_chars:
                        textboxes.append(self._create_textbox(current_box_chars, page_num))
                    current_box_chars = [char]
                else:
                    current_box_chars.append(char)
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

        logger.debug(f"[B-39] マージ: {len(textboxes)}個 → {len(merged)}個")
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
            logger.info(f"[B-39] Table {idx} (Page {page_num}): {rows}行×{cols}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-39] Table {idx} 行{row_idx}: {row}")
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
        テキスト削除。
        Acrobat は標準テキスト埋め込みが基本（graphics=False）。
        残存テキストは graphics=True で追いredact（混在パターン対策）。
        """
        try:
            import fitz
        except ImportError:
            logger.error("[B-39] PyMuPDF がインストールされていません")
            return file_path

        try:
            doc = fitz.open(str(file_path))
            page_count = len(doc)

            words_by_page: Dict[int, List[Dict]] = {}
            for w in all_words:
                words_by_page.setdefault(w['page'], []).append(w)

            tables_by_page: Dict[int, List[Dict]] = {}
            if structured_tables:
                for table in structured_tables:
                    tables_by_page.setdefault(table.get('page', 0), []).append(table)

            # フェーズ1: 抽出した文字 bbox のみ redaction（graphics=False が基本）
            deleted_words = 0
            for page_num in range(page_count):
                page = doc[page_num]
                for w in words_by_page.get(page_num, []):
                    page.add_redact_annot(fitz.Rect(w['bbox']))
                    deleted_words += 1
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_NONE,
                    graphics=False,
                )
            logger.info(f"[B-39] フェーズ1: 抽出文字を削除 {deleted_words}文字")

            # フェーズ2: 残存テキスト検査 → graphics=True で追いredact（混在パターン対策、最大5回）
            for attempt in range(1, 6):
                remaining_pages = [
                    pn for pn in range(page_count)
                    if doc[pn].get_text("text").strip()
                ]
                if not remaining_pages:
                    break
                logger.info(f"[B-39] フェーズ2 試行{attempt}: 残存テキストあり {remaining_pages}")
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
                        graphics=True,
                    )

            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b39_{file_path.stem}_purged.pdf"
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
                    logger.info(f"[B-39] フェーズ3: 表罫線削除 {deleted_table_graphics}表")
                except Exception as e:
                    logger.warning(f"[B-39] 表罫線削除エラー: {e}")

            logger.info(f"[B-39] purged PDF 保存: {purged_pdf_path.name}")
            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-39] purge エラー: {e}", exc_info=True)
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
