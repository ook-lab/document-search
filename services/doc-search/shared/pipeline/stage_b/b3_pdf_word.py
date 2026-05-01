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

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        Word由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス
            masked_pages: スキップするページ番号リスト（0始まり）
            log_file: 個別ログファイルパス（Noneなら共有ロガーのみ）

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
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-3]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
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

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-3] ページ{page_num+1}: マスク → スキップ")
                        continue
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

                    # B-90 消去用：pdfplumber chars で1文字単位で全回収（取りこぼし防止）
                    for ch in (page.chars or []):
                        if ch.get('text', '').strip():
                            all_words.append({
                                'page': page_num,
                                'text': ch['text'],
                                'bbox': (ch['x0'], ch['top'], ch['x1'], ch['bottom'])
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
                for idx, block in enumerate(logical_blocks):
                    logger.info(f"[B-3] block{idx} (page={block.get('page')}, slice={block.get('slice')}): {block.get('text', '')}")

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
        指定範囲からテキストブロックを抽出（char単位でルビを先に除外して行復元）

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

        if table_bboxes is None:
            table_bboxes = []

        # char単位で全取得（空白文字除外）
        chars = [ch for ch in (cropped.chars or []) if ch.get("text", "").strip()]

        # 表領域内のcharを除外
        chars = [
            ch for ch in chars
            if not self._is_inside_any_table(
                (ch["x0"], ch["top"], ch["x1"], ch["bottom"]), table_bboxes
            )
        ]

        # ルビ除外（size > 0 かつ size <= RUBY_SIZE_THRESHOLD）
        body_chars = []
        for ch in chars:
            size = float(ch.get("size") or 0)
            if 0 < size <= self.RUBY_SIZE_THRESHOLD:
                logger.debug(f"[B-3] ルビ除外: '{ch['text']}' (size={size:.1f}pt)")
                continue
            body_chars.append(ch)

        # 装飾検出
        has_bold = any('bold' in ch.get('fontname', '').lower() for ch in body_chars)
        has_red = False  # TODO: color情報で赤字検出

        # 行クラスタリングでテキスト復元
        text = self._chars_to_text_lines(body_chars)

        return {
            'page': page_num,
            'slice': slice_idx,
            'bbox': bbox,
            'text': text,
            'has_red': has_red,
            'has_bold': has_bold,
            'word_count': len(text.split()) if text else 0
        }

    def _chars_to_text_lines(self, chars: List[Dict]) -> str:
        """
        char列をtop座標でクラスタリングして行復元し、改行で連結して返す

        Args:
            chars: pdfplumber の char オブジェクトのリスト

        Returns:
            復元テキスト（行間は改行）
        """
        if not chars:
            return ""

        # top座標でソート
        sorted_chars = sorted(chars, key=lambda c: (c["top"], c["x0"]))

        # 行クラスタリング（tol=2.0pt）
        tol = 2.0
        lines: List[List[Dict]] = []
        current_line = [sorted_chars[0]]
        current_top = sorted_chars[0]["top"]

        for ch in sorted_chars[1:]:
            if abs(ch["top"] - current_top) <= tol:
                current_line.append(ch)
            else:
                lines.append(current_line)
                current_line = [ch]
                current_top = ch["top"]
        lines.append(current_line)

        # 各行をx0順に並べてテキスト化
        result_lines = []
        for line_chars in lines:
            line_chars_sorted = sorted(line_chars, key=lambda c: c["x0"])
            result_lines.append(self._line_chars_to_text(line_chars_sorted))

        return "\n".join(result_lines)

    def _line_chars_to_text(self, line_chars: List[Dict]) -> str:
        """
        1行分のchar列を文字幅中央値基準のギャップ検出でスペース挿入しながらテキスト化

        Args:
            line_chars: x0順に並んだ1行分のcharリスト

        Returns:
            スペース挿入済みのテキスト
        """
        if not line_chars:
            return ""

        # 文字幅の中央値でgap_thresholdを決定
        import statistics
        widths = [ch["x1"] - ch["x0"] for ch in line_chars if ch["x1"] > ch["x0"]]
        gap_threshold = statistics.median(widths) * 0.8 if widths else 3.0

        result = line_chars[0]["text"]
        for i in range(1, len(line_chars)):
            prev = line_chars[i - 1]
            curr = line_chars[i]
            gap = curr["x0"] - prev["x1"]
            if gap > gap_threshold:
                result += " "
            result += curr["text"]

        return result

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
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-3] Table {idx} 行{row_idx}: {row}")

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
        抽出したテキストを PDF から削除

        フェーズ1: 抽出した文字の bbox のみ redaction（抽出できた部分だけ消す）
        フェーズ2: 残存テキスト検査 → 残っていたページだけ追いredact（最大5回）
        フェーズ3: それでも残るページのみ BT..ET 全消し（ページ限定フォールバック）
        フェーズ4: 表罫線削除（structured_tables がある場合のみ）
        """
        try:
            import fitz
        except ImportError:
            logger.error("[B-3] PyMuPDF がインストールされていません")
            return file_path

        try:
            doc = fitz.open(str(file_path))
            page_count = len(doc)

            # フェーズ1: 抽出した文字 bbox のみ redaction
            words_by_page: Dict[int, List[Dict]] = {}
            for w in all_words:
                words_by_page.setdefault(w['page'], []).append(w)

            deleted_chars = 0
            for page_num in range(page_count):
                page = doc[page_num]
                for w in words_by_page.get(page_num, []):
                    page.add_redact_annot(fitz.Rect(w['bbox']))
                    deleted_chars += 1
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_NONE,
                    graphics=False
                )

            logger.info(f"[B-3] フェーズ1: 抽出文字を削除 {deleted_chars}文字")

            # フェーズ2: 残存テキスト検査 → 追いredact（最大5回）
            for attempt in range(1, 6):
                remaining_pages = []
                for page_num in range(page_count):
                    page = doc[page_num]
                    if page.get_text("text").strip():
                        remaining_pages.append(page_num)

                if not remaining_pages:
                    break

                logger.info(f"[B-3] フェーズ2 試行{attempt}: 残存テキストあり {remaining_pages}")
                for page_num in remaining_pages:
                    page = doc[page_num]
                    for block in page.get_text("rawdict").get("blocks", []):
                        if block.get("type") != 0:
                            continue
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                if span.get("text", "").strip():
                                    page.add_redact_annot(fitz.Rect(span["bbox"]))
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=False
                    )

            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b3_{file_path.stem}_purged.pdf"
            doc.save(str(purged_pdf_path))
            doc.close()

            # フェーズ4: 表罫線削除（structured_tables がある場合のみ）
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
                    logger.info(f"[B-3] フェーズ4: 表罫線削除 {deleted_table_graphics}表")
                except Exception as e:
                    logger.warning(f"[B-3] 表罫線削除エラー: {e}")

            logger.info(f"[B-3] purged PDF 保存: {purged_pdf_path.name}")
            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-3] purge エラー: {e}", exc_info=True)
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
