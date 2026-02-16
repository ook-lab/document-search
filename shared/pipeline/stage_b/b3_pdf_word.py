"""
B-3: PDF-Word Processor（PDF-Word専用）

Word由来PDFの「Text Flow（テキストのまとまり）」を維持して抽出。
座標で並べ直さず、pdfplumber の単語順序を尊重する。

【抽出＋削除統合】
- テキスト抽出と同時に、抽出した bbox を PDF から削除（redaction）
- 内側罫線も削除（テキスト近傍の短線）
- 外枠は残す（害がない）
- Stage D には文字が存在しない画像として渡す
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger
import tempfile


class B3PDFWordProcessor:
    """B-3: PDF-Word Processor（PDF-Word専用）"""

    # ルビ除外の閾値（pt）
    RUBY_SIZE_THRESHOLD = 6.0

    # 空白列検出の最小幅（pt）
    GAP_MIN_WIDTH = 10.0

    def process(self, file_path: Path) -> Dict[str, Any]:
        """
        Word由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'is_structured': bool,
                'text_with_tags': str,           # 装飾タグ付きテキスト
                'logical_blocks': [...],         # 分割されたブロック
                'structured_tables': [...],      # 表構造データ
                'tags': {...},                   # メタ情報
                'all_words': [...],              # 全単語（B-90の消去用）
                'purged_image_path': str         # テキスト消去後の画像
            }
        """
        logger.info(f"[B-3] PDF-Word処理開始: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-3] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                # 全ページを処理
                logical_blocks = []
                all_tables = []
                all_words = []  # B-90 消去用：全単語（ルビ・本文問わず）

                for page_num, page in enumerate(pdf.pages):
                    # スライス検出（文字の空白列を基準に分割）
                    slices = self._detect_slices(page)
                    logger.info(f"[B-3] ページ{page_num+1}: {len(slices)}スライス検出")

                    # ★修正: 先に表を検出（表領域を特定するため）
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)

                    # 表のbboxリストを作成
                    table_bboxes = [table['bbox'] for table in tables]

                    # ★修正: 各スライスからテキストを抽出（表領域を除外）
                    for slice_idx, slice_bbox in enumerate(slices):
                        block = self._extract_block(page, slice_bbox, page_num, slice_idx, table_bboxes)
                        if block['text'].strip():  # 空でないブロックのみ追加
                            logical_blocks.append(block)

                    # B-90 消去用：ページ全体の単語を収集（ルビ・本文問わず）
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

                # 装飾タグ付きテキストを生成
                text_with_tags = self._build_tagged_text(logical_blocks)

                # メタ情報
                tags = {
                    'has_slices': len(logical_blocks) > len(pdf.pages),
                    'block_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'has_red_text': any(b.get('has_red', False) for b in logical_blocks),
                    'has_bold_text': any(b.get('has_bold', False) for b in logical_blocks)
                }

                logger.info(f"[B-3] 抽出完了:")
                logger.info(f"  ├─ ブロック: {len(logical_blocks)}")
                logger.info(f"  ├─ 表: {len(all_tables)}")
                logger.info(f"  └─ 全単語（削除対象）: {len(all_words)}")

                # ════════════════════════════════════════
                # 抽出＋削除統合: 抽出したテキストを即座に削除
                # ════════════════════════════════════════
                purged_pdf_path = self._purge_extracted_text(file_path, all_words, all_tables)
                logger.info(f"[B-3] テキスト削除完了: {purged_pdf_path}")

                return {
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'all_words': all_words,  # 参照用（削除済み）
                    'purged_pdf_path': str(purged_pdf_path)  # Stage D 入力
                }

        except Exception as e:
            logger.error(f"[B-3] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _detect_slices(self, page) -> List[Tuple[float, float, float, float]]:
        """
        ページ内の縦方向スライスを検出（文字の空白列を基準）

        垂直線ではなく、文字が存在しない「空白の縦列（ガター）」を検出し、
        左右のコラムを物理的に分離する。

        Args:
            page: pdfplumberのPageオブジェクト

        Returns:
            [(x0, y0, x1, y1), ...] スライスのbboxリスト
        """
        page_width = float(page.width)
        page_height = float(page.height)

        # 全文字の座標を取得
        chars = page.chars

        if not chars:
            # 文字がない場合はページ全体を1スライス
            return [(0, 0, page_width, page_height)]

        # 文字が存在しない「空白の縦列」を検出
        gaps = self._find_text_free_columns(chars, page_width, page_height)

        if not gaps:
            # 空白列がない場合はページ全体を1スライス
            logger.debug(f"[B-3] 空白列なし → 全体を1スライス")
            return [(0, 0, page_width, page_height)]

        # 空白列を境界として、スライスを生成
        slices = []
        prev_x = 0

        for gap_start, gap_end in gaps:
            # 前の境界から空白列開始までをスライスとして追加
            if gap_start > prev_x + 5:  # 最小幅5pt
                slices.append((prev_x, 0, gap_start, page_height))
            prev_x = gap_end

        # 最後のスライス（最後の空白列からページ右端まで）
        if prev_x < page_width - 5:
            slices.append((prev_x, 0, page_width, page_height))

        logger.debug(f"[B-3] 空白列検出: {len(gaps)}箇所 → {len(slices)}スライス")
        return slices if slices else [(0, 0, page_width, page_height)]

    def _find_text_free_columns(
        self,
        chars: List[Dict],
        page_width: float,
        page_height: float
    ) -> List[Tuple[float, float]]:
        """
        文字が存在しない「空白の縦列」を検出

        Args:
            chars: 文字リスト
            page_width: ページ幅
            page_height: ページ高さ

        Returns:
            [(gap_start_x, gap_end_x), ...] 空白列の開始・終了座標
        """
        # x座標を1pt刻みでスキャン
        SCAN_STEP = 1.0
        occupied = [False] * int(page_width)

        # 各文字が占める x 範囲をマーク
        for char in chars:
            x0 = int(char['x0'])
            x1 = int(char['x1'])
            for x in range(max(0, x0), min(len(occupied), x1)):
                occupied[x] = True

        # 連続した空白領域を検出
        gaps = []
        gap_start = None

        for x in range(len(occupied)):
            if not occupied[x]:
                # 空白開始
                if gap_start is None:
                    gap_start = x
            else:
                # 空白終了
                if gap_start is not None:
                    gap_width = x - gap_start
                    if gap_width >= self.GAP_MIN_WIDTH:
                        gaps.append((float(gap_start), float(x)))
                    gap_start = None

        # 最後の空白
        if gap_start is not None:
            gap_width = len(occupied) - gap_start
            if gap_width >= self.GAP_MIN_WIDTH:
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
        """
        指定範囲からテキストブロックを抽出（Wordの単語順序を維持）

        Args:
            page: pdfplumberのPageオブジェクト
            bbox: 抽出範囲 (x0, y0, x1, y1)
            page_num: ページ番号
            slice_idx: スライスインデックス
            table_bboxes: 表のbboxリスト（除外用）

        Returns:
            {
                'page': int,
                'slice': int,
                'bbox': tuple,
                'text': str,
                'has_red': bool,
                'has_bold': bool,
                'word_count': int
            }
        """
        x0, y0, x1, y1 = bbox
        cropped = page.within_bbox((x0, y0, x1, y1))

        # 単語を抽出（pdfplumber が Word の並びをある程度保持）
        words = cropped.extract_words(
            x_tolerance=3,
            y_tolerance=3,
            keep_blank_chars=False
        )

        if table_bboxes is None:
            table_bboxes = []

        # ルビを除外（フォントサイズ 6pt 以下）
        # ただし、size=0.0pt（サイズ不明）は本文として救済する
        # ★修正: 表領域の単語も除外
        body_words = []
        has_red = False
        has_bold = False

        for word in words:
            # ★修正: 表領域内の単語をスキップ
            word_bbox = (word['x0'], word['top'], word['x1'], word['bottom'])
            if self._is_inside_any_table(word_bbox, table_bboxes):
                continue
            avg_size = self._get_avg_size(word)

            # ルビ判定（小さいフォントサイズ）
            # size=0.0pt は「サイズ不明」なので本文として扱う（救済）
            if 0 < avg_size <= self.RUBY_SIZE_THRESHOLD:
                logger.debug(f"[B-3] ルビ除外: '{word['text']}' (size={avg_size:.1f}pt)")
                continue

            # size=0.0pt または size > 6.0pt は本文として採用
            if avg_size == 0.0:
                logger.debug(f"[B-3] サイズ不明を救済: '{word['text']}' (size=0.0pt → 本文扱い)")

            body_words.append(word)

            # 装飾検出
            fontname = word.get('fontname', '').lower()
            if 'bold' in fontname:
                has_bold = True

            # TODO: 赤字検出（pdfplumber の color 情報を活用）

        # 単語を結合して「意味のある文章」を復元
        # Word の並び順を維持（座標で並べ直さない）
        text = " ".join([w['text'] for w in body_words])

        return {
            'page': page_num,
            'slice': slice_idx,
            'bbox': bbox,
            'text': text,
            'has_red': has_red,
            'has_bold': has_bold,
            'word_count': len(body_words)
        }

    def _get_avg_size(self, word: Dict[str, Any]) -> float:
        """
        単語の平均フォントサイズを取得

        Args:
            word: pdfplumber の word オブジェクト

        Returns:
            平均フォントサイズ（pt）
        """
        # pdfplumber の word には 'chars' が含まれる
        chars = word.get('chars', [])
        if not chars:
            return 0.0

        sizes = [char.get('size', 0) for char in chars]
        return sum(sizes) / len(sizes) if sizes else 0.0

    def _is_inside_any_table(self, word_bbox: Tuple[float, float, float, float], table_bboxes: List[Tuple[float, float, float, float]]) -> bool:
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

    def _extract_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        """
        ページ内の表を抽出

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
            logger.info(f"[B-3] Table {idx} (Page {page_num}): {len(data) if data else 0}行×{len(data[0]) if data and len(data) > 0 else 0}列")
            if data and len(data) > 0:
                first_row_sample = str(data[0][:min(3, len(data[0]))])[:100]
                logger.debug(f"[B-3] Table {idx} 1行目サンプル: {first_row_sample}")

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

    def _build_tagged_text(self, logical_blocks: List[Dict[str, Any]]) -> str:
        """
        装飾タグ付きテキストを生成

        Args:
            logical_blocks: 論理ブロックリスト

        Returns:
            [BLOCK page=X slice=Y]...[/BLOCK] 形式
        """
        result = []

        for block in logical_blocks:
            header = f"[BLOCK page={block['page']} slice={block['slice']}]"
            footer = "[/BLOCK]"

            # 赤字・ボールドのタグ付け（簡易版）
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
            logger.error("[B-3] PyMuPDF がインストールされていません")
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
            purged_pdf_path = purged_dir / f"b3_{file_path.stem}_purged.pdf"

            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-3] テキスト削除: {deleted_words}語")
            if deleted_table_graphics > 0:
                logger.info(f"[B-3] 表罫線削除: {deleted_table_graphics}表（抽出済みのため）")
            else:
                logger.info(f"[B-3] 表罫線: 保持（Stage D 検出用）")
            logger.info(f"[B-3] purged PDF 保存: {purged_pdf_path.name}")

            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-3] テキスト削除エラー: {e}", exc_info=True)
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
