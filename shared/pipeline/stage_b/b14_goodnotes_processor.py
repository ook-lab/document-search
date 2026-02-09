"""
B-14: Goodnotes Processor（Goodnotes PDF専用）

Goodnotes由来のPDFから、デジタル入力テキストを抽出し、
手書きが存在する領域を特定する。

目的:
1. デジタルテキスト（テキストツール入力）の完全抽出
2. 手書き領域（Annots）の特定
3. Stage E での手書き読み取りのためのヒント生成
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import pdfplumber


class B14GoodnotesProcessor:
    """B-14: Goodnotes Processor（Goodnotes PDF専用）"""

    def __init__(self):
        """Goodnotes Processor 初期化"""
        pass

    def process(
        self,
        file_path: Path
    ) -> Dict[str, Any]:
        """
        Goodnotes PDFを処理

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'is_structured': bool,
                'data_type': str,
                'digital_texts': list,      # デジタル入力テキスト
                'handwritten_zones': list,  # 手書き領域
                'logical_blocks': list,     # 論理ブロック
                'tags': dict
            }
        """
        logger.info(f"[B-14] Goodnotes処理開始: {file_path.name}")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                digital_texts = []
                handwritten_zones = []
                logical_blocks = []

                for page_num, page in enumerate(pdf.pages):
                    logger.info(f"[B-14] ページ {page_num + 1} を処理中")

                    # デジタルテキストを抽出
                    page_digital_texts = self._extract_digital_text(page, page_num)
                    digital_texts.extend(page_digital_texts)

                    # 手書き領域を特定
                    page_handwritten_zones = self._detect_handwritten_zones(page, page_num)
                    handwritten_zones.extend(page_handwritten_zones)

                    # 論理ブロックを生成
                    page_blocks = self._create_logical_blocks(
                        page_digital_texts,
                        page_num,
                        page.width,
                        page.height
                    )
                    logical_blocks.extend(page_blocks)

                logger.info(f"[B-14] 処理完了:")
                logger.info(f"  ├─ デジタルテキスト: {len(digital_texts)}個")
                logger.info(f"  ├─ 手書き領域: {len(handwritten_zones)}個")
                logger.info(f"  └─ 論理ブロック: {len(logical_blocks)}個")

                return {
                    'is_structured': True,
                    'data_type': 'goodnotes',
                    'digital_texts': digital_texts,
                    'handwritten_zones': handwritten_zones,
                    'logical_blocks': logical_blocks,
                    'tags': {
                        'source': 'goodnotes',
                        'has_handwriting': len(handwritten_zones) > 0,
                        'digital_text_count': len(digital_texts)
                    }
                }

        except Exception as e:
            logger.error(f"[B-14] 処理エラー: {e}", exc_info=True)
            return {
                'is_structured': False,
                'error': str(e),
                'data_type': 'goodnotes'
            }

    def _extract_digital_text(
        self,
        page,
        page_num: int
    ) -> List[Dict[str, Any]]:
        """
        デジタル入力テキストを抽出

        Args:
            page: pdfplumber page object
            page_num: ページ番号

        Returns:
            デジタルテキストリスト
        """
        digital_texts = []

        # テキスト抽出（文字単位）
        chars = page.chars if hasattr(page, 'chars') else []

        for char in chars:
            # Goodnotes のテキストツールで入力された文字を抽出
            # fontname や size で判別可能な場合も
            digital_texts.append({
                'page': page_num,
                'text': char.get('text', ''),
                'bbox': [
                    char.get('x0', 0),
                    char.get('top', 0),
                    char.get('x1', 0),
                    char.get('bottom', 0)
                ],
                'fontname': char.get('fontname', ''),
                'size': char.get('size', 0)
            })

        return digital_texts

    def _detect_handwritten_zones(
        self,
        page,
        page_num: int
    ) -> List[Dict[str, Any]]:
        """
        手書き領域を特定

        Args:
            page: pdfplumber page object
            page_num: ページ番号

        Returns:
            手書き領域リスト
        """
        handwritten_zones = []

        # PDF の Annots（注釈）を確認
        # Goodnotes の手書きは Annots レイヤーに存在する
        try:
            if hasattr(page, 'annots'):
                for annot in page.annots:
                    # Ink Annotation（手書きストローク）を特定
                    if annot.get('Subtype') == '/Ink':
                        # バウンディングボックスを取得
                        rect = annot.get('Rect')
                        if rect:
                            handwritten_zones.append({
                                'page': page_num,
                                'bbox': rect,
                                'type': 'handwritten',
                                'subtype': 'ink'
                            })

            # Lines/Curves の密集度から手書きエリアを推定（補完的手法）
            lines = page.lines if hasattr(page, 'lines') else []
            curves = page.curves if hasattr(page, 'curves') else []

            # 線が密集しているエリアを手書きとして扱う
            # TODO: より高度なクラスタリング実装
            if len(lines) > 100 or len(curves) > 100:
                # 簡易的に全体を1つのゾーンとする
                handwritten_zones.append({
                    'page': page_num,
                    'bbox': [0, 0, page.width, page.height],
                    'type': 'handwritten',
                    'subtype': 'dense_strokes',
                    'confidence': 'low'
                })

        except Exception as e:
            logger.warning(f"[B-14] 手書き領域検出エラー: {e}")

        return handwritten_zones

    def _create_logical_blocks(
        self,
        digital_texts: List[Dict[str, Any]],
        page_num: int,
        page_width: float,
        page_height: float
    ) -> List[Dict[str, Any]]:
        """
        デジタルテキストから論理ブロックを生成

        Args:
            digital_texts: デジタルテキストリスト
            page_num: ページ番号
            page_width: ページ幅
            page_height: ページ高さ

        Returns:
            論理ブロックリスト
        """
        if not digital_texts:
            return []

        # テキストを結合
        combined_text = ''.join([t.get('text', '') for t in digital_texts])

        # バウンディングボックスを計算
        if digital_texts:
            all_x0 = [t['bbox'][0] for t in digital_texts]
            all_y0 = [t['bbox'][1] for t in digital_texts]
            all_x1 = [t['bbox'][2] for t in digital_texts]
            all_y1 = [t['bbox'][3] for t in digital_texts]

            bbox = [
                min(all_x0),
                min(all_y0),
                max(all_x1),
                max(all_y1)
            ]
        else:
            bbox = [0, 0, page_width, page_height]

        # 正規化座標
        bbox_normalized = [
            bbox[0] / page_width,
            bbox[1] / page_height,
            bbox[2] / page_width,
            bbox[3] / page_height
        ]

        return [{
            'page': page_num,
            'text': combined_text,
            'bbox': bbox,
            'bbox_normalized': bbox_normalized,
            'type': 'digital_text'
        }]
