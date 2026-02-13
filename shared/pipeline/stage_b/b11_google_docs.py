"""
B-11: Google Docs Processor（Google Docs由来PDF専用）

pdfplumber を使用して、Google Docs由来PDFからテキストと表を抽出。
extract_text() でページ単位テキスト、find_tables() で表を補助的に抽出。

【抽出＋削除統合】
- テキスト抽出と同時に、抽出した bbox を PDF から削除
- インペインティングで背景を自然に補完
- purged_pdf_path を Stage D に渡す
"""

from pathlib import Path
from typing import Dict, Any, List
from loguru import logger


class B11GoogleDocsProcessor:
    """B-11: Google Docs Processor（Google Docs由来PDF専用）"""

    def process(self, file_path: Path) -> Dict[str, Any]:
        """
        Google Docs由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'is_structured': bool,
                'text_with_tags': str,           # ページ単位テキスト
                'logical_blocks': [...],         # ページ単位ブロック
                'structured_tables': [...],      # 表構造データ
                'tags': {...},                   # メタ情報
                'purged_image_path': str
            }
        """
        logger.info(f"[B-11] Google Docs処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-11] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logical_blocks = []
                all_tables = []
                all_words = []  # 削除対象の全単語

                for page_num, page in enumerate(pdf.pages):
                    # ページ全体のテキストを抽出
                    text = page.extract_text() or ""

                    logical_blocks.append({
                        'page': page_num,
                        'type': 'page',
                        'text': text,
                    })

                    # 表を補助的に抽出
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

                tags = {
                    'page_count': len(pdf.pages),
                    'block_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'is_google_docs': True,
                }

                logger.info(f"[B-11] 抽出完了:")
                logger.info(f"  ├─ ページ: {len(logical_blocks)}")
                logger.info(f"  ├─ 表: {len(all_tables)}")
                logger.info(f"  └─ 全単語（削除対象）: {len(all_words)}")

                # ════════════════════════════════════════
                # 抽出＋削除統合: 抽出したテキストを即座に削除
                # ════════════════════════════════════════
                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-11] テキスト削除完了: {purged_pdf_path}")

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
            logger.error(f"[B-11] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _extract_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        """
        ページ内の表を抽出

        Args:
            page: pdfplumberのPageオブジェクト
            page_num: ページ番号

        Returns:
            表構造データのリスト
        """
        tables = []
        for idx, table in enumerate(page.find_tables()):
            data = table.extract()
            if not data:
                continue

            tables.append({
                'page': page_num,
                'index': idx,
                'rows': len(data),
                'cols': len(data[0]) if data else 0,
                'data': data,
                'bbox': table.bbox,
            })

        return tables

    def _build_text(self, logical_blocks: List[Dict[str, Any]]) -> str:
        """
        ページ単位テキストを生成

        Args:
            logical_blocks: ページ単位ブロックリスト

        Returns:
            [PAGE page=X]...[/PAGE] 形式
        """
        result = []

        for block in logical_blocks:
            header = f"[PAGE page={block['page']}]"
            footer = "[/PAGE]"
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
            logger.error("[B-11] PyMuPDF がインストールされていません")
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
            purged_pdf_path = purged_dir / f"b11_{file_path.stem}_purged.pdf"

            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-11] テキスト削除: {deleted_words}語")
            if deleted_table_graphics > 0:
                logger.info(f"[B-11] 表罫線削除: {deleted_table_graphics}表（抽出済みのため）")
            else:
                logger.info(f"[B-11] 表罫線: 保持（Stage D 検出用）")
            logger.info(f"[B-11] purged PDF 保存: {purged_pdf_path.name}")

            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-11] テキスト削除エラー: {e}", exc_info=True)
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
            'purged_pdf_path': '',
        }
