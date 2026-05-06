"""
B-16: Canva Processor（Canva 由来 PDF 専用）

Canva で作成された PDF を処理する。
Canva PDF の特徴:
- デザインツール由来の固定レイアウト（ポスター・チラシ・プレゼン）
- テキストがテキストボックス単位で配置される
- 画像と文字が重なるレイヤー構造
- ベクターパスで文字が描画される場合がある

処理方針:
- B30（Illustrator）と同じテキストボックス抽出方式
- Canva はボックス間の間隔が大きいためマージ閾値を緩く設定
- purge は graphics=True（ベクターパス文字も確実に消去）
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B16CanvaProcessor:
    """B-16: Canva Processor（Canva 由来 PDF 専用）"""

    # Canva はテキストボックス間の空白が大きい → マージ閾値を広く取る
    MERGE_THRESHOLD = 15.0

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        Canva 由来 PDF から構造化データを抽出

        Returns:
            {
                'is_structured': bool,
                'text_with_tags': str,
                'logical_blocks': [...],
                'structured_tables': [...],
                'tags': {...},
                'all_words': [...],
                'purged_image_path': str,
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-16]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-16] Canva PDF処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-16] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logger.info(f"[B-16] PDF情報:")
                logger.info(f"[B-16]   ├─ ページ数: {len(pdf.pages)}")
                logger.info(f"[B-16]   ├─ メタデータ: {pdf.metadata}")

                logical_blocks = []
                all_tables = []
                all_words = []

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-16] ページ{page_num+1}: マスク → スキップ")
                        continue

                    logger.info(f"[B-16] ========== ページ {page_num+1}/{len(pdf.pages)} ==========")
                    logger.info(
                        f"[B-16]   ├─ ページサイズ: {page.width:.1f} x {page.height:.1f} pt"
                    )
                    logger.info(
                        f"[B-16]   ├─ chars={len(page.chars or [])} "
                        f"images={len(page.images or [])} "
                        f"rects={len(page.rects or [])}"
                    )

                    # 表を先に検出
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)
                    table_bboxes = [t['bbox'] for t in tables]

                    # テキストボックス単位で抽出（表領域を除外）
                    textboxes = self._extract_textboxes(page, page_num, table_bboxes)

                    # 近接ボックスをマージ（Canva は広い間隔なので閾値を MERGE_THRESHOLD に設定）
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

                    logger.info(
                        f"[B-16]   └─ テキストボックス={len(merged_boxes)} "
                        f"表={len(tables)} 単語={len(page_words)}"
                    )

                text_with_tags = self._build_text(logical_blocks)

                tags = {
                    'page_count': len(pdf.pages),
                    'textbox_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'processor': 'b16_canva',
                }

                for idx, block in enumerate(logical_blocks):
                    logger.info(f"[B-16] block{idx} (page={block.get('page')}): {block.get('text', '')}")
                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-16] purged PDF 生成完了: {purged_pdf_path.name}")

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
            logger.error(f"[B-16] 処理エラー: {e}", exc_info=True)
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

        # Canva はテキストボックスが明確に分離されている
        # top 座標 + x 座標でグループ化（行単位で区切りやすい）
        chars_sorted = sorted(non_table_chars, key=lambda c: (round(c['top'], 1), c['x0']))

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
                # Canva はボックス間で y が大きく跳ぶ → 12pt 超で新ボックス
                if y_gap > 12 or (y_gap > 3 and x_gap > 50):
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

        logger.debug(f"[B-16] マージ: {len(textboxes)}個 → {len(merged)}個")
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
            logger.info(f"[B-16] Table {idx} (Page {page_num}): {rows}行×{cols}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-16] Table {idx} 行{row_idx}: {row}")
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
        Canva PDF はベクターパス文字があるため graphics=True を使用する。
        """
        try:
            import fitz
        except ImportError:
            logger.error("[B-16] PyMuPDF がインストールされていません")
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

            # フェーズ1: テキスト削除（graphics=True でベクターパス文字も消去）
            deleted_words = 0
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_words = words_by_page.get(page_num, [])
                for word in page_words:
                    page.add_redact_annot(fitz.Rect(word['bbox']))
                    deleted_words += 1
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_NONE,
                    graphics=True,
                )
            logger.info(f"[B-16] フェーズ1: 抽出文字を削除 {deleted_words}文字")

            # フェーズ2: 残存テキスト検査 → 追いredact（最大5回）
            for attempt in range(1, 6):
                remaining_pages = [
                    pn for pn in range(len(doc))
                    if doc[pn].get_text("text").strip()
                ]
                if not remaining_pages:
                    break
                logger.info(f"[B-16] フェーズ2 試行{attempt}: 残存テキストあり {remaining_pages}")
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
            purged_pdf_path = purged_dir / f"b16_{file_path.stem}_purged.pdf"
            doc.save(str(purged_pdf_path))
            doc.close()

            # フェーズ3: 表罫線削除
            deleted_graphics = 0
            if structured_tables:
                try:
                    doc2 = fitz.open(str(purged_pdf_path))
                    for page_index, page in enumerate(doc2):
                        page_tables = [t for t in structured_tables if t.get("page") == page_index]
                        if not page_tables:
                            continue
                        for t in page_tables:
                            bbox = t.get("bbox")
                            if bbox:
                                page.add_redact_annot(fitz.Rect(bbox))
                                deleted_graphics += 1
                        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=True)
                    doc2.save(str(purged_pdf_path))
                    doc2.close()
                    logger.info(f"[B-16] フェーズ3: 表罫線削除 {deleted_graphics}表")
                except Exception as e:
                    logger.warning(f"[B-16] 表罫線削除エラー: {e}")

            logger.info(f"[B-16] purged PDF 保存: {purged_pdf_path.name}")
            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-16] テキスト削除エラー: {e}", exc_info=True)
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
