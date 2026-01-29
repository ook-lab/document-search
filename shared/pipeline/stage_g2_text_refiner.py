"""
Stage G2: Text Refiner（テキスト専用整理）- AI研磨対応版

【設計 2026-01-28】テキストの統合・ソート・整形 + AI校閲

役割: G-Gate から受け取ったテキストセグメントを整理し、H2 用 JSON を出力
      AI（Flash-Lite）で断片的なテキストを「一本の完璧な原稿」に繋ぎ直す

============================================
入力（G-Gate から）:
  - segments: テキストセグメントリスト
  - post_body: 投稿本文
  - placeholder_count: 表プレースホルダー数

出力（H2 へ）:
  - segments: 整理済みセグメント（ref_id, page, text, type）
  - unified_text: AI研磨済み統合テキスト
  - dedup_stats: 重複排除統計
  - token_usage: トークン使用量

処理フロー:
  1. ページ順 → 読み順でソート
  2. 重複テキストの排除（信頼度高い方を採用）
  3. REF_ID を再付与
  4. unified_text を構築
  5. AI研磨（常駐）← 投資ポイント
     - OCRのゴミを浄化
     - 文脈を復元
     - アンカー（表参照）を保持
============================================
"""
from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass
from loguru import logger
import re
from difflib import SequenceMatcher

# G2 で使用するモデル
G2_MODEL = "gemini-2.5-flash-lite"


@dataclass
class DedupStats:
    """重複排除統計"""
    total_input: int = 0
    total_output: int = 0
    duplicates_removed: int = 0
    merged_segments: int = 0


class StageG2TextRefiner:
    """G2: テキストの統合・ソート・整形"""

    # 類似度の閾値（これ以上なら重複とみなす）
    SIMILARITY_THRESHOLD = 0.85

    # ソースの優先順位（数字が小さいほど優先）
    SOURCE_PRIORITY = {
        'post_body': 1,
        'stage_f': 2,
        'stage_e': 3,
        'g_gate': 4,
    }

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMクライアント（オプション、複雑なテキスト整形時に使用）
        """
        self.llm = llm_client
        self._token_usage: Dict[str, Any] = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'model': G2_MODEL
        }

    def process(self, g2_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        テキストセグメントを整理・統合

        Args:
            g2_input: G-Gate からの入力
                - segments: テキストセグメントリスト
                - post_body: 投稿本文
                - placeholder_count: 表プレースホルダー数

        Returns:
            H2 用の整形済み JSON
            {
                'segments': [...],
                'unified_text': str,
                'dedup_stats': {...},
                'post_body': {...}
            }
        """
        logger.info("[G2] テキスト整理開始...")

        segments = g2_input.get('segments', [])
        post_body = g2_input.get('post_body', {})
        placeholder_count = g2_input.get('placeholder_count', 0)

        stats = DedupStats(total_input=len(segments))

        if not segments:
            logger.info("[G2] セグメントなし → post_body のみ")
            logger.info(f"[G2] トークン使用量: 0 (セグメントなし)")
            unified_text = post_body.get('text', '') if post_body else ''
            return {
                'segments': [],
                'unified_text': unified_text,
                'dedup_stats': self._stats_to_dict(stats),
                'post_body': post_body or {},
                'token_usage': self._token_usage.copy()
            }

        # ============================================
        # Step 1: ページ順・読み順でソート
        # ============================================
        sorted_segments = self._sort_segments(segments)
        logger.debug(f"[G2] ソート完了: {len(sorted_segments)}セグメント")

        # ============================================
        # Step 2: 重複排除
        # ============================================
        deduped_segments = self._deduplicate_segments(sorted_segments)
        stats.duplicates_removed = len(sorted_segments) - len(deduped_segments)
        logger.info(f"[G2] 重複排除: {stats.duplicates_removed}件削除")

        # ============================================
        # Step 3: REF_ID を再付与
        # ============================================
        renumbered_segments = self._renumber_ref_ids(deduped_segments)
        stats.total_output = len(renumbered_segments)

        # ============================================
        # Step 4: unified_text を構築
        # ============================================
        unified_text = self._build_unified_text(renumbered_segments)

        # ============================================
        # Step 5: AI研磨（全件実行 - 常駐化）
        # ============================================
        # 2026-01-28: 条件判定なし、全てのテキストをAIに通す
        # 理由: OCRのゴミ、文脈の断絶を、H2に渡る前に100%浄化する
        if self.llm and unified_text:  # LLMクライアントがあれば必ず実行
            logger.info(f"[G2] AI研磨実行（常駐）: {len(unified_text)}文字")
            post_body_text = post_body.get('text', '') if post_body else ''
            polished_text = self._polish_text_with_ai(unified_text, post_body_text)
            if polished_text:
                unified_text = polished_text
                logger.info(f"[G2] AI研磨完了: {len(unified_text)}文字")
            else:
                logger.warning("[G2] AI研磨失敗 → 元テキストを維持")

        # トークン使用量をログ出力
        logger.info(f"[G2] トークン使用量: prompt={self._token_usage['prompt_tokens']}, "
                   f"completion={self._token_usage['completion_tokens']}, "
                   f"total={self._token_usage['total_tokens']} (model={self._token_usage['model']})")

        logger.info(f"[G2] 完了: {stats.total_input}→{stats.total_output}セグメント, unified_text={len(unified_text)}文字")

        return {
            'segments': renumbered_segments,
            'unified_text': unified_text,
            'dedup_stats': self._stats_to_dict(stats),
            'post_body': post_body or {},
            'placeholder_count': placeholder_count,
            'token_usage': self._token_usage.copy()
        }

    def _sort_segments(self, segments: List[Dict]) -> List[Dict]:
        """
        セグメントをページ順 → 読み順でソート

        ソート順:
        1. page (昇順)
        2. segment_type (post_body が最初)
        3. reading_order または ref_id (昇順)
        """
        def sort_key(seg):
            page = seg.get('page', 0)

            # segment_type の優先度
            seg_type = seg.get('segment_type', 'paragraph')
            type_order = {
                'post_body': 0,
                'heading': 1,
                'table_marker': 2,
                'paragraph': 3,
                'list_item': 4,
            }.get(seg_type, 5)

            # reading_order または ref_id から順序を取得
            reading_order = seg.get('reading_order', 0)
            if reading_order == 0:
                # ref_id から番号を抽出
                ref_id = seg.get('ref_id', '')
                match = re.search(r'(\d+)', ref_id)
                if match:
                    reading_order = int(match.group(1))

            return (page, type_order, reading_order)

        return sorted(segments, key=sort_key)

    def _deduplicate_segments(self, segments: List[Dict]) -> List[Dict]:
        """
        重複セグメントを排除

        ルール:
        1. 完全一致 → 優先度高いソースを採用
        2. 高類似度 → 長い方を採用
        3. 包含関係 → 長い方を採用
        """
        if not segments:
            return []

        result = []
        seen_texts: Set[str] = set()

        for seg in segments:
            text = seg.get('text', '').strip()

            # 空テキストは table_marker 以外スキップ
            if not text and seg.get('segment_type') != 'table_marker':
                continue

            # table_marker は常に採用
            if seg.get('segment_type') == 'table_marker':
                result.append(seg)
                continue

            # 正規化したテキスト
            normalized = self._normalize_text(text)

            # 完全一致チェック
            if normalized in seen_texts:
                logger.debug(f"[G2] 重複スキップ (完全一致): {text[:50]}...")
                continue

            # 類似度チェック（既存のセグメントと比較）
            is_duplicate = False
            for existing in result:
                existing_text = existing.get('text', '').strip()
                if not existing_text:
                    continue

                similarity = self._calculate_similarity(text, existing_text)
                if similarity >= self.SIMILARITY_THRESHOLD:
                    # 長い方を採用
                    if len(text) > len(existing_text):
                        result.remove(existing)
                        result.append(seg)
                        seen_texts.add(self._normalize_text(existing_text))
                        logger.debug(f"[G2] 類似テキスト置換: {existing_text[:30]}... → {text[:30]}...")
                    else:
                        logger.debug(f"[G2] 重複スキップ (類似): {text[:50]}...")
                    is_duplicate = True
                    break

                # 包含関係チェック
                if text in existing_text or existing_text in text:
                    # 長い方を採用
                    if len(text) > len(existing_text):
                        result.remove(existing)
                        result.append(seg)
                        seen_texts.add(self._normalize_text(existing_text))
                    is_duplicate = True
                    break

            if not is_duplicate:
                result.append(seg)
                seen_texts.add(normalized)

        return result

    def _normalize_text(self, text: str) -> str:
        """テキストを正規化（比較用）"""
        # 空白を統一
        normalized = re.sub(r'\s+', ' ', text.strip().lower())
        # 句読点を除去
        normalized = re.sub(r'[、。，．,\.]+', '', normalized)
        return normalized

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """2つのテキストの類似度を計算（0.0〜1.0）"""
        if not text1 or not text2:
            return 0.0

        # 短いテキストは厳しく判定
        if len(text1) < 20 or len(text2) < 20:
            return 1.0 if text1 == text2 else 0.0

        return SequenceMatcher(None, text1, text2).ratio()

    def _renumber_ref_ids(self, segments: List[Dict]) -> List[Dict]:
        """REF_ID を連番で再付与"""
        result = []
        ref_counter = 0

        for seg in segments:
            ref_counter += 1
            new_seg = seg.copy()
            new_seg['ref_id'] = f'REF_{ref_counter:03d}'
            result.append(new_seg)

        return result

    def _build_unified_text(self, segments: List[Dict]) -> str:
        """
        統合テキストを構築

        フォーマット:
        - post_body: そのまま
        - heading: 【見出し】形式
        - table_marker: プレースホルダーをそのまま挿入
        - paragraph/list_item: そのまま
        """
        parts = []
        current_page = -1

        for seg in segments:
            page = seg.get('page', 0)
            seg_type = seg.get('segment_type', 'paragraph')
            text = seg.get('text', '').strip()
            placeholder = seg.get('table_placeholder')

            # ページ区切り（オプション）
            if page != current_page and current_page >= 0:
                # parts.append(f"\n--- Page {page + 1} ---\n")
                pass
            current_page = page

            # セグメントタイプに応じたフォーマット
            if seg_type == 'table_marker' and placeholder:
                parts.append(f"\n{placeholder}\n")
            elif seg_type == 'heading':
                parts.append(f"\n【{text}】\n")
            elif seg_type == 'post_body':
                parts.append(text)
            elif seg_type == 'list_item':
                parts.append(f"・{text}")
            else:
                parts.append(text)

        # 結合して整形
        unified = '\n\n'.join(p for p in parts if p.strip())

        # 連続する改行を整理
        unified = re.sub(r'\n{3,}', '\n\n', unified)

        return unified.strip()

    def _stats_to_dict(self, stats: DedupStats) -> Dict[str, Any]:
        """統計を辞書に変換"""
        return {
            'total_input': stats.total_input,
            'total_output': stats.total_output,
            'duplicates_removed': stats.duplicates_removed,
            'merged_segments': stats.merged_segments,
            'dedup_rate': f"{(stats.duplicates_removed / max(stats.total_input, 1)) * 100:.1f}%"
        }

    # ============================================
    # 追加ヘルパー: E と F のテキストマージ
    # ============================================
    def merge_e_f_texts(
        self,
        e_text: str,
        f_text: str,
        post_body_text: str = ""
    ) -> str:
        """
        E と F のテキストをマージ（重複排除付き）

        Args:
            e_text: Stage E の抽出テキスト
            f_text: Stage F の抽出テキスト
            post_body_text: 投稿本文

        Returns:
            マージ済みテキスト
        """
        segments = []
        ref_counter = 0

        # post_body
        if post_body_text:
            ref_counter += 1
            segments.append({
                'ref_id': f'REF_{ref_counter:03d}',
                'page': 0,
                'text': post_body_text,
                'segment_type': 'post_body',
                'source': 'post_body'
            })

        # E テキストを段落分割
        e_paragraphs = self._split_paragraphs(e_text)
        for para in e_paragraphs:
            ref_counter += 1
            segments.append({
                'ref_id': f'REF_{ref_counter:03d}',
                'page': 0,
                'text': para,
                'segment_type': 'paragraph',
                'source': 'stage_e'
            })

        # F テキストを段落分割
        f_paragraphs = self._split_paragraphs(f_text)
        for para in f_paragraphs:
            ref_counter += 1
            segments.append({
                'ref_id': f'REF_{ref_counter:03d}',
                'page': 0,
                'text': para,
                'segment_type': 'paragraph',
                'source': 'stage_f'
            })

        # 重複排除
        deduped = self._deduplicate_segments(segments)

        # 統合テキスト構築
        return self._build_unified_text(deduped)

    def _split_paragraphs(self, text: str) -> List[str]:
        """テキストを段落に分割"""
        if not text:
            return []

        # 空行で分割
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]

    # ============================================
    # クロスバリデーション: E vs F の一致度計算
    # ============================================
    def cross_validate(
        self,
        e_segments: List[Dict],
        f_segments: List[Dict]
    ) -> Dict[str, Any]:
        """
        E と F のセグメントをクロスバリデーション

        Returns:
            {
                'matched_count': int,  # 一致したセグメント数
                'e_only_count': int,   # E にのみあるセグメント数
                'f_only_count': int,   # F にのみあるセグメント数
                'match_rate': float,   # 一致率
                'confidence': str      # 'high', 'medium', 'low'
            }
        """
        e_texts = {self._normalize_text(s.get('text', '')) for s in e_segments if s.get('text')}
        f_texts = {self._normalize_text(s.get('text', '')) for s in f_segments if s.get('text')}

        matched = e_texts & f_texts
        e_only = e_texts - f_texts
        f_only = f_texts - e_texts

        total = len(e_texts | f_texts)
        match_rate = len(matched) / max(total, 1)

        # 信頼度判定
        if match_rate >= 0.7:
            confidence = 'high'
        elif match_rate >= 0.4:
            confidence = 'medium'
        else:
            confidence = 'low'

        return {
            'matched_count': len(matched),
            'e_only_count': len(e_only),
            'f_only_count': len(f_only),
            'match_rate': match_rate,
            'confidence': confidence
        }

    # ============================================
    # AI研磨機能（2026-01-28 追加）
    # 連鎖研磨（スライディング・ウィンドウ）対応
    # ============================================

    # 連鎖研磨のパラメータ
    CHUNK_SIZE = 6000      # 1チャンクあたりの文字数
    OVERLAP_SIZE = 500     # チャンク間の重複（文脈バッファ）

    def _polish_text_with_ai(
        self,
        unified_text: str,
        post_body_text: str = ""
    ) -> Optional[str]:
        """
        AIでテキストを研磨（知能化）- 連鎖研磨対応

        断片的なテキストを「一本の完璧な原稿」に繋ぎ直す。
        長文の場合はスライディング・ウィンドウ方式で分割処理。

        Args:
            unified_text: ルールベースで統合されたテキスト
            post_body_text: 投稿本文（背景情報）

        Returns:
            研磨後のテキスト（失敗時はNone）
        """
        if not self.llm:
            return None

        try:
            # 短いテキストは単発処理
            if len(unified_text) <= self.CHUNK_SIZE:
                return self._polish_single_chunk(unified_text, post_body_text)

            # 長文は連鎖研磨
            logger.info(f"[G2] 連鎖研磨開始: {len(unified_text)}文字 → チャンク分割")
            return self._polish_chained(unified_text, post_body_text)

        except Exception as e:
            logger.error(f"[G2] AI研磨エラー: {e}")
            return None

    def _polish_single_chunk(
        self,
        text: str,
        post_body_text: str = "",
        context_prefix: str = ""
    ) -> Optional[str]:
        """
        単一チャンクを研磨

        Args:
            text: 研磨対象テキスト
            post_body_text: 投稿本文
            context_prefix: 前チャンクからの文脈（連鎖研磨時）

        Returns:
            研磨後テキスト
        """
        prompt = self._build_text_polish_prompt(text, post_body_text, context_prefix)

        logger.debug(f"[G2] チャンク研磨: {len(text)}文字")
        response = self.llm.call_model(
            tier="default",
            prompt=prompt,
            model_name=G2_MODEL,
            temperature=0.0
        )

        # トークン使用量を記録
        if hasattr(self.llm, 'last_usage') and self.llm.last_usage:
            usage = self.llm.last_usage
            self._token_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
            self._token_usage['completion_tokens'] += usage.get('completion_tokens', 0)
            self._token_usage['total_tokens'] += usage.get('total_tokens', 0)

        if not response.get('success'):
            logger.warning(f"[G2] チャンク研磨失敗: {response.get('error')}")
            return None

        polished = response.get('content', '').strip()

        # アンカー保持チェック
        original_anchors = set(re.findall(r'\[→\s*TBL_\d+\s*参照\]', text))
        polished_anchors = set(re.findall(r'\[→\s*TBL_\d+\s*参照\]', polished))

        if original_anchors and not original_anchors.issubset(polished_anchors):
            missing = original_anchors - polished_anchors
            logger.warning(f"[G2] アンカー欠落検出: {missing}")
            # 欠落したアンカーを末尾に追加して救済
            for anchor in missing:
                polished += f"\n{anchor}"

        return polished

    def _polish_chained(
        self,
        unified_text: str,
        post_body_text: str
    ) -> Optional[str]:
        """
        連鎖研磨（スライディング・ウィンドウ方式）

        長文を分割し、前チャンクの末尾を次チャンクの文脈として渡す。
        情報を1ミリも捨てずに全文を研磨する。

        Args:
            unified_text: 長文テキスト
            post_body_text: 投稿本文

        Returns:
            連結された研磨済みテキスト
        """
        # チャンク分割
        chunks = self._split_into_chunks(unified_text)
        logger.info(f"[G2] 連鎖研磨: {len(chunks)}チャンクに分割")

        polished_chunks = []
        context_prefix = ""

        for i, chunk in enumerate(chunks):
            logger.info(f"[G2] チャンク {i+1}/{len(chunks)} 研磨中...")

            # 最初のチャンクのみpost_bodyを渡す
            pb = post_body_text if i == 0 else ""

            polished = self._polish_single_chunk(chunk, pb, context_prefix)

            if polished:
                polished_chunks.append(polished)
                # 次のチャンクへの文脈バッファ
                context_prefix = polished[-self.OVERLAP_SIZE:] if len(polished) > self.OVERLAP_SIZE else polished
            else:
                # 研磨失敗時は元のチャンクを使用
                logger.warning(f"[G2] チャンク {i+1} 研磨失敗 → 元テキスト維持")
                polished_chunks.append(chunk)
                context_prefix = chunk[-self.OVERLAP_SIZE:] if len(chunk) > self.OVERLAP_SIZE else chunk

        # チャンクを結合（重複部分を除去）
        result = self._merge_polished_chunks(polished_chunks)

        logger.info(f"[G2] 連鎖研磨完了: {len(unified_text)}→{len(result)}文字, "
                   f"トークン計={self._token_usage['total_tokens']}")

        return result

    def _split_into_chunks(self, text: str) -> List[str]:
        """
        テキストをチャンクに分割

        段落境界で分割し、アンカーを壊さないように配慮。
        """
        chunks = []
        current_pos = 0
        text_len = len(text)

        while current_pos < text_len:
            # チャンク終了位置
            end_pos = min(current_pos + self.CHUNK_SIZE, text_len)

            if end_pos < text_len:
                # 段落境界を探す（チャンクサイズの80%〜100%の範囲で）
                search_start = current_pos + int(self.CHUNK_SIZE * 0.8)
                search_text = text[search_start:end_pos]

                # 段落区切り（空行）を探す
                para_break = search_text.rfind('\n\n')
                if para_break != -1:
                    end_pos = search_start + para_break + 2
                else:
                    # 単一改行を探す
                    line_break = search_text.rfind('\n')
                    if line_break != -1:
                        end_pos = search_start + line_break + 1

            chunk = text[current_pos:end_pos].strip()
            if chunk:
                chunks.append(chunk)

            current_pos = end_pos

        return chunks

    def _merge_polished_chunks(self, chunks: List[str]) -> str:
        """
        研磨済みチャンクを結合

        重複部分を検出して除去し、滑らかに接続。
        """
        if not chunks:
            return ""
        if len(chunks) == 1:
            return chunks[0]

        result = chunks[0]

        for i in range(1, len(chunks)):
            next_chunk = chunks[i]

            # 重複部分を検出（result末尾とnext_chunk冒頭の一致を探す）
            overlap_found = False
            for overlap_len in range(min(self.OVERLAP_SIZE, len(result), len(next_chunk)), 50, -10):
                if result[-overlap_len:] == next_chunk[:overlap_len]:
                    # 重複部分を除去して結合
                    result = result + next_chunk[overlap_len:]
                    overlap_found = True
                    break

            if not overlap_found:
                # 重複が見つからない場合は段落区切りで結合
                result = result + "\n\n" + next_chunk

        return result

    def _build_text_polish_prompt(
        self,
        unified_text: str,
        post_body_text: str = "",
        context_prefix: str = ""
    ) -> str:
        """
        AI研磨用プロンプトを構築

        キラープロンプト: 校閲記者としての絶対命令
        連鎖研磨時は前チャンクの文脈を受け取る

        Args:
            unified_text: 研磨対象テキスト
            post_body_text: 投稿本文（最初のチャンクのみ）
            context_prefix: 前チャンクからの文脈（連鎖研磨時）
        """
        # 連鎖研磨の文脈セクション
        context_section = ""
        if context_prefix:
            context_section = f"""【前セクションからの文脈】
以下は直前のセクションの末尾です。この続きとして自然に繋がるように校閲してください。
```
...{context_prefix}
```

"""

        prompt = f"""あなたは超一流の校閲記者です。
以下の断片的なテキストを、論理的に繋がる一本の文章に校閲・修復してください。

【絶対の掟】
1. **アンカーは聖域**
   `[→ TBL_001 参照]` などの表参照マーカーは情報の座標です。
   **1文字も変えず**、文脈上適切な位置に必ず残してください。

2. **OCRのゴミを浄化**
   読み間違い（「は」が「1」になる等）や不自然な改行を、
   文脈から推測して自然な日本語に修正してください。

3. **情報の完全維持**
   余計な要約・圧縮はしないでください。
   原文の情報を**最大限活かして**浄化するだけです。
   削除していいのはOCRのゴミのみ。

4. **post_body との接続**
   投稿本文（背景情報）と読み取り内容を、
   矛盾なく滑らかに接続してください。

{f'''【投稿本文（背景情報）】
{post_body_text[:500]}
''' if post_body_text else ''}{context_section}【校閲対象テキスト】
```
{unified_text}
```

【出力】
校閲・修復後のテキストのみを出力してください。
説明や注釈は一切不要です。
"""
        return prompt
