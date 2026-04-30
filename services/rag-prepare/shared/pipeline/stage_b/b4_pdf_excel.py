"""
B-4: PDF-Excel Processor（PDF-Excel専用）

pdfplumber を使用して、Excel由来PDFから構造化データを抽出。
格子解析により、セル構造を復元し、DataFrame形式へ変換。
"""

from pathlib import Path
from typing import Dict, Any, List
from loguru import logger


class B4PDFExcelProcessor:
    """B-4: PDF-Excel Processor（PDF-Excel専用）"""

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        Excel由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス
            masked_pages: スキップするページ番号リスト（0始まり）
            log_file: 個別ログファイルパス（Noneなら共有ロガーのみ）

        Returns:
            {
                'is_structured': bool,
                'text_with_tags': str,           # 全表のテキスト
                'logical_blocks': [...],         # シート相当のブロック
                'structured_tables': [...],      # 表構造データ（DataFrame形式）
                'tags': {...},                   # メタ情報
                'purged_image_path': str         # テキスト消去後の画像
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-4]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-4] PDF-Excel処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-4] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                # 全ページを処理
                all_tables = []
                logical_blocks = []
                all_text_blocks = []  # 表外テキストブロック（text_with_tags 用）
                all_words = []  # 削除対象の全単語

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-4] ページ{page_num+1}: マスク → スキップ")
                        continue

                    # ページ全体の単語を先に収集（表内外の判定に使う）
                    page_words = page.extract_words(
                        x_tolerance=3,
                        y_tolerance=3,
                        keep_blank_chars=False
                    )

                    # 格子解析により表を検出
                    tables = self._extract_grid_tables(page, page_num)
                    all_tables.extend(tables)

                    # 表外テキストを logical_blocks に保存
                    table_bboxes = [t['bbox'] for t in tables if t.get('bbox')]
                    non_table_words = self._extract_non_table_words(page_words, table_bboxes)
                    text_blocks = self._group_words_into_blocks(non_table_words, page_num)
                    logical_blocks.extend(text_blocks)
                    all_text_blocks.extend(text_blocks)
                    logger.info(f"[B-4] ページ{page_num+1}: 表={len(tables)}, 表外テキストブロック={len(text_blocks)}")

                    # 削除用：ページ全体の単語を収集
                    for word in page_words:
                        all_words.append({
                            'page': page_num,
                            'text': word['text'],
                            'bbox': (word['x0'], word['top'], word['x1'], word['bottom'])
                        })

                # テキストを生成（表 + 表外テキストをページ順に統合）
                text_with_tags = self._build_text(all_tables, all_text_blocks)

                # メタ情報
                tags = {
                    'page_count': len(pdf.pages),
                    'table_count': len(all_tables),
                    'is_grid_based': True
                }

                logger.info(f"[B-4] 抽出完了: 表={len(all_tables)}, 単語（削除対象）={len(all_words)}")

                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-4] テキスト削除完了: {purged_pdf_path}")

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
            logger.error(f"[B-4] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _extract_non_table_words(
        self,
        page_words: List[Dict],
        table_bboxes: List[tuple]
    ) -> List[Dict]:
        """
        ページ単語リストから表領域外の単語だけを返す。
        判定: 単語の中心点がいずれの表 bbox にも含まれない。
        """
        result = []
        for word in page_words:
            cx = (word['x0'] + word['x1']) / 2
            cy = (word['top'] + word['bottom']) / 2
            in_table = any(
                tx0 <= cx <= tx1 and ty0 <= cy <= ty1
                for tx0, ty0, tx1, ty1 in table_bboxes
            )
            if not in_table:
                result.append(word)
        return result

    def _group_words_into_blocks(
        self,
        words: List[Dict],
        page_num: int,
        y_line_tol: int = 3,
        y_block_gap: int = 12,
    ) -> List[Dict[str, Any]]:
        """
        表外単語を行→段落ブロックにまとめて logical_blocks 形式で返す。

        Args:
            words: pdfplumber の extract_words() 結果（表外フィルタ済み）
            page_num: ページ番号
            y_line_tol: 同一行とみなす top 差の許容値（pt）
            y_block_gap: ブロック間とみなす行間ギャップ（pt）
        """
        if not words:
            return []

        # top → x0 の順でソート
        words_sorted = sorted(words, key=lambda w: (w['top'], w['x0']))

        # 単語 → 行にグループ化
        lines: List[List[Dict]] = []
        current_line = [words_sorted[0]]
        for word in words_sorted[1:]:
            if abs(word['top'] - current_line[0]['top']) <= y_line_tol:
                current_line.append(word)
            else:
                lines.append(current_line)
                current_line = [word]
        lines.append(current_line)

        # 行 → ブロックにグループ化
        block_groups: List[List[List[Dict]]] = []
        current_group = [lines[0]]
        for line in lines[1:]:
            prev_bottom = max(w['bottom'] for w in current_group[-1])
            curr_top = min(w['top'] for w in line)
            if curr_top - prev_bottom > y_block_gap:
                block_groups.append(current_group)
                current_group = [line]
            else:
                current_group.append(line)
        block_groups.append(current_group)

        # ブロックを logical_block 形式に変換
        result = []
        for group in block_groups:
            all_w = [w for line in group for w in line]
            text = '\n'.join(
                ' '.join(w['text'] for w in sorted(line, key=lambda w: w['x0']))
                for line in group
            )
            x0 = min(w['x0'] for w in all_w)
            y0 = min(w['top'] for w in all_w)
            x1 = max(w['x1'] for w in all_w)
            y1 = max(w['bottom'] for w in all_w)
            result.append({
                'page': page_num,
                'type': 'text',
                'text': text,
                'bbox': (x0, y0, x1, y1),
            })
        return result

    def _extract_grid_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        """
        格子解析により表を抽出

        Args:
            page: pdfplumberのPageオブジェクト
            page_num: ページ番号

        Returns:
            [{
                'page': int,
                'index': int,
                'rows': int,
                'cols': int,
                'data': [[...], [...], ...],
                'bbox': tuple
            }, ...]
        """
        tables = []

        # pdfplumberの表検出を使用
        detected_tables = page.find_tables()

        for idx, table in enumerate(detected_tables):
            # 表データを抽出
            data = table.extract()

            if not data:
                continue

            # 行列数を取得
            row_count = len(data)
            col_count = len(data[0]) if data else 0

            logger.info(f"[B-4] Table {idx} (Page {page_num}): {row_count}行×{col_count}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-4] Table {idx} 行{row_idx}: {row}")

            tables.append({
                'page': page_num,
                'index': idx,
                'rows': row_count,
                'cols': col_count,
                'data': data,
                'bbox': table.bbox,
                'source': 'stage_b'
            })

        return tables

    def _build_text(
        self,
        tables: List[Dict[str, Any]],
        text_blocks: List[Dict[str, Any]] = None,
    ) -> str:
        """
        表テキストと表外テキストをページ順に統合して生成する。

        Returns:
            [TABLE page=X index=Y]...[/TABLE] および
            [TEXT page=X]...[/TEXT] を混在させた文字列
        """
        # ページ・bbox.y0 でソートできるよう (page, y0, fragment) のリストを作る
        fragments = []

        for table in tables:
            header = f"[TABLE page={table['page']} index={table['index']} rows={table['rows']} cols={table['cols']}]"
            rows = []
            for row in table['data']:
                row_data = [str(cell) if cell is not None else '' for cell in row]
                rows.append(" | ".join(row_data))
            body = f"{header}\n" + "\n".join(rows) + "\n[/TABLE]"
            y0 = table['bbox'][1] if table.get('bbox') else 0
            fragments.append((table['page'], y0, body))

        for block in (text_blocks or []):
            body = f"[TEXT page={block['page']}]\n{block['text']}\n[/TEXT]"
            y0 = block['bbox'][1] if block.get('bbox') else 0
            fragments.append((block['page'], y0, body))

        fragments.sort(key=lambda f: (f[0], f[1]))
        return "\n\n".join(f[2] for f in fragments)

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
            logger.error("[B-4] PyMuPDF がインストールされていません")
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
            purged_pdf_path = purged_dir / f"b4_{file_path.stem}_purged.pdf"

            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-4] テキスト削除: {deleted_words}語")
            if deleted_table_graphics > 0:
                logger.info(f"[B-4] 表罫線削除: {deleted_table_graphics}表（抽出済みのため）")
            else:
                logger.info(f"[B-4] 表罫線: 保持（Stage D 検出用）")
            logger.info(f"[B-4] purged PDF 保存: {purged_pdf_path.name}")

            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-4] テキスト削除エラー: {e}", exc_info=True)
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
