"""
座標マッチャー（B+Cハイブリッド方式）

E1（単語レベル座標）と E5（ブロックレベル座標）を組み合わせて、
Gemini が抽出したテキスト要素に正確な bbox を付与する。

処理フロー:
1. E5 ブロックで粗い位置特定（テキストを含むブロックを検索）
2. ブロック内の E1 単語で精密マッチング
3. マッチした単語の bbox を統合して要素の bbox を計算
"""

from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import re


class CoordinateMatcher:
    """座標マッチャー（B+Cハイブリッド）"""

    def __init__(self):
        """初期化"""
        pass

    def match_text_to_bbox(
        self,
        text: str,
        words: List[Dict[str, Any]],
        blocks: Optional[List[Dict[str, Any]]] = None,
        page: int = 0
    ) -> Dict[str, Any]:
        """
        テキストに対応する bbox を計算

        Args:
            text: マッチング対象のテキスト
            words: E1 の単語リスト [{'text': str, 'bbox': [x0,y0,x1,y1], 'conf': float}]
            blocks: E5 のブロックリスト [{'block_id': int, 'bbox': [x0,y0,x1,y1]}]（オプション）
            page: ページ番号

        Returns:
            {
                'bbox': [x0, y0, x1, y1],
                'page': int,
                'matched_words': [str],
                'confidence': float
            }
        """
        if not text or not words:
            return self._empty_bbox(page)

        # テキストを正規化（空白・記号を除去）
        normalized_text = self._normalize_text(text)

        # Step 1: E5 ブロックで粗い位置特定（あれば）
        candidate_words = words
        if blocks:
            candidate_words = self._filter_by_blocks(text, words, blocks)
            if candidate_words:
                logger.debug(f"[CoordinateMatcher] ブロックフィルタ: {len(words)} → {len(candidate_words)}語")

        # Step 2: 単語レベルで精密マッチング
        matched_words = self._match_words(normalized_text, candidate_words)

        if not matched_words:
            logger.warning(f"[CoordinateMatcher] マッチなし: '{text[:30]}...'")
            return self._empty_bbox(page)

        # Step 3: マッチした単語の bbox を統合
        bbox = self._compute_union_bbox(matched_words)

        # 信頼度を計算（マッチした単語の平均信頼度）
        confidence = sum(w['conf'] for w in matched_words) / len(matched_words) / 100.0

        logger.debug(f"[CoordinateMatcher] マッチ成功: '{text[:30]}...' → bbox={bbox}")

        return {
            'bbox': bbox,
            'page': page,
            'matched_words': [w['text'] for w in matched_words],
            'confidence': confidence
        }

    def enrich_elements(
        self,
        elements: List[Dict[str, Any]],
        words: List[Dict[str, Any]],
        blocks: Optional[List[Dict[str, Any]]] = None,
        page: int = 0,
        text_key: str = 'content'
    ) -> List[Dict[str, Any]]:
        """
        要素リストに bbox を一括付与

        Args:
            elements: 要素リスト（Gemini の出力）
            words: E1 の単語リスト
            blocks: E5 のブロックリスト（オプション）
            page: ページ番号
            text_key: テキストが格納されているキー名

        Returns:
            bbox が付与された要素リスト
        """
        enriched = []

        for elem in elements:
            # テキストを取得（複数のキー候補を試す）
            text = elem.get(text_key) or elem.get('text') or elem.get('event') or elem.get('item') or ''

            if not text:
                logger.warning(f"[CoordinateMatcher] テキストなし: {elem}")
                elem['bbox'] = [0, 0, 0, 0]
                elem['page'] = page
                enriched.append(elem)
                continue

            # bbox をマッチング
            match_result = self.match_text_to_bbox(text, words, blocks, page)

            # 要素に bbox と page を追加
            elem['bbox'] = match_result['bbox']
            elem['page'] = match_result['page']
            elem['_match_confidence'] = match_result.get('confidence', 0.0)

            enriched.append(elem)

        return enriched

    def _normalize_text(self, text: str) -> str:
        """テキストを正規化（マッチング用）"""
        # 空白・改行・記号を除去
        normalized = re.sub(r'[\s\u3000]+', '', text)  # 空白・全角スペース
        normalized = re.sub(r'[、。，．！？!?・]', '', normalized)  # 句読点
        return normalized

    def _filter_by_blocks(
        self,
        text: str,
        words: List[Dict[str, Any]],
        blocks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        E5 ブロックを使って候補単語をフィルタリング

        テキストを含む可能性が高いブロック内の単語のみを返す
        """
        # テキストの一部を含むブロックを探す
        text_tokens = set(self._normalize_text(text)[:20])  # 最初の20文字

        matching_blocks = []
        for block in blocks:
            # ブロック内の単語を結合
            block_bbox = block['bbox']
            block_words = [w for w in words if self._is_inside_bbox(w['bbox'], block_bbox)]
            block_text = ''.join([w['text'] for w in block_words])

            # テキストトークンとの一致度を計算
            block_tokens = set(self._normalize_text(block_text)[:20])
            overlap = len(text_tokens & block_tokens)

            if overlap > 0:
                matching_blocks.append((overlap, block))

        if not matching_blocks:
            return words  # ブロックが見つからない場合は全単語を返す

        # 最も一致度が高いブロック内の単語を返す
        best_block = max(matching_blocks, key=lambda x: x[0])[1]
        return [w for w in words if self._is_inside_bbox(w['bbox'], best_block['bbox'])]

    def _match_words(
        self,
        normalized_text: str,
        words: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        正規化されたテキストに対して単語をマッチング

        部分文字列マッチングで柔軟に対応
        """
        matched = []

        for word in words:
            word_text = self._normalize_text(word['text'])
            if not word_text:
                continue

            # 部分文字列マッチング
            if word_text in normalized_text:
                matched.append(word)

        return matched

    def _compute_union_bbox(self, words: List[Dict[str, Any]]) -> List[float]:
        """
        複数の単語の bbox を統合（Union）

        Returns:
            [x0, y0, x1, y1]
        """
        if not words:
            return [0, 0, 0, 0]

        x0 = min(w['bbox'][0] for w in words)
        y0 = min(w['bbox'][1] for w in words)
        x1 = max(w['bbox'][2] for w in words)
        y1 = max(w['bbox'][3] for w in words)

        return [x0, y0, x1, y1]

    def _is_inside_bbox(
        self,
        word_bbox: List[float],
        block_bbox: List[float]
    ) -> bool:
        """単語が ブロック内にあるか判定"""
        wx0, wy0, wx1, wy1 = word_bbox
        bx0, by0, bx1, by1 = block_bbox

        # 中心点がブロック内にあるか
        cx = (wx0 + wx1) / 2
        cy = (wy0 + wy1) / 2

        return bx0 <= cx <= bx1 and by0 <= cy <= by1

    def _empty_bbox(self, page: int) -> Dict[str, Any]:
        """空の bbox を返す"""
        return {
            'bbox': [0, 0, 0, 0],
            'page': page,
            'matched_words': [],
            'confidence': 0.0
        }
