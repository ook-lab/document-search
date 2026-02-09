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

                # テキストを生成
                text_with_tags = self._build_text(all_tables)

                # メタ情報
                tags = {
                    'page_count': len(pdf.pages),
                    'table_count': len(all_tables),
                    'is_grid_based': True
                }

                logger.info(f"[B-4] 抽出完了: 表={len(all_tables)}")

                return {
                    'is_structured': True,
                    'text_with_tags': text_with_tags,
                    'logical_blocks': logical_blocks,
                    'structured_tables': all_tables,
                    'tags': tags,
                    'purged_image_path': ''  # TODO: Layer Purge
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

            tables.append({
                'page': page_num,
                'index': idx,
                'rows': row_count,
                'cols': col_count,
                'data': data,
                'bbox': table.bbox
            })

            logger.debug(f"[B-4] 表{idx}: {row_count}行 x {col_count}列")

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
