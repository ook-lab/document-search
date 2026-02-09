"""
B-3: PDF-Word Processor（PDF-Word専用）

pdfplumber を使用して、Word由来PDFから構造化データを抽出。
5A/5B並列レイアウトのスライス機能、赤字・ボールドのタグ付け。
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B3PDFWordProcessor:
    """B-3: PDF-Word Processor（PDF-Word専用）"""

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

                for page_num, page in enumerate(pdf.pages):
                    # スライス検出（縦方向の分割線を探す）
                    slices = self._detect_slices(page)

                    # 各スライスからテキストを抽出
                    for slice_idx, slice_bbox in enumerate(slices):
                        block = self._extract_block(page, slice_bbox, page_num, slice_idx)
                        logical_blocks.append(block)

                    # 表を抽出
                    tables = self._extract_tables(page, page_num)
                    all_tables.extend(tables)

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

                logger.info(f"[B-3] 抽出完了: ブロック={len(logical_blocks)}, 表={len(all_tables)}")

                return {
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'purged_image_path': ''  # TODO: Layer Purge
                }

        except Exception as e:
            logger.error(f"[B-3] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _detect_slices(self, page) -> List[Tuple[float, float, float, float]]:
        """
        ページ内の縦方向スライスを検出（5A/5B並列レイアウト対応）

        Args:
            page: pdfplumberのPageオブジェクト

        Returns:
            [(x0, y0, x1, y1), ...] スライスのbboxリスト
        """
        # ページ全体のサイズ
        page_width = float(page.width)
        page_height = float(page.height)

        # 縦線を検出（ページを縦に貫通する線）
        vertical_lines = []
        for line in page.lines:
            # 縦線の判定（高さがページの80%以上）
            line_height = abs(line['bottom'] - line['top'])
            if line_height >= page_height * 0.8:
                vertical_lines.append(line['x0'])

        # 縦線がない場合はページ全体を1つのスライスとする
        if not vertical_lines:
            return [(0, 0, page_width, page_height)]

        # 縦線でページを分割
        vertical_lines = sorted(set(vertical_lines))
        slices = []

        # 最初のスライス（左端〜最初の縦線）
        slices.append((0, 0, vertical_lines[0], page_height))

        # 中間のスライス
        for i in range(len(vertical_lines) - 1):
            slices.append((vertical_lines[i], 0, vertical_lines[i + 1], page_height))

        # 最後のスライス（最後の縦線〜右端）
        slices.append((vertical_lines[-1], 0, page_width, page_height))

        logger.debug(f"[B-3] スライス検出: {len(slices)}個")
        return slices

    def _extract_block(
        self,
        page,
        bbox: Tuple[float, float, float, float],
        page_num: int,
        slice_idx: int
    ) -> Dict[str, Any]:
        """
        指定範囲からテキストブロックを抽出

        Args:
            page: pdfplumberのPageオブジェクト
            bbox: 抽出範囲 (x0, y0, x1, y1)
            page_num: ページ番号
            slice_idx: スライスインデックス

        Returns:
            {
                'page': int,
                'slice': int,
                'bbox': tuple,
                'text': str,
                'has_red': bool,
                'has_bold': bool,
                'chars': [...]
            }
        """
        # 範囲内の文字を抽出
        x0, y0, x1, y1 = bbox
        cropped = page.within_bbox((x0, y0, x1, y1))
        chars = cropped.chars

        # 赤字・ボールドを検出
        has_red = False
        has_bold = False

        for char in chars:
            # フォント名から太字を推定
            fontname = char.get('fontname', '').lower()
            if 'bold' in fontname:
                has_bold = True

            # 色から赤字を推定（RGBまたはstroking_color）
            # TODO: pdfplumberのcolor情報を活用

        # テキストを結合
        text = cropped.extract_text() or ""

        return {
            'page': page_num,
            'slice': slice_idx,
            'bbox': bbox,
            'text': text,
            'has_red': has_red,
            'has_bold': has_bold,
            'chars': chars
        }

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
            tables.append({
                'page': page_num,
                'index': idx,
                'bbox': table.bbox,
                'data': table.extract()
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
