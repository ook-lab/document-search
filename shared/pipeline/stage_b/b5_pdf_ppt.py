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

    def process(self, file_path: Path) -> Dict[str, Any]:
        """
        PowerPoint由来PDFから構造化データを抽出

        Args:
            file_path: PDFファイルパス

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

                for page_num, page in enumerate(pdf.pages):
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

                # テキストを生成
                text_with_tags = self._build_text(logical_blocks)

                # メタ情報
                tags = {
                    'slide_count': len(logical_blocks),
                    'table_count': len(all_tables),
                    'total_textboxes': sum(b['textbox_count'] for b in logical_blocks)
                }

                logger.info(f"[B-5] 抽出完了: スライド={len(logical_blocks)}, 表={len(all_tables)}")

                return {
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'purged_image_path': ''  # TODO: Layer Purge
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
            tables.append({
                'page': page_num,
                'index': idx,
                'bbox': table.bbox,
                'data': table.extract()
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

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'is_structured': False,
            'error': error_message,
            'text_with_tags': '',
            'logical_blocks': [],
            'structured_tables': [],
            'tags': {},
            'purged_image_path': ''
        }
