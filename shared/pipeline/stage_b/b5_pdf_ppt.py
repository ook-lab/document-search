"""
B-5: PDF-PowerPoint Processor（PDF-PowerPoint専用）

pdfplumber を使用して、PowerPoint由来PDFから構造化データを抽出。
スライド単位の構造、テキストボックスごとの順序を復元。
"""

from pathlib import Path
from typing import Dict, Any, List
from loguru import logger


class B5PDFPPTProcessor:
    """B-5: PDF-PowerPoint Processor（PDF-PowerPoint専用）"""

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        PowerPoint由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス
            masked_pages: スキップするページ番号リスト（0始まり）
            log_file: 個別ログファイルパス（Noneなら共有ロガーのみ）

        Returns:
            {
                'is_structured': bool,
                'text_with_tags': str,           # 全スライドのテキスト
                'logical_blocks': [...],         # スライドごとのブロック
                'structured_tables': [...],      # 表構造データ
                'tags': {...},                   # メタ情報
                'purged_image_path': str         # テキスト消去後の画像
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-5]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-5] PDF-PowerPoint処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-5] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                # 全ページ（スライド）を処理
                logical_blocks = []
                all_tables = []
                all_words = []  # 削除対象の全単語

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-5] ページ{page_num+1}: マスク → スキップ")
                        continue
                    # テキストボックスを検出（座標ベース）
                    textboxes = self._extract_textboxes(page, page_num)

                    # スライドとして登録
                    logical_blocks.append({
                        'page': page_num,
                        'type': 'slide',
                        'textboxes': textboxes,
                        'textbox_count': len(textboxes)
                    })

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
                    'slide_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'total_textboxes': sum(b['textbox_count'] for b in logical_blocks)
                }

                logger.info(f"[B-5] 抽出完了: スライド={len(logical_blocks)}, 表={len(all_tables)}, 単語（削除対象）={len(all_words)}")
                for slide in logical_blocks:
                    for tb_idx, tb in enumerate(slide.get('textboxes', [])):
                        logger.info(f"[B-5] slide{slide.get('page')} textbox{tb_idx}: {tb.get('text', '')}")

                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-5] テキスト削除完了: {purged_pdf_path}")

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
            logger.error(f"[B-5] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _extract_textboxes(self, page, page_num: int) -> List[Dict[str, Any]]:
        """
        テキストボックスを検出（座標ベース）

        Args:
            page: pdfplumberのPageオブジェクト
            page_num: ページ番号

        Returns:
            [{
                'index': int,
                'bbox': tuple,
                'text': str,
                'top': float,
                'left': float
            }, ...]
        """
        # 文字をグループ化してテキストボックスを推定
        # ここでは簡易的に、Y座標が近い文字をまとめる
        chars = page.chars
        if not chars:
            return []

        # Y座標でグループ化（10pt以内なら同じ行）
        lines = []
        current_line = []
        prev_y = None

        for char in sorted(chars, key=lambda c: (c['top'], c['x0'])):
            if prev_y is None or abs(char['top'] - prev_y) < 10:
                current_line.append(char)
            else:
                if current_line:
                    lines.append(current_line)
                current_line = [char]
            prev_y = char['top']

        if current_line:
            lines.append(current_line)

        # 各行をテキストボックスとして登録
        textboxes = []
        for idx, line in enumerate(lines):
            text = ''.join([c['text'] for c in line])
            bbox = (
                min(c['x0'] for c in line),
                min(c['top'] for c in line),
                max(c['x1'] for c in line),
                max(c['bottom'] for c in line)
            )

            textboxes.append({
                'index': idx,
                'bbox': bbox,
                'text': text,
                'top': bbox[1],
                'left': bbox[0]
            })

        # 座標順にソート（上から下、左から右）
        textboxes.sort(key=lambda tb: (tb['top'], tb['left']))

        return textboxes

    def _extract_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        """
        スライド内の表を抽出

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
            data = table.extract()
            logger.info(f"[B-5] Table {idx} (Page {page_num}): {len(data) if data else 0}行×{len(data[0]) if data and len(data) > 0 else 0}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-5] Table {idx} 行{row_idx}: {row}")

            tables.append({
                'page': page_num,
                'index': idx,
                'bbox': table.bbox,
                'data': data,
                'rows': len(data) if data else 0,
                'cols': len(data[0]) if data and len(data) > 0 else 0,
                'source': 'stage_b'
            })
        return tables

    def _build_text(self, logical_blocks: List[Dict[str, Any]]) -> str:
        """
        全スライドのテキストを生成

        Args:
            logical_blocks: スライドリスト

        Returns:
            [SLIDE page=X]...[/SLIDE] 形式
        """
        result = []

        for block in logical_blocks:
            header = f"[SLIDE page={block['page']}]"
            footer = "[/SLIDE]"

            # テキストボックスを結合
            textbox_texts = [tb['text'] for tb in block['textboxes']]

            result.append(f"{header}\n" + "\n".join(textbox_texts) + f"\n{footer}")

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
            logger.error("[B-5] PyMuPDF がインストールされていません")
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
            purged_pdf_path = purged_dir / f"b5_{file_path.stem}_purged.pdf"

            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-5] テキスト削除: {deleted_words}語")
            if deleted_table_graphics > 0:
                logger.info(f"[B-5] 表罫線削除: {deleted_table_graphics}表（抽出済みのため）")
            else:
                logger.info(f"[B-5] 表罫線: 保持（Stage D 検出用）")
            logger.info(f"[B-5] purged PDF 保存: {purged_pdf_path.name}")

            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-5] テキスト削除エラー: {e}", exc_info=True)
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
