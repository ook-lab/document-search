"""
Stage G Gate: 仕分けゲート（表とテキストの物理的分離）

【設計 2026-01-28】G の入り口で「表」と「テキスト」を物理的に分離

役割: Stage E + Stage F の出力を受け取り、
      G1（表専用）と G2（テキスト専用）に振り分ける

============================================
入力:
  - stage_e_result: 物理抽出テキスト（ページ付き）
  - stage_f_payload: 独立読解結果（アンカー付き）
  - post_body: 投稿本文

出力:
  - g1_input: 表データ + 表ページのコンテキスト
  - g2_input: 純粋テキスト + post_body + 表の跡地マーカー

仕分けルール:
  1. 座標重なりチェック: E のテキストが F の表エリア内 → G1
  2. 構造自己主張チェック: E がタブ区切り等を持つ → G1 候補
  3. アンカー自動発行: 抜き取った跡地に目印を埋め込む
============================================
"""
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger
import re


@dataclass
class TableCandidate:
    """表候補データ"""
    anchor_id: str
    page: int
    source: str  # 'stage_e', 'stage_f.f7', 'stage_f.f8', 'merged'
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    title: str = ""
    table_type: str = "unknown"
    bbox: Optional[Dict[str, float]] = None  # 座標情報
    confidence: str = "medium"
    raw_text: str = ""  # E から来た生テキスト（構造化前）


@dataclass
class TextSegment:
    """テキストセグメント"""
    ref_id: str
    page: int
    text: str
    segment_type: str  # 'post_body', 'heading', 'paragraph', 'list_item'
    source: str  # 'post_body', 'stage_e', 'stage_f'
    table_placeholder: Optional[str] = None  # 表があった場所のマーカー


class StageGGate:
    """Gの入り口で表とテキストを物理的に分離する仕分けゲート"""

    # タブ区切りやカンマ区切りを検出するパターン
    TABULAR_PATTERNS = [
        r'\t.*\t',  # タブ区切り
        r'│.*│',    # 罫線（パイプ）
        r'\|.*\|',  # Markdown表
        r'^\s*\d+\.\s+\S+\s+\d+',  # 順位表パターン（例: "1. 山田 100"）
    ]

    def __init__(self):
        self._table_counter = 0
        self._text_counter = 0

    def route(
        self,
        stage_e_result: Dict[str, Any],
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        E + F の出力を G1（表用）と G2（テキスト用）に仕分け

        Args:
            stage_e_result: Stage E の出力
            stage_f_payload: Stage F の出力
            post_body: 投稿本文

        Returns:
            (g1_input, g2_input)
        """
        logger.info("[G-Gate] 仕分け開始...")

        # カウンターリセット
        self._table_counter = 0
        self._text_counter = 0

        # ============================================
        # Step 1: Stage F から表とテキストブロックを抽出
        # ============================================
        f_tables = self._extract_f_tables(stage_f_payload)
        f_text_blocks = self._extract_f_text_blocks(stage_f_payload)
        f_anchors = stage_f_payload.get('anchors', [])

        logger.info(f"[G-Gate] Stage F: tables={len(f_tables)}, text_blocks={len(f_text_blocks)}, anchors={len(f_anchors)}")

        # ============================================
        # Step 2: Stage E からテキストを抽出し、表候補を検出
        # ============================================
        e_content = stage_e_result.get('content', '')
        e_metadata = stage_e_result.get('metadata', {})
        e_table_bboxes = e_metadata.get('table_bboxes', [])

        e_table_candidates, e_text_segments = self._analyze_e_content(
            e_content, e_table_bboxes, f_tables
        )

        logger.info(f"[G-Gate] Stage E: table_candidates={len(e_table_candidates)}, text_segments={len(e_text_segments)}")

        # ============================================
        # Step 3: 座標重なりチェック → 表データをマージ
        # ============================================
        merged_tables = self._merge_tables_by_coordinate(
            e_table_candidates, f_tables, f_anchors
        )

        logger.info(f"[G-Gate] マージ後: tables={len(merged_tables)}")

        # ============================================
        # Step 4: G1 入力を構築（表 + 表ページのコンテキスト）
        # ============================================
        g1_input = self._build_g1_input(merged_tables, e_text_segments, f_text_blocks)

        # ============================================
        # Step 5: G2 入力を構築（テキスト + 表の跡地マーカー）
        # ============================================
        g2_input = self._build_g2_input(
            e_text_segments, f_text_blocks, merged_tables, post_body
        )

        # 統計ログ
        logger.info(f"[G-Gate] 仕分け完了:")
        logger.info(f"  ├─ G1: {len(g1_input.get('tables', []))}表, {len(g1_input.get('table_page_context', {}))}ページコンテキスト")
        logger.info(f"  └─ G2: {len(g2_input.get('segments', []))}セグメント, {g2_input.get('placeholder_count', 0)}プレースホルダー")

        return g1_input, g2_input

    def _extract_f_tables(self, stage_f_payload: Dict[str, Any]) -> List[Dict]:
        """Stage F から表データを抽出"""
        tables = []

        # トップレベルの tables
        for tbl in stage_f_payload.get('tables', []):
            tables.append({
                'source': 'stage_f',
                'block_id': tbl.get('block_id', ''),
                'page': tbl.get('page', tbl.get('chunk_start_page', 0)),
                'title': tbl.get('table_title', ''),
                'table_type': tbl.get('table_type', 'visual_table'),
                'headers': tbl.get('headers', tbl.get('columns', [])),
                'rows': tbl.get('rows', []),
                'bbox': tbl.get('bbox'),
                'row_count': tbl.get('row_count', len(tbl.get('rows', []))),
                'col_count': tbl.get('col_count', len(tbl.get('headers', tbl.get('columns', [])))),
            })

        # アンカーから表を抽出
        for anchor in stage_f_payload.get('anchors', []):
            if anchor.get('type') == 'table':
                # 既に tables に含まれていないかチェック
                anchor_id = anchor.get('anchor_id', '')
                if not any(t.get('block_id') == anchor_id for t in tables):
                    tables.append({
                        'source': 'stage_f.anchor',
                        'block_id': anchor_id,
                        'page': anchor.get('page', 0),
                        'title': anchor.get('title', anchor.get('content', '')[:50]),
                        'table_type': anchor.get('table_type', 'visual_table'),
                        'headers': anchor.get('columns', []),
                        'rows': anchor.get('rows', []),
                        'is_heavy': anchor.get('is_heavy', False),
                        'row_count': anchor.get('row_count', len(anchor.get('rows', []))),
                        'col_count': anchor.get('col_count', len(anchor.get('columns', []))),
                    })

        return tables

    def _extract_f_text_blocks(self, stage_f_payload: Dict[str, Any]) -> List[Dict]:
        """Stage F からテキストブロックを抽出"""
        blocks = []

        for block in stage_f_payload.get('text_blocks', []):
            blocks.append({
                'block_id': block.get('block_id', ''),
                'page': block.get('page', block.get('original_page', 0)),
                'text': block.get('text', ''),
                'block_type': block.get('block_type', 'paragraph'),
                'reading_order': block.get('reading_order', 0),
                'confidence': block.get('confidence', 'medium'),
            })

        # アンカーからテキストを抽出
        for anchor in stage_f_payload.get('anchors', []):
            if anchor.get('type') == 'text':
                anchor_id = anchor.get('anchor_id', '')
                if not any(b.get('block_id') == anchor_id for b in blocks):
                    blocks.append({
                        'block_id': anchor_id,
                        'page': anchor.get('page', 0),
                        'text': anchor.get('content', ''),
                        'block_type': 'paragraph',
                        'reading_order': anchor.get('reading_order', 0),
                    })

        return blocks

    def _analyze_e_content(
        self,
        e_content: str,
        e_table_bboxes: List[Dict],
        f_tables: List[Dict]
    ) -> Tuple[List[TableCandidate], List[TextSegment]]:
        """
        Stage E のコンテンツを分析し、表候補とテキストセグメントに分離

        ルール:
        1. e_table_bboxes に含まれる領域 → 表候補
        2. タブ区切り等の構造を持つテキスト → 表候補
        3. それ以外 → テキストセグメント
        """
        table_candidates = []
        text_segments = []

        if not e_content:
            return table_candidates, text_segments

        # ページ区切りで分割（\f または [Page X] マーカー）
        page_pattern = r'(?:\f|\[Page\s*(\d+)\])'
        pages = re.split(page_pattern, e_content)

        current_page = 0
        for i, part in enumerate(pages):
            if not part:
                continue

            # ページ番号の更新
            if part.isdigit():
                current_page = int(part) - 1  # 0-indexed
                continue

            # 段落に分割
            paragraphs = self._split_paragraphs(part)

            for para in paragraphs:
                para_clean = para.strip()
                if not para_clean:
                    continue

                # 構造自己主張チェック: タブ区切り等を検出
                if self._is_tabular_text(para_clean):
                    self._table_counter += 1
                    anchor_id = f"E_TBL_{self._table_counter:03d}"

                    table_candidates.append(TableCandidate(
                        anchor_id=anchor_id,
                        page=current_page,
                        source='stage_e',
                        raw_text=para_clean,
                        confidence='medium'
                    ))
                    logger.debug(f"[G-Gate] E表候補検出: {anchor_id} (page={current_page})")
                else:
                    self._text_counter += 1
                    ref_id = f"E_TXT_{self._text_counter:03d}"

                    text_segments.append(TextSegment(
                        ref_id=ref_id,
                        page=current_page,
                        text=para_clean,
                        segment_type=self._detect_segment_type(para_clean),
                        source='stage_e'
                    ))

        return table_candidates, text_segments

    def _is_tabular_text(self, text: str) -> bool:
        """テキストが表形式かどうかを判定"""
        # 短すぎるテキストは表ではない
        if len(text) < 20:
            return False

        # 複数行あるかチェック
        lines = text.strip().split('\n')
        if len(lines) < 2:
            return False

        # タブ区切り・罫線パターンをチェック
        for pattern in self.TABULAR_PATTERNS:
            if re.search(pattern, text, re.MULTILINE):
                # 同じパターンが複数行にあれば表の可能性が高い
                matches = re.findall(pattern, text, re.MULTILINE)
                if len(matches) >= 2:
                    return True

        # 列の揃いをチェック（各行の区切り位置が似ている）
        tab_counts = [line.count('\t') for line in lines if line.strip()]
        if tab_counts and len(set(tab_counts)) == 1 and tab_counts[0] >= 2:
            return True

        return False

    def _detect_segment_type(self, text: str) -> str:
        """テキストセグメントの種類を検出"""
        text_stripped = text.strip()

        # 見出しパターン
        if len(text_stripped) < 50 and not text_stripped.endswith('。'):
            if re.match(r'^[■□●○◆◇▶▷★☆【】]', text_stripped):
                return 'heading'
            if re.match(r'^\d+\.\s', text_stripped):
                return 'heading'

        # リストアイテム
        if re.match(r'^[\-\*・]\s', text_stripped):
            return 'list_item'
        if re.match(r'^\(\d+\)\s', text_stripped):
            return 'list_item'

        return 'paragraph'

    def _split_paragraphs(self, text: str) -> List[str]:
        """テキストを段落に分割"""
        # 空行で分割
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _merge_tables_by_coordinate(
        self,
        e_table_candidates: List[TableCandidate],
        f_tables: List[Dict],
        f_anchors: List[Dict]
    ) -> List[Dict]:
        """
        座標重なりチェックにより E と F の表をマージ

        ルール:
        1. 同じページで座標が重なる → マージ（F を優先、E で補完）
        2. F のみ → そのまま採用
        3. E のみ → 構造化を試みて採用
        """
        merged = []
        used_e_indices = set()

        # F の表を基準にマージ
        for f_tbl in f_tables:
            f_page = f_tbl.get('page', 0)
            f_bbox = f_tbl.get('bbox')

            # 同じページの E 表候補を探す
            matching_e = None
            for i, e_cand in enumerate(e_table_candidates):
                if i in used_e_indices:
                    continue
                if e_cand.page == f_page:
                    # 座標チェック（bbox があれば）またはページ一致で採用
                    matching_e = e_cand
                    used_e_indices.add(i)
                    break

            # マージした表を作成
            self._table_counter += 1
            anchor_id = f"TBL_{self._table_counter:03d}"

            merged_table = {
                'anchor_id': anchor_id,
                'page': f_page,
                'title': f_tbl.get('title', ''),
                'table_type': f_tbl.get('table_type', 'visual_table'),
                'headers': f_tbl.get('headers', []),
                'rows': f_tbl.get('rows', []),
                'row_count': f_tbl.get('row_count', 0),
                'col_count': f_tbl.get('col_count', 0),
                'source': 'stage_f' if not matching_e else 'merged',
                'is_heavy': f_tbl.get('is_heavy', False) or f_tbl.get('row_count', 0) >= 20,
            }

            # E のデータで補完
            if matching_e and matching_e.raw_text:
                merged_table['e_raw_text'] = matching_e.raw_text
                merged_table['source'] = 'merged'

            merged.append(merged_table)

        # E のみの表候補を追加
        for i, e_cand in enumerate(e_table_candidates):
            if i in used_e_indices:
                continue

            self._table_counter += 1
            anchor_id = f"TBL_{self._table_counter:03d}"

            # E のテキストから構造を推測
            headers, rows = self._parse_tabular_text(e_cand.raw_text)

            merged.append({
                'anchor_id': anchor_id,
                'page': e_cand.page,
                'title': '',
                'table_type': 'e_detected',
                'headers': headers,
                'rows': rows,
                'row_count': len(rows),
                'col_count': len(headers) if headers else 0,
                'source': 'stage_e',
                'e_raw_text': e_cand.raw_text,
                'is_heavy': len(rows) >= 20,
            })

        # ページ順でソート
        merged.sort(key=lambda x: (x.get('page', 0), x.get('anchor_id', '')))

        return merged

    def _parse_tabular_text(self, text: str) -> Tuple[List[str], List[List[str]]]:
        """タブ区切りテキストから headers と rows を抽出"""
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if not lines:
            return [], []

        # 区切り文字を検出
        delimiter = '\t'
        if '\t' not in text and '|' in text:
            delimiter = '|'

        # 最初の行をヘッダーとして扱う
        headers = [c.strip() for c in lines[0].split(delimiter) if c.strip()]
        rows = []

        for line in lines[1:]:
            cells = [c.strip() for c in line.split(delimiter)]
            if cells and any(c for c in cells):
                rows.append(cells)

        return headers, rows

    def _build_g1_input(
        self,
        merged_tables: List[Dict],
        e_text_segments: List[TextSegment],
        f_text_blocks: List[Dict]
    ) -> Dict[str, Any]:
        """
        G1 入力を構築: 表 + 表ページのコンテキスト

        G1 JSON 構造:
        {
            "tables": [
                {
                    "anchor_id": "TBL_001",
                    "page": 1,
                    "title": "成績一覧",
                    "table_type": "ranking",
                    "headers": ["順位", "氏名", "点数"],
                    "rows": [["1", "山田", "100"], ...],
                    "source": "merged",
                    "is_heavy": true
                }
            ],
            "table_page_context": {
                "page_1": "表の前後のテキスト..."
            }
        }
        """
        # 表があるページを特定
        table_pages = set(t.get('page', 0) for t in merged_tables)

        # 表ページのテキストコンテキストを収集
        table_page_context = {}
        for page in table_pages:
            context_texts = []

            # E のテキストから収集
            for seg in e_text_segments:
                if seg.page == page:
                    context_texts.append(seg.text)

            # F のテキストから収集
            for block in f_text_blocks:
                if block.get('page', 0) == page:
                    context_texts.append(block.get('text', ''))

            if context_texts:
                table_page_context[f"page_{page}"] = '\n'.join(context_texts)

        return {
            'tables': merged_tables,
            'table_page_context': table_page_context,
            'table_count': len(merged_tables),
            'heavy_table_count': sum(1 for t in merged_tables if t.get('is_heavy', False))
        }

    def _build_g2_input(
        self,
        e_text_segments: List[TextSegment],
        f_text_blocks: List[Dict],
        merged_tables: List[Dict],
        post_body: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        G2 入力を構築: テキスト + 表の跡地マーカー

        G2 JSON 構造:
        {
            "segments": [
                {
                    "ref_id": "REF_001",
                    "page": 0,
                    "text": "投稿本文...",
                    "segment_type": "post_body",
                    "source": "post_body",
                    "table_placeholder": null
                },
                {
                    "ref_id": "REF_002",
                    "page": 1,
                    "text": "",
                    "segment_type": "table_marker",
                    "source": "g_gate",
                    "table_placeholder": "[→ TBL_001 参照]"
                }
            ]
        }
        """
        segments = []
        ref_counter = 0
        placeholder_count = 0

        # 表があるページとアンカーのマップ
        table_page_anchors = {}
        for tbl in merged_tables:
            page = tbl.get('page', 0)
            anchor_id = tbl.get('anchor_id', '')
            if page not in table_page_anchors:
                table_page_anchors[page] = []
            table_page_anchors[page].append(anchor_id)

        # 1. post_body を先頭に
        if post_body and post_body.get('text'):
            ref_counter += 1
            segments.append({
                'ref_id': f'REF_{ref_counter:03d}',
                'page': 0,
                'text': post_body['text'],
                'segment_type': 'post_body',
                'source': 'post_body',
                'table_placeholder': None
            })

        # 2. E のテキストセグメントを追加（表ページ以外 or 表の跡地マーカー付き）
        # ページ順でソート
        all_segments = []

        for seg in e_text_segments:
            all_segments.append({
                'page': seg.page,
                'text': seg.text,
                'segment_type': seg.segment_type,
                'source': seg.source,
                'reading_order': 0
            })

        for block in f_text_blocks:
            # 重複チェック（E に同じテキストがあればスキップ）
            block_text = block.get('text', '')
            if any(s['text'] == block_text for s in all_segments):
                continue

            all_segments.append({
                'page': block.get('page', 0),
                'text': block_text,
                'segment_type': block.get('block_type', 'paragraph'),
                'source': 'stage_f',
                'reading_order': block.get('reading_order', 0)
            })

        # ページと読み順でソート
        all_segments.sort(key=lambda x: (x['page'], x['reading_order']))

        # セグメントを追加（表の跡地にはプレースホルダーを挿入）
        inserted_placeholders = set()
        for seg in all_segments:
            page = seg['page']

            # このページに表があり、まだプレースホルダーを挿入していない場合
            if page in table_page_anchors and page not in inserted_placeholders:
                for anchor_id in table_page_anchors[page]:
                    ref_counter += 1
                    placeholder_count += 1
                    segments.append({
                        'ref_id': f'REF_{ref_counter:03d}',
                        'page': page,
                        'text': '',
                        'segment_type': 'table_marker',
                        'source': 'g_gate',
                        'table_placeholder': f'[→ {anchor_id} 参照]'
                    })
                inserted_placeholders.add(page)

            # テキストセグメントを追加
            ref_counter += 1
            segments.append({
                'ref_id': f'REF_{ref_counter:03d}',
                'page': seg['page'],
                'text': seg['text'],
                'segment_type': seg['segment_type'],
                'source': seg['source'],
                'table_placeholder': None
            })

        return {
            'segments': segments,
            'segment_count': len(segments),
            'placeholder_count': placeholder_count,
            'post_body': post_body or {}
        }
