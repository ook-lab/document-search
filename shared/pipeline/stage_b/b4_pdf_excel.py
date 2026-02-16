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

    def process(self, file_path: Path) -> Dict[str, Any]:
        """
        Excel由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス

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
                all_words = []  # 削除対象の全単語

                for page_num, page in enumerate(pdf.pages):
                    # 格子解析により表を検出
                    tables = self._extract_grid_tables(page, page_num)
                    all_tables.extend(tables)

                    # ページ全体を1つのブロックとして扱う
                    logical_blocks.append({
                        'page': page_num,
                        'type': 'sheet',
                        'table_count': len(tables)
                    })

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
                text_with_tags = self._build_text(all_tables)

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
            if data and len(data) > 0:
                first_row_sample = str(data[0][:min(3, len(data[0]))])[:100]
                logger.debug(f"[B-4] Table {idx} 1行目サンプル: {first_row_sample}")

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

    def _build_text(self, tables: List[Dict[str, Any]]) -> str:
        """
        全表のテキストを生成

        Args:
            tables: 表リスト

        Returns:
            [TABLE page=X index=Y]...[/TABLE] 形式
        """
        result = []

        for table in tables:
            header = f"[TABLE page={table['page']} index={table['index']} rows={table['rows']} cols={table['cols']}]"
            footer = "[/TABLE]"

            # 表データをテキスト化
            rows = []
            for row in table['data']:
                # None を空文字列に変換
                row_data = [str(cell) if cell is not None else '' for cell in row]
                rows.append(" | ".join(row_data))

            result.append(f"{header}\n" + "\n".join(rows) + f"\n{footer}")

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
