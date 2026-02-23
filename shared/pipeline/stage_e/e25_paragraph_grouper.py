"""
E-25: Paragraph Grouper（段落グループ化）

Stage Bの単語レベルデータとStage Dの表領域情報を使用して、
表外のテキストを段落ごとにグループ化する。

目的:
1. Stage Bの単語データ（type='word'）を取得
2. Stage Dの表領域bboxを使用して表外の単語のみを抽出
3. Y座標ベースで段落にグループ化
4. bbox付きの段落ブロックを生成
"""

from typing import Dict, Any, List, Optional
from loguru import logger


class E25ParagraphGrouper:
    """E-25: Paragraph Grouper（段落グループ化）"""

    def __init__(
        self,
        y_tolerance: float = 5.0,
        paragraph_gap: float = 15.0
    ):
        """
        Paragraph Grouper 初期化

        Args:
            y_tolerance: 同じ行と見なすY座標の許容差（pt）
            paragraph_gap: 段落の区切りと見なす行間の最小値（pt）
        """
        self.y_tolerance = y_tolerance
        self.paragraph_gap = paragraph_gap

    def group(
        self,
        stage_b_result: Dict[str, Any],
        stage_d_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        単語を段落にグループ化

        Args:
            stage_b_result: Stage Bの結果（logical_blocks含む）
            stage_d_result: Stage Dの結果（tables含む）

        Returns:
            {
                'success': bool,
                'paragraphs': [
                    {
                        'page': int,
                        'type': 'paragraph',
                        'text': str,
                        'bbox': [x0, y0, x1, y1]
                    }
                ]
            }
        """
        logger.info("[E-25] ========================================")
        logger.info("[E-25] 段落グループ化開始")
        logger.info("[E-25] ========================================")

        try:
            # Stage Bからテキストブロックを取得
            # type='text_block': PyMuPDFのテキストボックス単位
            # type='word': 旧実装（pdfplumberの単語単位）
            logical_blocks = stage_b_result.get('logical_blocks', [])
            text_blocks = [
                block for block in logical_blocks
                if block.get('type') in ('text_block', 'word')
            ]

            logger.info(f"[E-25] Stage B テキストブロック数: {len(text_blocks)}")

            # Stage Dから表領域を取得
            tables = []
            d_results = stage_d_result if isinstance(stage_d_result, list) else [stage_d_result]
            for d_result in d_results:
                tables.extend(d_result.get('tables', []))

            table_bboxes = [table.get('bbox') for table in tables if table.get('bbox')]
            logger.info(f"[E-25] Stage D 表領域数: {len(table_bboxes)}")

            # 表外のテキストブロックを抽出
            non_table_blocks = []
            for block in text_blocks:
                block_bbox = block.get('bbox')
                if not block_bbox:
                    continue

                # 表領域外かチェック
                if not self._is_inside_any_table(block_bbox, table_bboxes):
                    non_table_blocks.append(block)

            logger.info(f"[E-25] 表外テキストブロック数: {len(non_table_blocks)}")

            if not non_table_blocks:
                logger.info("[E-25] 表外テキストブロックが見つかりません")
                return {
                    'success': True,
                    'paragraphs': []
                }

            # type='text_block'の場合、すでにテキストボックス単位なので
            # グループ化せずにそのまま段落として扱う
            all_paragraphs = []
            for block in non_table_blocks:
                # typeを'paragraph'に統一
                paragraph = {
                    'page': block.get('page', 0),
                    'type': 'paragraph',
                    'text': block.get('text', ''),
                    'bbox': block.get('bbox', [0, 0, 0, 0])
                }
                all_paragraphs.append(paragraph)

            logger.info(f"[E-25] 段落化完了: {len(all_paragraphs)}段落")
            for para_idx, para in enumerate(all_paragraphs):
                logger.info(f"[E-25]   段落{para_idx}: {para.get('text')}")

            logger.info("")
            logger.info(f"[E-25] 段落グループ化完了: {len(all_paragraphs)}段落")
            logger.info("[E-25] ========================================")

            return {
                'success': True,
                'paragraphs': all_paragraphs
            }

        except Exception as e:
            logger.error(f"[E-25] 段落グループ化エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'paragraphs': []
            }

    def _is_inside_any_table(
        self,
        word_bbox: List[float],
        table_bboxes: List[List[float]]
    ) -> bool:
        """
        単語が表領域内にあるかチェック

        Args:
            word_bbox: 単語のbbox [x0, y0, x1, y1]
            table_bboxes: 表のbboxリスト [[x0, y0, x1, y1], ...]

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

    def _group_words_into_paragraphs(
        self,
        words: List[Dict[str, Any]],
        page: int
    ) -> List[Dict[str, Any]]:
        """
        単語を段落単位でグループ化

        Args:
            words: 単語リスト
            page: ページ番号

        Returns:
            段落ブロックリスト
        """
        if not words:
            return []

        # Y座標でソート
        words_sorted = sorted(words, key=lambda w: (w['bbox'][1], w['bbox'][0]))

        # 行にグループ化
        lines = []
        current_line = []
        last_y = None

        for word in words_sorted:
            bbox = word['bbox']
            y = bbox[1]  # top

            if last_y is None or abs(y - last_y) <= self.y_tolerance:
                # 同じ行
                current_line.append(word)
                last_y = y
            else:
                # 新しい行
                if current_line:
                    lines.append(current_line)
                current_line = [word]
                last_y = y

        # 最後の行を追加
        if current_line:
            lines.append(current_line)

        # 段落にグループ化
        paragraphs = []
        current_paragraph = []
        last_line_bottom = None

        for line in lines:
            line_top = min(w['bbox'][1] for w in line)
            line_bottom = max(w['bbox'][3] for w in line)

            if last_line_bottom is None or (line_top - last_line_bottom) <= self.paragraph_gap:
                # 同じ段落
                current_paragraph.extend(line)
                last_line_bottom = line_bottom
            else:
                # 新しい段落
                if current_paragraph:
                    paragraphs.append(current_paragraph)
                current_paragraph = line
                last_line_bottom = line_bottom

        # 最後の段落を追加
        if current_paragraph:
            paragraphs.append(current_paragraph)

        # 各段落のブロックを作成
        blocks = []
        for para_words in paragraphs:
            # テキストを結合（X座標順にソート）
            para_words_sorted = sorted(para_words, key=lambda w: (w['bbox'][1], w['bbox'][0]))
            text = ' '.join([w['text'] for w in para_words_sorted])

            # bboxを計算
            x0 = min(w['bbox'][0] for w in para_words)
            y0 = min(w['bbox'][1] for w in para_words)
            x1 = max(w['bbox'][2] for w in para_words)
            y1 = max(w['bbox'][3] for w in para_words)

            blocks.append({
                'page': page,
                'type': 'paragraph',
                'text': text,
                'bbox': [x0, y0, x1, y1]
            })

        return blocks
