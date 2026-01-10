"""
PDF プロセッサ (総力戦アーキテクチャ: pdfplumber + Table + Vision)

設計書: COMPLETE_IMPLEMENTATION_GUIDE_v3.md の 1.4節に基づき、PDFファイルからテキストを抽出する。
v3.0: 「テキスト解析（pdfplumber）」で基礎を固め、「表構造解析」で論理を通し、
      最後に「Vision」で視覚情報を補完する総力戦アーキテクチャ。
"""
from typing import Dict, Any, List, Optional
from pathlib import Path
import os
import tempfile
import hashlib
from loguru import logger

import pdfplumber

# pdf2image は poppler が必要（macOS: brew install poppler）
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False
    logger.warning("pdf2image が利用できません。Gemini Vision補完機能は無効化されます。")


class PDFProcessor:
    """PDFファイルからテキストを抽出するプロセッサ（総力戦方式）"""

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMClient インスタンス（Vision補完用）
        """
        self.llm_client = llm_client
        logger.info("PDFプロセッサ初期化完了 (総力戦アーキテクチャ)")

    def extract_text(self, file_path: str) -> Dict[str, Any]:
        """
        PDFファイルからテキストを抽出（pdfplumber + OCR分離方式）

        処理フロー:
        Layer 1: pdfplumber でテキスト + 表を抽出（正確）
        Layer 2: 画像があるページで OCR 実行（下読み・文字拾い徹底）
        Layer 3: 2つを別々に返す（統合しない）

        Args:
            file_path: PDFファイルのローカルパス

        Returns:
            抽出結果 (pdfplumber_text, ocr_text, metadata, success)
        """
        file_path = Path(file_path)

        if not file_path.exists():
            logger.error(f"ファイルが見つかりません: {file_path}")
            return {
                "content": "",
                "metadata": {"error": "File not found"},
                "success": False,
                "error_message": "File not found"
            }

        if file_path.suffix.lower() not in ['.pdf']:
            logger.warning(f"PDFファイルではありません: {file_path}")
            return {
                "content": "",
                "metadata": {"error": "Not a PDF file"},
                "success": False,
                "error_message": "Not a PDF file"
            }

        try:
            # ============================================
            # Layer 1: pdfplumber でテキスト + 表を抽出
            # ============================================
            pdfplumber_result = self._extract_with_pdfplumber(file_path)

            if not pdfplumber_result["success"]:
                logger.warning(f"pdfplumber完全失敗: {file_path.name} → E-4で全面補完を実行します")
                # pdfplumberが失敗してもE-4（Gemini Vision）で全面補完する
                # 空のpage_textsを用意してE-4に進む
                import fitz  # PyMuPDF
                pdf_doc = fitz.open(file_path)
                page_count = len(pdf_doc)
                pdf_doc.close()

                page_texts = [""] * page_count  # 全ページ空テキスト
                page_images = [True] * page_count  # 全ページ画像あり扱い
                metadata = {"total_pages": page_count, "total_tables": 0, "pdfplumber_failed": True}
            else:
                page_texts = pdfplumber_result["page_texts"]
                page_images = pdfplumber_result["page_images"]
                metadata = pdfplumber_result["metadata"]

            total_tables = metadata.get('total_tables', 0)
            logger.info(f"pdfplumber抽出完了（E-1~E-3）: {len(page_texts)} ページ, {total_tables} 表")

            # ============================================
            # Layer 2: Vision戦略の判定（E-4の準備）
            # ============================================
            vision_target_pages = self._detect_vision_target_pages(page_texts, page_images)

            logger.info(f"Vision差分検出対象: {len(vision_target_pages)}/{len(page_texts)} ページ")

            # ============================================
            # E-4: Gemini Vision 差分検出（対象ページのみ）
            # ============================================
            vision_corrections = {}

            if vision_target_pages and self.llm_client and PDF2IMAGE_AVAILABLE:
                logger.info(f"[E-4] Gemini Vision差分検出開始: {len(vision_target_pages)} ページ")
                vision_corrections = self._extract_with_gemini_vision(
                    file_path,
                    vision_target_pages,
                    page_texts
                )
            elif vision_target_pages and not PDF2IMAGE_AVAILABLE:
                logger.warning("[E-4] pdf2image が利用できないため、Vision差分検出をスキップします")
            elif vision_target_pages and not self.llm_client:
                logger.warning("[E-4] LLMClient が未指定のため、Vision差分検出をスキップします")

            # ============================================
            # E-5: VisionのOCR結果を適用
            # ============================================

            # E-3の統合Markdown文字数（適用前）
            e3_total_chars = sum(len(text) for text in page_texts)
            logger.info(f"[E-5] Vision OCR結果適用開始")
            logger.info(f"  ├─ E-3統合Markdown: {e3_total_chars}文字")

            # E-4の完全OCR結果を補完
            # - E-3がゼロの場合：E-4をそのまま使用（全体補完）
            # - E-3が非空の場合：E-3にE-4を追加（部分補完）
            vision_total = 0
            for i in vision_corrections:
                e4_markdown = vision_corrections[i].strip()
                if e4_markdown:
                    e3_chars = len(page_texts[i])
                    e4_chars = len(e4_markdown)

                    if e3_chars == 0:
                        # 全体補完：E-4をそのまま使用
                        page_texts[i] = e4_markdown
                        logger.info(f"  ├─ ページ{i+1}: E-4を使用 (全体補完: {e4_chars}文字)")
                    else:
                        # 部分補完：E-3にE-4を追加
                        page_texts[i] = page_texts[i] + f"\n\n---\n\n## Vision OCR 補完情報\n\n{e4_markdown}"
                        final_chars = len(page_texts[i])
                        logger.info(f"  ├─ ページ{i+1}: E-3にE-4を追加 (部分補完: E-3 {e3_chars}文字 + E-4 {e4_chars}文字 = {final_chars}文字)")

                    vision_total += e4_chars

            # E-5適用後の文字数
            e5_total_chars = sum(len(text) for text in page_texts)

            logger.info(f"[E-5] Vision OCR結果適用完了")
            logger.info(f"  ├─ Vision OCR適用ページ数: {len(vision_corrections)}ページ")
            logger.info(f"  └─ E-5最終Markdown: {e5_total_chars}文字")

            # ============================================
            # 最終統合: 全ページをMarkdown文書に統合
            # ============================================
            complete_parts = []
            for i, text in enumerate(page_texts):
                page_num = i + 1
                complete_parts.append(f"\n\n---\n\n# Page {page_num}\n\n")
                complete_parts.append(text)

            complete_text = "".join(complete_parts)

            # Stage E 完了ログ
            logger.info(f"[Stage E完了] PDF抽出結果:")
            logger.info(f"  ├─ E-1~E-3: {e3_total_chars}文字")
            logger.info(f"  ├─ E-4: Vision差分検出 {len(vision_corrections)}ページ")
            logger.info(f"  ├─ E-5: Vision修正適用 {vision_total}文字")
            logger.info(f"  └─ 最終Markdown文書: {len(complete_text)}文字")

            # メタデータ更新
            metadata['vision_corrected'] = len(vision_corrections) > 0
            metadata['vision_pages'] = len(vision_corrections)
            metadata['pdfplumber_model'] = 'pdfplumber'
            metadata['vision_model'] = 'gemini-2.5-flash' if vision_corrections else None
            metadata['e3_chars'] = e3_total_chars
            metadata['vision_correction_chars'] = vision_total
            metadata['e5_chars'] = e5_total_chars
            metadata['total_chars'] = len(complete_text)

            return {
                "content": complete_text,
                "metadata": metadata,
                "success": True
            }

        except Exception as e:
            logger.error(f"PDFテキスト抽出エラー ({file_path}): {e}")
            import traceback
            traceback.print_exc()
            return {
                "content": "",
                "metadata": {"error": str(e)},
                "success": False,
                "error_message": str(e)
            }

    def _extract_with_pdfplumber(self, file_path: Path) -> Dict[str, Any]:
        """
        pdfplumber でテキスト + 表を抽出

        Returns:
            {
                "success": bool,
                "page_texts": List[str],
                "page_tables": List[List[str]],  # ページごとのMarkdown表リスト
                "metadata": dict
            }
        """
        try:
            with pdfplumber.open(file_path) as pdf:
                num_pages = len(pdf.pages)
                page_texts = []
                page_images = []  # 各ページの画像有無
                all_stats = []  # 全ページの統計情報

                for i, page in enumerate(pdf.pages):
                    page_num = i + 1

                    # 画像検出
                    images = page.images
                    page_images.append(len(images) > 0)  # 画像があればTrue

                    # E-1からE-3: 位置情報を使った統合Markdown生成
                    unified_markdown, stats = self._merge_with_position(page, page_num)
                    page_texts.append(unified_markdown)
                    all_stats.append(stats)

                # 統計情報をメタデータに追加
                total_tables = sum(s['num_tables'] for s in all_stats)
                total_unified_chars = sum(s['unified_chars'] for s in all_stats)

                metadata = {
                    'num_pages': num_pages,
                    'extractor': 'pdfplumber',
                    'total_tables': total_tables,
                    'total_unified_chars': total_unified_chars,
                    'per_page_stats': all_stats
                }

                # 全ページが空テキストの場合
                if not any(page_texts):
                    logger.warning(f"pdfplumber: テキストを抽出できませんでした ({file_path})")
                    return {
                        "success": False,
                        "page_texts": [],
                        "page_images": [],
                        "metadata": metadata,
                        "error_message": "No text extracted"
                    }

                return {
                    "success": True,
                    "page_texts": page_texts,
                    "page_images": page_images,
                    "metadata": metadata
                }

        except Exception as e:
            logger.error(f"pdfplumber抽出エラー: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "page_texts": [],
                "page_images": [],
                "metadata": {"error": str(e)},
                "error_message": str(e)
            }

    def _table_to_markdown(self, table: List[List]) -> str:
        """
        pdfplumberの表データをMarkdown形式に変換

        Args:
            table: pdfplumberのextract_tables()が返す2次元リスト

        Returns:
            Markdown形式の表文字列
        """
        if not table:
            return ""

        # None を空文字列に変換
        cleaned_table = []
        for row in table:
            cleaned_row = [str(cell).strip() if cell is not None else "" for cell in row]
            cleaned_table.append(cleaned_row)

        if not cleaned_table:
            return ""

        # Markdownテーブル生成
        md_lines = []

        # ヘッダー行
        header = cleaned_table[0]
        md_lines.append("| " + " | ".join(header) + " |")

        # セパレーター
        md_lines.append("| " + " | ".join(["---"] * len(header)) + " |")

        # データ行
        for row in cleaned_table[1:]:
            # ヘッダーと列数を合わせる
            if len(row) < len(header):
                row.extend([""] * (len(header) - len(row)))
            elif len(row) > len(header):
                row = row[:len(header)]

            md_lines.append("| " + " | ".join(row) + " |")

        return "\n".join(md_lines)

    def _convert_to_markdown(self, text: str) -> str:
        """
        プレーンテキストをMarkdown形式に変換

        Args:
            text: プレーンテキスト

        Returns:
            Markdown形式のテキスト
        """
        if not text or not text.strip():
            return ""

        lines = text.split('\n')
        markdown_lines = []

        for line in lines:
            stripped = line.strip()

            # 空行はそのまま
            if not stripped:
                markdown_lines.append("")
                continue

            # 箇条書き（・で始まる行）を Markdown の - に変換
            if stripped.startswith('・'):
                markdown_line = "- " + stripped[1:].strip()
                markdown_lines.append(markdown_line)

            # 短い行（20文字以下）で全て大文字または数字のみ → 見出しとして扱う
            elif len(stripped) <= 20 and (stripped.isupper() or stripped.isdigit()):
                markdown_lines.append(f"## {stripped}")

            # その他はそのまま
            else:
                markdown_lines.append(stripped)

        return "\n".join(markdown_lines)

    def _is_inside_bbox(self, word: Dict, bbox: tuple) -> bool:
        """
        単語が表の範囲（bbox）内にあるか判定

        Args:
            word: pdfplumberのextract_words()が返す単語辞書 {'x0', 'x1', 'top', 'bottom', 'text'}
            bbox: 表の範囲 (x0, top, x1, bottom)

        Returns:
            True if 単語が表の範囲内にある
        """
        wx0, wx1 = word['x0'], word['x1']
        wtop, wbottom = word['top'], word['bottom']
        bx0, btop, bx1, bbottom = bbox

        # 単語の中心点が表の範囲内にあるか判定
        word_center_x = (wx0 + wx1) / 2
        word_center_y = (wtop + wbottom) / 2

        return (bx0 <= word_center_x <= bx1) and (btop <= word_center_y <= bbottom)

    def _merge_with_position(self, page, page_num: int) -> tuple:
        """
        位置情報を使ってテキストと表を正しい順序で統合（重複削除）

        E-1: テキスト抽出 (Markdown形式)
        E-2: 表抽出 (Markdown形式)
        E-3: 位置情報で統合 + 重複削除

        Args:
            page: pdfplumberのPageオブジェクト
            page_num: ページ番号（ログ用）

        Returns:
            (unified_markdown: str, stats: dict) 統合されたMarkdownと統計情報
        """
        # E-1: テキスト抽出
        words = page.extract_words()
        raw_text_chars = sum(len(w['text']) for w in words)
        logger.info(f"[E-1] ページ{page_num} テキスト抽出:")
        logger.info(f"  ├─ 生テキスト: {raw_text_chars}文字 ({len(words)}単語)")

        # E-2: 表抽出
        tables = page.find_tables()
        logger.info(f"[E-2] ページ{page_num} 表抽出:")
        logger.info(f"  ├─ 表の数: {len(tables)}個")

        # 表のMarkdown変換
        table_elements = []
        table_markdown_total = 0
        for table in tables:
            table_data = table.extract()
            table_md = self._table_to_markdown(table_data)
            if table_md:
                # table.bbox = (x0, top, x1, bottom)
                table_elements.append({
                    'type': 'table',
                    'y': table.bbox[1],  # top (上端) でソート
                    'content': table_md,
                    'bbox': table.bbox
                })
                table_markdown_total += len(table_md)

        logger.info(f"  └─ Markdown変換後: {table_markdown_total}文字")

        # E-3: 統合（重複削除 + 順序保持）
        logger.info(f"[E-3] ページ{page_num} 統合:")

        # 表の範囲内にある単語を除外（重複削除）
        words_outside_tables = []
        table_bboxes = [te['bbox'] for te in table_elements]

        for word in words:
            is_inside = False
            for bbox in table_bboxes:
                if self._is_inside_bbox(word, bbox):
                    is_inside = True
                    break
            if not is_inside:
                words_outside_tables.append(word)

        # テキスト要素を行ごとにグループ化（y座標が近い単語をまとめる）
        text_lines = self._group_words_into_lines(words_outside_tables)
        text_elements = []
        for line in text_lines:
            text_elements.append({
                'type': 'text',
                'y': line['top'],  # top (上端) でソート
                'content': line['text']
            })

        # テキストと表を統合
        all_elements = text_elements + table_elements

        # y座標でソート（上から下: top が小さい → 大きい）
        all_elements.sort(key=lambda e: e['y'])

        # Markdown統合
        markdown_parts = []
        for elem in all_elements:
            if elem['type'] == 'text':
                markdown_text = self._convert_to_markdown(elem['content'])
                if markdown_text.strip():
                    markdown_parts.append(markdown_text)
            else:  # table
                markdown_parts.append(elem['content'])

        unified_markdown = "\n\n".join(markdown_parts)

        # 統計情報
        removed_text_chars = raw_text_chars - sum(len(e['content']) for e in text_elements if e['type'] == 'text')

        logger.info(f"  ├─ 統合前（テキスト + 表）: {raw_text_chars + table_markdown_total}文字")
        logger.info(f"  ├─ 重複除去（表内テキスト）: -{removed_text_chars}文字")
        logger.info(f"  └─ 統合後: {len(unified_markdown)}文字")

        stats = {
            'raw_text_chars': raw_text_chars,
            'table_markdown_chars': table_markdown_total,
            'removed_chars': removed_text_chars,
            'unified_chars': len(unified_markdown),
            'num_tables': len(tables)
        }

        return unified_markdown, stats

    def _group_words_into_lines(self, words: List[Dict]) -> List[Dict]:
        """
        単語をy座標でグループ化して行にまとめる

        Args:
            words: pdfplumberのextract_words()が返す単語リスト

        Returns:
            行のリスト [{'top', 'bottom', 'text'}, ...]
        """
        if not words:
            return []

        # y座標でソート（上から下: top が小さい → 大きい）
        sorted_words = sorted(words, key=lambda w: (w['top'], w['x0']))

        lines = []
        current_line = None
        y_tolerance = 3  # y座標の許容範囲（ピクセル）

        for word in sorted_words:
            if current_line is None:
                # 新しい行を開始
                current_line = {
                    'top': word['top'],
                    'bottom': word['bottom'],
                    'words': [word]
                }
            elif abs(word['top'] - current_line['top']) <= y_tolerance:
                # 同じ行に追加
                current_line['words'].append(word)
                current_line['bottom'] = max(current_line['bottom'], word['bottom'])
            else:
                # 現在の行を確定して新しい行を開始
                current_line['text'] = " ".join(w['text'] for w in current_line['words'])
                lines.append(current_line)
                current_line = {
                    'top': word['top'],
                    'bottom': word['bottom'],
                    'words': [word]
                }

        # 最後の行を追加
        if current_line:
            current_line['text'] = " ".join(w['text'] for w in current_line['words'])
            lines.append(current_line)

        return lines

    def _detect_vision_target_pages(
        self,
        page_texts: List[str],
        page_images: List[bool]
    ) -> List[int]:
        """
        Vision差分検出が必要なページを検出

        判定基準:
        画像が埋め込まれている可能性があるPDFは全てVision対象
        一番大事な情報が画像化されている場合があるため、
        文字数に関わらず画像があれば処理する

        Args:
            page_texts: 各ページのテキストリスト（E-3統合後）
            page_images: 各ページの画像有無リスト（True=画像あり）

        Returns:
            Vision差分検出対象のページ番号リスト（0-indexed）
        """
        target_pages = []

        for i, has_image in enumerate(page_images):
            # 画像が埋め込まれているページは全てVision処理
            if has_image:
                target_pages.append(i)
                logger.debug(f"ページ {i+1}: 画像検出 → Vision差分検出")

        return target_pages

    def _extract_with_gemini_vision(
        self,
        pdf_path: Path,
        page_numbers: List[int],
        page_texts: List[str]
    ) -> Dict[int, str]:
        """
        指定されたページをGemini Visionで解析（E-4: 差分検出型）

        Args:
            pdf_path: PDFファイルパス
            page_numbers: 解析対象のページ番号リスト（0-indexed）
            page_texts: 各ページの E-3統合Markdown テキスト

        Returns:
            {ページ番号: Vision修正情報} の辞書
        """
        vision_results = {}

        # PDFを画像に変換
        try:
            logger.info(f"PDF→画像変換開始: {pdf_path}")
            images = convert_from_path(
                pdf_path,
                dpi=200,  # 表組み認識に十分な解像度
                fmt='png'
            )

            # 該当ページのみ処理
            for page_num in page_numbers:
                if page_num >= len(images):
                    logger.warning(f"ページ {page_num} は範囲外です")
                    continue

                image = images[page_num]

                # 一時ファイルに保存
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                    tmp_path = Path(tmp_file.name)
                    image.save(tmp_path, 'PNG')

                try:
                    # このページの E-3統合Markdown を取得
                    e3_markdown = page_texts[page_num] if page_num < len(page_texts) else ""
                    e3_chars = len(e3_markdown)

                    # E-4: 常に完全OCRモード（画像から完全なMarkdownを生成）
                    prompt = """この画像を詳細に見て、全ての文字と構造を正確にMarkdown形式で再現してください。

【あなたの役割】
画像から全ての文字を漏らさず拾い、**文書の構造も正確に再現**してください。

【文字拾いの徹底指示】
- **小さな文字**: 注釈、脚注、コピーライト表記なども全て拾う
- **ロゴ化された文字**: 画像として埋め込まれたタイトル、会社名、ブランド名なども全て読み取る
- **装飾された文字**: 太字、斜体、色付きなど、装飾に関わらず全て拾う
- **背景に埋もれた文字**: 薄い色、透かし文字なども可能な限り読み取る
- **手書き文字**: 判読可能な範囲で全て拾う
- **表構造**: 表がある場合は、セルの位置関係を正確に再現
- **文字色・装飾**: 重要な情報（赤字、太字など）は注釈として記載

【出力形式（重要）】
画像内の全てのテキストを **Markdown形式** で出力してください：

1. **通常のテキスト**: そのまま記述
2. **見出し**: `##` を使用
3. **箇条書き**: `・` または `- ` を使用（画像に応じて）
4. **表**: Markdown table形式で出力
   ```
   | ヘッダー1 | ヘッダー2 | ヘッダー3 |
   | --- | --- | --- |
   | セル1 | セル2 | セル3 |
   ```
5. **段落**: 空行で区切る

**重要**:
- 表がある場合は必ずMarkdown table形式で出力してください
- セルの結合は空白セルで表現してください
- 画像を見たまま、上から下、左から右の順に正確に再現してください
- 1文字も見逃さないでください"""

                    # Gemini Vision で解析（E-4）
                    logger.info(f"[E-4] Vision差分検出: ページ {page_num + 1} (E-3: {e3_chars}文字)")
                    result = self.llm_client.transcribe_image(
                        image_path=tmp_path,
                        model="gemini-2.5-flash",
                        prompt=prompt
                    )

                    if result.get("success"):
                        content = result.get("content", "")
                        vision_chars = len(content)
                        vision_results[page_num] = content
                        logger.info(f"[E-4] Vision差分検出成功: ページ {page_num + 1}")
                        logger.info(f"  ├─ E-3統合Markdown: {e3_chars}文字")
                        logger.info(f"  └─ Vision差分情報: {vision_chars}文字")
                    else:
                        logger.warning(f"[E-4] Vision差分検出失敗: ページ {page_num + 1}, エラー: {result.get('error')}")

                finally:
                    # 一時ファイル削除
                    if tmp_path.exists():
                        os.unlink(tmp_path)

            return vision_results

        except Exception as e:
            logger.error(f"[E-4] Gemini Vision差分検出エラー: {e}")
            import traceback
            traceback.print_exc()
            return {}


def calculate_content_hash(pdf_path: str) -> str:
    """
    PDFファイルの内容全体からSHA256ハッシュを計算する

    Args:
        pdf_path: PDFファイルのローカルパス

    Returns:
        SHA256ハッシュ値（16進数文字列）

    Raises:
        FileNotFoundError: ファイルが存在しない場合
        IOError: ファイル読み込みエラー
    """
    file_path = Path(pdf_path)

    if not file_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {pdf_path}")

    # SHA256ハッシュオブジェクトを作成
    sha256_hash = hashlib.sha256()

    # ファイルをバイナリモードで読み込み、チャンクごとにハッシュを更新
    # 大きなファイルでもメモリ効率的に処理
    with open(file_path, 'rb') as f:
        # 64KBずつ読み込む
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)

    # 16進数文字列として返す
    return sha256_hash.hexdigest()
