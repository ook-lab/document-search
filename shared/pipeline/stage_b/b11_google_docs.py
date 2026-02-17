"""
B-11: Google Docs Processor（Google Docs由来PDF専用）

PyMuPDF でテキストブロック単位抽出、pdfplumber で表を抽出。
- PyMuPDF get_text("blocks"): PDFのテキストボックス単位（結合も分解もしない）
- pdfplumber find_tables(): 表構造抽出

【抽出＋削除統合】
- テキスト抽出と同時に、抽出した bbox を PDF から削除
- インペインティングで背景を自然に補完
- purged_pdf_path を Stage D に渡す
"""

from pathlib import Path
from typing import Dict, Any, List
from loguru import logger

try:
    import fitz  # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False
    logger.warning("[B-11] PyMuPDF (fitz) がインストールされていません")


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

        if not FITZ_AVAILABLE:
            logger.error("[B-11] PyMuPDF (fitz) が必要です")
            return self._error_result("PyMuPDF not installed")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-11] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            # ★PyMuPDFでテキストブロックを抽出
            fitz_doc = fitz.open(str(file_path))

            # ★pdfplumberで表を抽出
            with pdfplumber.open(str(file_path)) as pdf:
                logical_blocks = []
                all_tables = []
                all_text_blocks = []  # 削除対象の全テキストブロック

                for page_num in range(len(fitz_doc)):
                    fitz_page = fitz_doc[page_num]
                    plumber_page = pdf.pages[page_num]

                    # ★1. 表を抽出（pdfplumber）
                    tables = self._extract_tables(plumber_page, page_num)
                    all_tables.extend(tables)

                    # 表のbboxリストを作成
                    table_bboxes = [table['bbox'] for table in tables]

                    # ★2. テキストブロックを抽出（PyMuPDF）
                    # get_text("blocks"): PDFのテキストボックス単位
                    text_blocks = fitz_page.get_text("blocks")

                    for block in text_blocks:
                        # block形式: (x0, y0, x1, y1, text, block_no, block_type)
                        # block_type: 0=text, 1=image
                        if len(block) < 7:
                            continue

                        x0, y0, x1, y1, text, block_no, block_type = block[:7]

                        # テキストブロックのみ処理
                        if block_type != 0:
                            continue

                        text = text.strip()
                        if not text:
                            continue

                        block_bbox = (x0, y0, x1, y1)

                        # 削除用：全テキストブロックを収集
                        all_text_blocks.append({
                            'page': page_num,
                            'text': text,
                            'bbox': block_bbox
                        })

                        # 表領域外のテキストブロックのみをlogical_blocksに追加
                        if not self._is_inside_any_table(block_bbox, table_bboxes):
                            logical_blocks.append({
                                'page': page_num,
                                'type': 'text_block',
                                'text': text,
                                'bbox': [x0, y0, x1, y1]
                            })

                fitz_doc.close()

                # テキストを生成
                text_with_tags = self._build_text(logical_blocks)

                tags = {
                    'page_count': len(pdf.pages),
                    'block_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'is_google_docs': True,
                }

                logger.info(f"[B-11] 抽出完了:")
                logger.info(f"  ├─ テキストブロック: {len(logical_blocks)}")
                logger.info(f"  ├─ 表: {len(all_tables)}")
                logger.info(f"  └─ 全テキストブロック（削除対象）: {len(all_text_blocks)}")

                # デバッグ：各表のdataサイズを確認
                for idx, tbl in enumerate(all_tables):
                    data_size = len(tbl.get('data', [])) if isinstance(tbl.get('data'), list) else 'N/A'
                    logger.debug(f"[B-11] all_tables[{idx}]: data={data_size}行, has_data_key={'data' in tbl}")

                # ════════════════════════════════════════
                # 抽出＋削除統合: 抽出したテキストを即座に削除
                # ════════════════════════════════════════
                purged_pdf_path = self._purge_extracted_text(file_path, all_text_blocks, all_tables)
                logger.info(f"[B-11] テキスト削除完了: {purged_pdf_path}")

                return {
                    'success': True,
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'all_text_blocks': all_text_blocks,
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
            logger.debug(f"[B-11] Page{page_num} Table{idx}: extract()={len(data) if data else 0}行, bbox={table.bbox}")
            if not data:
                logger.warning(f"[B-11] Table {idx}: extract()が空のためスキップ")
                continue

            # データの健全性チェック
            is_broken = False

            # 1行のデータの場合：全セルがnullまたは空なら壊れている
            if len(data) == 1:
                first_row = data[0]
                if isinstance(first_row, list):
                    non_null_count = sum(1 for cell in first_row if cell is not None and cell != '')
                    if non_null_count == 0:
                        is_broken = True
                        logger.warning(f"[B-11] Table {idx}: 1行データが全て空です")

            # 2行以上：2行目がnullだらけなら壊れている
            elif len(data) > 1:
                second_row = data[1]
                if isinstance(second_row, list):
                    non_null_count = sum(1 for cell in second_row if cell is not None and cell != '')
                    # 2行目の80%以上が null または空の場合は壊れていると判断
                    if non_null_count < len(second_row) * 0.2:
                        is_broken = True
                        logger.warning(f"[B-11] Table {idx}: データが壊れています（null多数）")

            # 壊れている場合は再構築
            if is_broken:
                logger.warning(f"[B-11] Table {idx}: bbox内テキストで再構築")
                data = self._rebuild_table_from_text(page, table.bbox)

            # 再構築後も空データ ([[]] など) をチェック
            if not data or data == [[]] or (len(data) == 1 and not data[0]):
                logger.warning(f"[B-11] Table {idx}: 再構築後もデータが空のためスキップ")
                continue

            # デバッグ：追加する表のデータ内容を確認
            logger.info(f"[B-11] Table {idx}: {len(data)}行×{len(data[0]) if data else 0}列 追加")
            if data and len(data) > 0:
                # 最初の数セルをサンプル表示
                first_row_sample = str(data[0][:min(3, len(data[0]))])[:100]
                logger.debug(f"[B-11] Table {idx} 1行目サンプル: {first_row_sample}")

            tables.append({
                'page': page_num,
                'index': idx,
                'rows': len(data),
                'cols': len(data[0]) if data else 0,
                'data': data,
                'bbox': table.bbox,
                'source': 'stage_b',              # F-5が結合処理を認識するために必要
                'origin_uid': f"B:P{page_num}:T{idx}",  # 出自付き一意ID（D表と混線防止）
                'canonical_id': f"T{idx + 1}",    # 汎用ID（後段用）
                'table_id': f"T{idx + 1}",        # 汎用表現（D表と同形式）
            })

        # 全経路で必須キーを補完（appendが複数経路あっても確実に揃える）
        for i, t in enumerate(tables):
            page = t.get('page', 0)
            idx = t.get('index', i)
            t.setdefault('source', 'stage_b')
            t.setdefault('origin_uid', f"B:P{page}:T{idx}")
            t.setdefault('canonical_id', f"T{idx + 1}")
            t.setdefault('table_id', f"T{idx + 1}")

        return tables

    def _is_inside_any_table(self, word_bbox: tuple, table_bboxes: List[tuple]) -> bool:
        """
        単語が表領域内にあるかチェック

        Args:
            word_bbox: 単語のbbox (x0, y0, x1, y1)
            table_bboxes: 表のbboxリスト [(x0, y0, x1, y1), ...]

        Returns:
            表領域内にある場合 True
        """
        if not table_bboxes:
            return False

        wx0, wy0, wx1, wy1 = word_bbox

        for table_bbox in table_bboxes:
            tx0, ty0, tx1, ty1 = table_bbox

            # 単語が表領域内にあるかチェック（完全に内側）
            if wx0 >= tx0 and wx1 <= tx1 and wy0 >= ty0 and wy1 <= ty1:
                return True

        return False

    def _rebuild_table_from_text(self, page, bbox: tuple) -> List[List[str]]:
        """
        bbox 内のテキストから表を再構築

        Args:
            page: pdfplumber Page オブジェクト
            bbox: 表の境界ボックス (x0, y0, x1, y1)

        Returns:
            表データ（行×列の配列）
        """
        try:
            # bbox 内の単語を取得
            x0, y0, x1, y1 = bbox
            cropped = page.crop((x0, y0, x1, y1))
            words = cropped.extract_words(x_tolerance=3, y_tolerance=3)

            if not words:
                logger.warning(f"[B-11] _rebuild_table_from_text: bbox内に単語が見つかりません bbox={bbox}")
                return [[]]

            # Y座標でグループ化して行を検出
            rows_dict = {}
            for word in words:
                y = round(word['top'], 1)
                if y not in rows_dict:
                    rows_dict[y] = []
                rows_dict[y].append(word)

            # Y座標順にソート
            sorted_rows = sorted(rows_dict.items())

            # 各行内を X座標順にソート
            table_data = []
            for y, row_words in sorted_rows:
                sorted_words = sorted(row_words, key=lambda w: w['x0'])
                row_text = [w['text'] for w in sorted_words]
                table_data.append(row_text)

            return table_data if table_data else [[]]

        except Exception as e:
            logger.error(f"[B-11] Table rebuild error: {e}")
            return [[]]

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
