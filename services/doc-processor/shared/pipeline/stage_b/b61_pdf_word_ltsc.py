"""
B-61: PDF-Word LTSC Processor（Microsoft® Word LTSC 専用）

Word LTSC 由来PDF向け。ルビ（振り仮名）を除外してテキストを抽出する。
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B61PDFWordLTSCProcessor:
    """B-61: PDF-Word LTSC Processor（Microsoft® Word LTSC 専用）"""

    # ルビ除外の閾値（pt）
    RUBY_SIZE_THRESHOLD = 6.0

    # 空白列検出の最小幅（pt）
    GAP_MIN_WIDTH = 10.0

    # ToUnicode品質ゲートの閾値
    # 文字描画rawbytesに対するToUnicodeありフォントの比率。
    # 実測値: GS rebuild 後の "良いページ"=0.9894 / "悪いページ"=0.9661
    # 0.985 = 両者の間で最も厳しい現実的な境界値
    TOUNICODE_MIN_RATIO = 0.985

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """Word LTSC 由来PDFから構造化データを抽出"""
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-61]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(file_path, masked_pages)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _rebuild_with_ghostscript(self, file_path: Path) -> Path:
        """Ghostscriptで再構築。ToUnicodeを作り直す。失敗したらエラーを上げる（フォールバックなし）。"""
        import subprocess
        import shutil

        gs_cmd = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
        if not gs_cmd:
            # Windowsの固定インストールパスを探す
            import glob as _glob
            candidates = _glob.glob(r"C:\Program Files\gs\gs*\bin\gswin64c.exe") + \
                         _glob.glob(r"C:\Program Files\gs\gs*\bin\gswin32c.exe") + \
                         _glob.glob(r"C:\Program Files (x86)\gs\gs*\bin\gswin64c.exe") + \
                         _glob.glob(r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe")
            if candidates:
                gs_cmd = candidates[-1]  # 最新バージョン（末尾）
                logger.info(f"[B-61] Ghostscript固定パスで発見: {gs_cmd}")
            else:
                raise RuntimeError("Ghostscriptが見つかりません。インストールしてください: https://www.ghostscript.com/")

        rebuilt_dir = file_path.parent / "rebuilt"
        rebuilt_dir.mkdir(parents=True, exist_ok=True)
        rebuilt_path = rebuilt_dir / f"b61_{file_path.stem}_rebuilt.pdf"

        cmd = [
            gs_cmd,
            "-dNOPAUSE", "-dBATCH", "-dQUIET",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.7",
            f"-sOutputFile={rebuilt_path}",
            str(file_path),
        ]
        logger.info(f"[B-61] Ghostscript再構築: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Ghostscript失敗 (rc={result.returncode}): {result.stderr[:500]}")

        logger.info(f"[B-61] Ghostscript再構築完了: {rebuilt_path.name}")
        return rebuilt_path

    def _process_impl(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        logger.info(f"[B-61] PDF-Word LTSC処理開始: {file_path.name}")

        try:
            import pdfplumber
            import fitz
            from pypdf import PdfReader as _PdfReader
        except ImportError as e:
            logger.error(f"[B-61] 必要なライブラリがありません: {e}")
            return self._error_result(str(e))

        # Ghostscriptで再構築（ToUnicodeを作り直す）
        try:
            rebuilt_path = self._rebuild_with_ghostscript(file_path)
        except Exception as e:
            logger.error(f"[B-61] Ghostscript再構築失敗: {e}")
            return self._error_result(str(e))

        fitz_doc = fitz.open(str(rebuilt_path))
        pypdf_reader = _PdfReader(str(rebuilt_path))
        try:
            with pdfplumber.open(str(rebuilt_path)) as pdf:
                logical_blocks = []
                all_tables = []
                all_words = []

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-61] ページ{page_num+1}: マスク → スキップ")
                        continue

                    # ToUnicode品質ゲート（抽出・purge両方をブロック）
                    q = self._page_tounicode_quality(pypdf_reader, pypdf_reader.pages[page_num])
                    tou_ratio = q["tounicode_ratio"]
                    if tou_ratio < self.TOUNICODE_MIN_RATIO:
                        logger.warning(
                            f"[B-61] ページ{page_num+1}: ToUnicode品質不足 "
                            f"ratio={tou_ratio:.4f} < {self.TOUNICODE_MIN_RATIO} "
                            f"→ 抽出・purgeスキップ | "
                            f"ToUnicodeなしフォント={q['fonts_without_tounicode']}"
                        )
                        continue
                    logger.debug(f"[B-61] ページ{page_num+1}: ToUnicode品質OK ratio={tou_ratio:.4f}")

                    slices = self._detect_slices(page)
                    logger.info(f"[B-61] ページ{page_num+1}: {len(slices)}スライス検出")

                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)
                    table_bboxes = [table['bbox'] for table in tables]

                    fitz_chars = self._extract_fitz_chars(fitz_doc[page_num])
                    logger.info(f"[B-61] ページ{page_num+1}: fitz文字数={len(fitz_chars)}")

                    for slice_idx, slice_bbox in enumerate(slices):
                        block = self._extract_block_fitz(fitz_chars, slice_bbox, page_num, slice_idx, table_bboxes)
                        if block['text'].strip():
                            logical_blocks.append(block)

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
                    'has_red_text': any(b.get('has_red', False) for b in logical_blocks),
                    'has_bold_text': any(b.get('has_bold', False) for b in logical_blocks)
                }

                logger.info(f"[B-61] 抽出完了:")
                logger.info(f"[B-61]   ├─ ブロック: {len(logical_blocks)}")
                logger.info(f"[B-61]   ├─ 表: {len(all_tables)}")
                logger.info(f"[B-61]   └─ 全単語（削除対象）: {len(all_words)}")

                purged_pdf_path = self._purge_extracted_text(rebuilt_path, all_words, all_tables)
                logger.info(f"[B-61] テキスト削除完了: {purged_pdf_path}")

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
            logger.error(f"[B-61] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))
        finally:
            fitz_doc.close()

    def _page_tounicode_quality(self, reader, page) -> dict:
        """
        ページの ToUnicode 品質を計測する。

        content stream を走査して文字描画（Tj/TJ）の rawbytes を集計し、
        「ToUnicode を持つフォントが担う rawbytes 比率」を返す。
        比率が TOUNICODE_MIN_RATIO を下回るページは抽出・purge 両方をスキップする。

        Returns:
            {
                "total_raw_bytes": int,
                "tounicode_raw_bytes": int,
                "tounicode_ratio": float,          # 0.0〜1.0
                "fonts_without_tounicode": list[str],
            }
        """
        from collections import defaultdict

        def _resolve(obj):
            try:
                return obj.get_object()
            except Exception:
                return obj

        # フォントタグ → ToUnicode有無
        res = _resolve(page.get("/Resources"))
        fonts = _resolve(res.get("/Font")) if res and "/Font" in res else {}
        font_has_tou = {}
        for tag, fref in (fonts or {}).items():
            fobj = _resolve(fref)
            font_has_tou[str(tag)] = (fobj.get("/ToUnicode") is not None)

        # content stream 走査
        try:
            from pypdf.generic import ContentStream
            cs = ContentStream(page.get_contents(), reader)
        except Exception as e:
            logger.warning(f"[B-61] ToUnicode品質計測: ContentStream解析失敗 → ratio=0.0: {e}")
            return {
                "total_raw_bytes": 0,
                "tounicode_raw_bytes": 0,
                "tounicode_ratio": 0.0,
                "fonts_without_tounicode": [],
            }

        current_font = None
        usage_bytes = defaultdict(int)

        for operands, op in cs.operations:
            if op == b"Tf":
                current_font = str(operands[0])

            elif op == b"Tj":
                s = operands[0]
                b = getattr(s, "original_bytes", None)
                if b is None:
                    try:
                        b = bytes(s)
                    except Exception:
                        b = b""
                usage_bytes[current_font or "(none)"] += len(b)

            elif op == b"TJ":
                arr = operands[0]
                chunk = 0
                for item in arr:
                    b = getattr(item, "original_bytes", None)
                    if b is not None:
                        chunk += len(b)
                    elif isinstance(item, (bytes, bytearray)):
                        chunk += len(item)
                usage_bytes[current_font or "(none)"] += chunk

        total = sum(usage_bytes.values())
        tou_total = sum(v for k, v in usage_bytes.items() if font_has_tou.get(k, False))
        ratio = (tou_total / total) if total else 0.0

        return {
            "total_raw_bytes": total,
            "tounicode_raw_bytes": tou_total,
            "tounicode_ratio": ratio,
            "fonts_without_tounicode": [
                k for k, v in usage_bytes.items()
                if v > 0 and not font_has_tou.get(k, False)
            ],
        }

    def _extract_fitz_chars(self, fitz_page) -> List[Dict]:
        """fitz(MuPDF)でchar単位抽出。MuPDF の CMap 処理で LTSC 文字化け補正。"""
        import fitz as _fitz
        chars = []
        rawdict = fitz_page.get_text("rawdict", flags=_fitz.TEXT_PRESERVE_WHITESPACE)
        for block in rawdict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size", 0)
                    fontname = span.get("font", "")
                    for ch in span.get("chars", []):
                        c = ch.get("c", "")
                        if not c.strip():
                            continue
                        bbox = ch.get("bbox")
                        if bbox:
                            chars.append({
                                "text": c,
                                "x0": bbox[0],
                                "top": bbox[1],
                                "x1": bbox[2],
                                "bottom": bbox[3],
                                "size": size,
                                "fontname": fontname,
                            })
        return chars

    def _extract_block_fitz(
        self,
        fitz_chars: List[Dict],
        bbox: Tuple[float, float, float, float],
        page_num: int,
        slice_idx: int,
        table_bboxes: List[Tuple[float, float, float, float]] = None
    ) -> Dict[str, Any]:
        """fitz chars からスライス内テキストを抽出（ルビ除外あり）"""
        from collections import Counter
        x0, y0, x1, y1 = bbox
        if table_bboxes is None:
            table_bboxes = []

        chars = [
            ch for ch in fitz_chars
            if ch["x0"] >= x0 and ch["x1"] <= x1 and ch["top"] >= y0 and ch["bottom"] <= y1
        ]
        chars = [
            ch for ch in chars
            if not self._is_inside_any_table(
                (ch["x0"], ch["top"], ch["x1"], ch["bottom"]), table_bboxes
            )
        ]

        # サイズ分布ログ（スライス内の全文字）
        if chars:
            size_dist = dict(sorted(Counter(round(float(ch.get("size") or 0), 1) for ch in chars).items()))
            logger.info(f"[B-61] ページ{page_num+1} slice{slice_idx}: 全文字数={len(chars)} サイズ分布={size_dist}")

        body_chars = []
        ruby_chars = []
        for ch in chars:
            size = float(ch.get("size") or 0)
            if 0 < size <= self.RUBY_SIZE_THRESHOLD:
                ruby_chars.append(ch)
                continue
            body_chars.append(ch)

        logger.info(f"[B-61] ページ{page_num+1} slice{slice_idx}: 本文採用数={len(body_chars)} / ルビ除外数={len(ruby_chars)}")

        has_bold = any("bold" in ch.get("fontname", "").lower() for ch in body_chars)
        text = self._chars_to_text_lines(body_chars)
        logger.info(f"[B-61] ページ{page_num+1} slice{slice_idx}: 本文テキスト=\n{text if text else '（空）'}")

        return {
            "page": page_num,
            "slice": slice_idx,
            "bbox": bbox,
            "text": text,
            "has_red": False,
            "has_bold": has_bold,
            "word_count": len(text.split()) if text else 0,
        }

    def _detect_slices(self, page) -> List[Tuple[float, float, float, float]]:
        page_width = float(page.width)
        page_height = float(page.height)
        chars = page.chars
        if not chars:
            return [(0, 0, page_width, page_height)]
        gaps = self._find_text_free_columns(chars, page_width, page_height)
        if not gaps:
            logger.debug(f"[B-61] 空白列なし → 全体を1スライス")
            return [(0, 0, page_width, page_height)]
        slices = []
        prev_x = 0
        for gap_start, gap_end in gaps:
            if gap_start > prev_x + 5:
                slices.append((prev_x, 0, gap_start, page_height))
            prev_x = gap_end
        if prev_x < page_width - 5:
            slices.append((prev_x, 0, page_width, page_height))
        logger.debug(f"[B-61] 空白列検出: {len(gaps)}箇所 → {len(slices)}スライス")
        return slices if slices else [(0, 0, page_width, page_height)]

    def _find_text_free_columns(
        self,
        chars: List[Dict],
        page_width: float,
        page_height: float
    ) -> List[Tuple[float, float]]:
        occupied = [False] * int(page_width)
        for char in chars:
            x0 = int(char['x0'])
            x1 = int(char['x1'])
            for x in range(max(0, x0), min(len(occupied), x1)):
                occupied[x] = True
        gaps = []
        gap_start = None
        for x in range(len(occupied)):
            if not occupied[x]:
                if gap_start is None:
                    gap_start = x
            else:
                if gap_start is not None:
                    gap_width = x - gap_start
                    if gap_width >= self.GAP_MIN_WIDTH:
                        gaps.append((float(gap_start), float(x)))
                    gap_start = None
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
        """char単位でルビを先に除外して行復元（Word LTSC はルビ使用あり）"""
        x0, y0, x1, y1 = bbox
        cropped = page.within_bbox((x0, y0, x1, y1))

        if table_bboxes is None:
            table_bboxes = []

        chars = [ch for ch in (cropped.chars or []) if ch.get("text", "").strip()]

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
                logger.debug(f"[B-61] ルビ除外: '{ch['text']}' (size={size:.1f}pt)")
                continue
            body_chars.append(ch)

        has_bold = any('bold' in ch.get('fontname', '').lower() for ch in body_chars)
        has_red = False

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
        if not chars:
            return ""
        sorted_chars = sorted(chars, key=lambda c: (c["top"], c["x0"]))
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
        result_lines = []
        for line_chars in lines:
            line_chars_sorted = sorted(line_chars, key=lambda c: c["x0"])
            result_lines.append(self._line_chars_to_text(line_chars_sorted))
        return "\n".join(result_lines)

    def _line_chars_to_text(self, line_chars: List[Dict]) -> str:
        if not line_chars:
            return ""
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
        if not table_bboxes:
            return False
        wx0, wy0, wx1, wy1 = word_bbox
        for table_bbox in table_bboxes:
            tx0, ty0, tx1, ty1 = table_bbox
            if wx0 >= tx0 and wx1 <= tx1 and wy0 >= ty0 and wy1 <= ty1:
                return True
        return False

    def _extract_tables(self, page, page_num: int) -> List[Dict[str, Any]]:
        tables = []
        for idx, table in enumerate(page.find_tables()):
            data = table.extract()
            rows = len(data) if data else 0
            cols = len(data[0]) if data and len(data) > 0 else 0
            logger.info(f"[B-61] Table {idx} (Page {page_num}): {rows}行×{cols}列")
            if data:
                for row_idx, row in enumerate(data):
                    logger.info(f"[B-61]   行{row_idx}: {row}")
            tables.append({
                'page': page_num,
                'index': idx,
                'bbox': table.bbox,
                'data': data,
                'rows': rows,
                'cols': cols,
                'source': 'stage_b'
            })
        return tables

    def _build_tagged_text(self, logical_blocks: List[Dict[str, Any]]) -> str:
        result = []
        for block in logical_blocks:
            header = f"[BLOCK page={block['page']} slice={block['slice']}]"
            footer = "[/BLOCK]"
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

        フェーズ1: 抽出した文字の bbox のみ redaction
        フェーズ2: 残存テキスト検査 → 残ったページだけ追いredact（最大5回）
        フェーズ3: 表罫線削除（structured_tables がある場合のみ）
        """
        try:
            import fitz
        except ImportError:
            logger.error("[B-61] PyMuPDF がインストールされていません")
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

            logger.info(f"[B-61] フェーズ1: 抽出文字を削除 {deleted_chars}文字")

            # フェーズ2: 残存テキスト検査 → 追いredact（最大5回）
            for attempt in range(1, 6):
                remaining_pages = []
                for page_num in range(page_count):
                    page = doc[page_num]
                    if page.get_text("text").strip():
                        remaining_pages.append(page_num)

                if not remaining_pages:
                    break

                logger.info(f"[B-61] フェーズ2 試行{attempt}: 残存テキストあり {remaining_pages}")
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
            purged_pdf_path = purged_dir / f"b61_{file_path.stem}_purged.pdf"
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
                    purged_pdf_path2 = purged_dir / f"b61_{file_path.stem}_purged2.pdf"
                    doc2.save(str(purged_pdf_path2))
                    doc2.close()
                    import os
                    os.replace(str(purged_pdf_path2), str(purged_pdf_path))
                    logger.info(f"[B-61] フェーズ3: 表罫線削除 {deleted_table_graphics}表")
                except Exception as e:
                    logger.warning(f"[B-61] 表罫線削除エラー: {e}")

            logger.info(f"[B-61] purged PDF 保存: {purged_pdf_path.name}")
            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-61] purge エラー: {e}", exc_info=True)
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
            'purged_pdf_path': ''
        }
