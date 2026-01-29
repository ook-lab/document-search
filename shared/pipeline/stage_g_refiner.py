"""
Stage G: Integration Refiner (統合精錬) - v2.0

【設計 2026-01-28】G-Gate + G1 + G2 による物理的分離

役割: Stage E（物理抽出）と Stage F（独立読解）の結果を統合し、
      G1（表専用）と G2（テキスト専用）で整理してから H1/H2 へ渡す

============================================
新アーキテクチャ:

[Stage E] + [Stage F]
         ↓
    [G-Gate] ←─── 仕分けゲート（表とテキストを物理的に分離）
         ↓
   ┌─────┴─────┐
   ↓           ↓
 [G1]        [G2]
 表整理      テキスト整理
   ↓           ↓
 [H1]        [H2]

入力:
  - stage_e_result: 物理抽出テキスト
  - stage_f_payload: 独立読解結果（アンカー付き）
  - post_body: 投稿本文

出力:
  - g1_result: 表データ（検証済み）→ H1 へ
  - g2_result: テキストセグメント（重複排除済み）→ H2 へ
  - unified_text: 統合テキスト（後方互換）
  - source_inventory: REF_ID付きセグメント（後方互換）
  - table_inventory: TBL_ID付き表（後方互換）
============================================
"""
import json
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger

from shared.ai.llm_client.llm_client import LLMClient
from .constants import STAGE_H_INPUT_SCHEMA_VERSION, G_MODEL
from .stage_g_gate import StageGGate
from .stage_g1_table_refiner import StageG1TableRefiner
from .stage_g2_text_refiner import StageG2TextRefiner


class StageGRefiner:
    """Stage G: 統合精錬（G-Gate + G1 + G2）"""

    def __init__(self, llm_client: LLMClient):
        """
        Args:
            llm_client: LLMクライアント
        """
        self.llm = llm_client
        self._g_usage: Dict[str, Any] = {}

        # 新しいサブモジュールを初期化（LLMクライアントを渡す）
        self.gate = StageGGate()
        self.g1 = StageG1TableRefiner(llm_client=llm_client)
        self.g2 = StageG2TextRefiner(llm_client=llm_client)

    def process(
        self,
        stage_e_result: Optional[Dict[str, Any]] = None,
        stage_f_payload: Optional[Dict[str, Any]] = None,
        post_body: Optional[Dict[str, Any]] = None,
        model: str = "gemini-2.0-flash-lite",
        workspace: str = "default"
    ) -> Dict[str, Any]:
        """
        Stage E と Stage F の結果を統合（v2.0: G-Gate + G1 + G2）

        Args:
            stage_e_result: Stage E の出力
            stage_f_payload: Stage F の出力
            post_body: 投稿本文
            model: 使用するモデル（未使用、後方互換のため残す）
            workspace: ワークスペース

        Returns:
            {
                'unified_text': str,
                'source_inventory': List[Dict],
                'table_inventory': List[Dict],
                'cross_validation': Dict,
                'ref_count': int,
                'warnings': List[str],
                'g1_result': Dict,  # H1 用
                'g2_result': Dict,  # H2 用
                'processing_mode': str
            }
        """
        logger.info(f"[Stage G] 統合精錬開始（v2.0: G-Gate + G1 + G2）")

        # 入力のデフォルト値
        stage_e_result = stage_e_result or {}
        stage_f_payload = stage_f_payload or {}

        # 入力データ取得
        e_content = stage_e_result.get('content', '')
        e_method = stage_e_result.get('method', 'unknown')
        f_processing_mode = stage_f_payload.get('processing_mode', 'unknown')

        logger.info(f"[Stage G] 入力:")
        logger.info(f"  ├─ Stage E: {len(e_content)}文字 (method={e_method})")
        logger.info(f"  ├─ Stage F: tables={len(stage_f_payload.get('tables', []))}, anchors={len(stage_f_payload.get('anchors', []))}")
        logger.info(f"  └─ F processing_mode: {f_processing_mode}")

        # ============================================
        # 特殊ケースの処理（従来ロジックを維持）
        # ============================================

        # 添付なし（E, F 両方スキップ）の場合
        if not e_content and f_processing_mode == 'skipped':
            logger.info("[Stage G] 添付なし → post_body のみで処理")
            return self._process_post_body_only(post_body)

        # Stage F がスキップされた場合（ドキュメントのみ）
        if f_processing_mode == 'skipped':
            logger.info("[Stage G] Stage F スキップ → Stage E のみで処理")
            return self._process_e_only(stage_e_result, post_body)

        # 音声/動画の場合（Transcription のみ）
        if f_processing_mode == 'transcription_only':
            logger.info("[Stage G] Transcription モード → F-7 結果を使用")
            return self._process_transcription(stage_f_payload, post_body)

        # ============================================
        # 通常処理: G-Gate → G1 → G2
        # ============================================
        logger.info("[Stage G] 通常モード → G-Gate + G1 + G2")

        try:
            # Step 1: G-Gate で仕分け
            g1_input, g2_input = self.gate.route(
                stage_e_result=stage_e_result,
                stage_f_payload=stage_f_payload,
                post_body=post_body
            )

            # Step 2: G1 で表を整理
            g1_result = self.g1.process(g1_input)

            # Step 3: G2 でテキストを整理
            g2_result = self.g2.process(g2_input)

            # Step 4: 後方互換のための出力を構築
            return self._build_unified_result(g1_result, g2_result, post_body)

        except Exception as e:
            logger.warning(f"[Stage G] G-Gate/G1/G2 処理失敗、フォールバック: {e}")
            # フォールバック: 従来のルールベース処理
            return self._legacy_rule_based_merge(stage_e_result, stage_f_payload, post_body)

    def _build_unified_result(
        self,
        g1_result: Dict[str, Any],
        g2_result: Dict[str, Any],
        post_body: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        G1 と G2 の結果を統合し、後方互換の出力形式を構築

        Args:
            g1_result: G1（表整理）の出力
            g2_result: G2（テキスト整理）の出力
            post_body: 投稿本文

        Returns:
            後方互換の Stage G 出力
        """
        # source_inventory: G2 のセグメントから構築
        source_inventory = []
        for seg in g2_result.get('segments', []):
            # table_marker はスキップ（table_inventory に含まれる）
            if seg.get('segment_type') == 'table_marker':
                continue

            source_inventory.append({
                'ref_id': seg.get('ref_id', ''),
                'text': seg.get('text', ''),
                'type': seg.get('segment_type', 'paragraph'),
                'source': seg.get('source', 'unknown'),
                'page': seg.get('page', 0),
                'confidence': 'high' if seg.get('source') == 'post_body' else 'medium'
            })

        # table_inventory: G1 の表から構築
        table_inventory = []
        for tbl in g1_result.get('tables', []):
            table_inventory.append({
                'ref_id': tbl.get('anchor_id', ''),
                'table_title': tbl.get('title', ''),
                'table_type': tbl.get('table_type', 'visual_table'),
                'headers': tbl.get('headers', []),
                'rows': tbl.get('rows', []),
                'row_count': tbl.get('row_count', 0),
                'col_count': tbl.get('col_count', 0),
                'page': tbl.get('page', 0),
                'source': tbl.get('source', 'unknown'),
                'is_heavy': tbl.get('is_heavy', False),
                'is_valid': tbl.get('is_valid', True)
            })

        # unified_text: G2 の出力を使用
        unified_text = g2_result.get('unified_text', '')

        # cross_validation: 統計情報を構築
        g1_stats = g1_result.get('statistics', {})
        g2_stats = g2_result.get('dedup_stats', {})

        cross_validation = {
            'mode': 'g_gate_v2',
            'table_count': g1_stats.get('total_tables', 0),
            'valid_tables': g1_stats.get('valid_tables', 0),
            'total_table_rows': g1_stats.get('total_rows', 0),
            'text_segments': g2_stats.get('total_output', 0),
            'duplicates_removed': g2_stats.get('duplicates_removed', 0),
            'dedup_rate': g2_stats.get('dedup_rate', '0%')
        }

        # 警告を収集
        warnings = []
        for val in g1_result.get('validation_results', []):
            for warn in val.get('warnings', []):
                warnings.append(f"G1_{val.get('anchor_id', '')}: {warn}")
            for err in val.get('errors', []):
                warnings.append(f"G1_ERROR_{val.get('anchor_id', '')}: {err}")

        # トークン使用量を集計
        g1_tokens = g1_result.get('token_usage', {})
        g2_tokens = g2_result.get('token_usage', {})
        total_tokens = {
            'G1': g1_tokens,
            'G2': g2_tokens,
            'total_prompt': g1_tokens.get('prompt_tokens', 0) + g2_tokens.get('prompt_tokens', 0),
            'total_completion': g1_tokens.get('completion_tokens', 0) + g2_tokens.get('completion_tokens', 0),
            'total': g1_tokens.get('total_tokens', 0) + g2_tokens.get('total_tokens', 0)
        }

        logger.info(f"[Stage G] 統合完了:")
        logger.info(f"  ├─ source_inventory: {len(source_inventory)}件")
        logger.info(f"  ├─ table_inventory: {len(table_inventory)}件")
        logger.info(f"  ├─ unified_text: {len(unified_text)}文字")
        logger.info(f"  ├─ warnings: {len(warnings)}件")
        logger.info(f"  └─ トークン合計: {total_tokens['total']} (G1={g1_tokens.get('total_tokens', 0)}, G2={g2_tokens.get('total_tokens', 0)})")

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': table_inventory,
            'cross_validation': cross_validation,
            'ref_count': len(source_inventory) + len(table_inventory),
            'warnings': warnings,
            'processing_mode': 'g_gate_v2',
            'post_body': post_body or {},
            # 新しい出力（H1/H2 で直接使用）
            'g1_result': g1_result,
            'g2_result': g2_result,
            'token_usage': total_tokens
        }

    # ============================================
    # H1/H2 ルーティング（新設計対応版）
    # ============================================
    def route_anchors_to_stages(
        self,
        stage_g_result: Dict[str, Any],
        anchors: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        アンカー単位でH1/H2への振り分けを行う（v2.0対応）

        新設計では G1/G2 の結果を直接使用可能

        Args:
            stage_g_result: Stage G の処理結果
            anchors: Stage F からのアンカー配列（レガシー用）

        Returns:
            {
                'h1_payload': {...},
                'h2_payload': {...},
                'anchor_map': {...}
            }
        """
        logger.info("[Stage G] H1/H2 ルーティング開始")

        # 新設計: g1_result と g2_result が存在する場合
        g1_result = stage_g_result.get('g1_result')
        g2_result = stage_g_result.get('g2_result')

        if g1_result and g2_result:
            return self._route_from_g1_g2(g1_result, g2_result, stage_g_result)

        # レガシー: 従来のルーティングロジック
        return self._legacy_route_anchors(stage_g_result, anchors)

    def _route_from_g1_g2(
        self,
        g1_result: Dict[str, Any],
        g2_result: Dict[str, Any],
        stage_g_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        G1/G2 の結果から H1/H2 ペイロードを構築

        Args:
            g1_result: G1（表整理）の出力
            g2_result: G2（テキスト整理）の出力
            stage_g_result: Stage G 全体の出力

        Returns:
            H1/H2 ルーティング結果
        """
        tables = g1_result.get('tables', [])
        segments = g2_result.get('segments', [])

        # H1 ペイロード: 全ての表（重い表を優先処理）
        heavy_tables = [t for t in tables if t.get('is_heavy', False)]
        light_tables = [t for t in tables if not t.get('is_heavy', False)]

        h1_payload = {
            'heavy_tables': heavy_tables,
            'light_tables': light_tables,
            'table_anchors': [t.get('anchor_id', '') for t in heavy_tables],
            'table_page_context': g1_result.get('table_page_context', {}),
            'validation_results': g1_result.get('validation_results', [])
        }

        # H2 ペイロード: テキストセグメント + 軽い表
        text_anchors = [s for s in segments if s.get('segment_type') != 'table_marker']

        h2_payload = {
            'text_anchors': text_anchors,
            'light_tables': light_tables,
            'reduced_text': g2_result.get('unified_text', ''),
            'dedup_stats': g2_result.get('dedup_stats', {}),
            'post_body': g2_result.get('post_body', {})
        }

        # アンカーマップ
        anchor_map = {}
        for t in heavy_tables:
            anchor_map[t.get('anchor_id', '')] = 'h1'
        for t in light_tables:
            anchor_map[t.get('anchor_id', '')] = 'h2'
        for s in text_anchors:
            anchor_map[s.get('ref_id', '')] = 'h2'

        logger.info(f"[Stage G] ルーティング完了（v2.0）:")
        logger.info(f"  ├─ H1: {len(heavy_tables)}重い表 + {len(light_tables)}軽い表")
        logger.info(f"  ├─ H2: {len(text_anchors)}テキスト")
        logger.info(f"  └─ reduced_text: {len(h2_payload['reduced_text'])}文字")

        return {
            'h1_payload': h1_payload,
            'h2_payload': h2_payload,
            'anchor_map': anchor_map
        }

    # ============================================
    # 特殊ケース処理（従来ロジック）
    # ============================================
    def _process_post_body_only(self, post_body: Optional[Dict]) -> Dict[str, Any]:
        """投稿本文のみの処理（添付なし）"""
        text = post_body.get('text', '') if post_body else ''

        source_inventory = []
        if text:
            source_inventory.append({
                'ref_id': 'REF_001',
                'text': text,
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })

        # 空の G1/G2 結果を作成
        g1_result = {
            'tables': [],
            'validation_results': [],
            'table_page_context': {},
            'statistics': {'total_tables': 0, 'valid_tables': 0, 'total_rows': 0}
        }
        g2_result = {
            'segments': source_inventory,
            'unified_text': text,
            'dedup_stats': {'total_input': 1, 'total_output': 1, 'duplicates_removed': 0},
            'post_body': post_body or {}
        }

        return {
            'unified_text': text,
            'source_inventory': source_inventory,
            'table_inventory': [],
            'cross_validation': {'mode': 'post_body_only'},
            'ref_count': len(source_inventory),
            'warnings': [],
            'processing_mode': 'post_body_only',
            'post_body': post_body or {},
            'g1_result': g1_result,
            'g2_result': g2_result
        }

    def _process_e_only(
        self,
        stage_e_result: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """Stage E のみの処理（ドキュメント、Stage F スキップ）"""
        e_content = stage_e_result.get('content', '')

        source_inventory = []
        ref_index = 1

        # post_body を先頭に
        if post_body and post_body.get('text'):
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': post_body['text'],
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })
            ref_index += 1

        # Stage E テキストを追加
        if e_content:
            paragraphs = self._split_paragraphs(e_content)
            for para in paragraphs:
                if para.strip():
                    source_inventory.append({
                        'ref_id': f'REF_{ref_index:03d}',
                        'text': para.strip(),
                        'type': 'paragraph',
                        'source': 'stage_e',
                        'confidence': 'high'
                    })
                    ref_index += 1

        # unified_text 構築
        unified_parts = []
        if post_body and post_body.get('text'):
            unified_parts.append(post_body['text'])
        if e_content:
            unified_parts.append(e_content)
        unified_text = '\n\n'.join(unified_parts)

        # G1/G2 結果
        g1_result = {
            'tables': [],
            'validation_results': [],
            'table_page_context': {},
            'statistics': {'total_tables': 0, 'valid_tables': 0, 'total_rows': 0}
        }
        g2_result = {
            'segments': source_inventory,
            'unified_text': unified_text,
            'dedup_stats': {'total_input': len(source_inventory), 'total_output': len(source_inventory), 'duplicates_removed': 0},
            'post_body': post_body or {}
        }

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': [],
            'cross_validation': {'mode': 'e_only'},
            'ref_count': len(source_inventory),
            'warnings': [],
            'processing_mode': 'e_only',
            'post_body': post_body or {},
            'g1_result': g1_result,
            'g2_result': g2_result
        }

    def _process_transcription(
        self,
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """音声/動画の Transcription 処理"""
        f_path_a = stage_f_payload.get('path_a_result', {})
        transcript = f_path_a.get('transcript', '')
        visual_log = f_path_a.get('visual_log', '')

        source_inventory = []
        ref_index = 1

        # post_body を先頭に
        if post_body and post_body.get('text'):
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': post_body['text'],
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })
            ref_index += 1

        # Transcript を追加
        if transcript:
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': transcript,
                'type': 'transcript',
                'source': 'stage_f.path_a',
                'confidence': 'high'
            })
            ref_index += 1

        # Visual log を追加（動画の場合）
        if visual_log:
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': visual_log,
                'type': 'visual_log',
                'source': 'stage_f.path_a',
                'confidence': 'high'
            })
            ref_index += 1

        # unified_text 構築
        unified_parts = []
        if post_body and post_body.get('text'):
            unified_parts.append(f"【投稿本文】\n{post_body['text']}")
        if transcript:
            unified_parts.append(f"【書き起こし】\n{transcript}")
        if visual_log:
            unified_parts.append(f"【映像ログ】\n{visual_log}")
        unified_text = '\n\n---\n\n'.join(unified_parts)

        # G1/G2 結果
        g1_result = {
            'tables': [],
            'validation_results': [],
            'table_page_context': {},
            'statistics': {'total_tables': 0, 'valid_tables': 0, 'total_rows': 0}
        }
        g2_result = {
            'segments': source_inventory,
            'unified_text': unified_text,
            'dedup_stats': {'total_input': len(source_inventory), 'total_output': len(source_inventory), 'duplicates_removed': 0},
            'post_body': post_body or {}
        }

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': [],
            'cross_validation': {'mode': 'transcription'},
            'ref_count': len(source_inventory),
            'warnings': [],
            'processing_mode': 'transcription',
            'post_body': post_body or {},
            'g1_result': g1_result,
            'g2_result': g2_result
        }

    # ============================================
    # レガシー処理（フォールバック用）
    # ============================================
    def _legacy_rule_based_merge(
        self,
        stage_e_result: Dict[str, Any],
        stage_f_payload: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """レガシーのルールベース統合（フォールバック）"""
        logger.warning("[Stage G] レガシーモードで処理")

        e_content = stage_e_result.get('content', '')
        f_full_text = stage_f_payload.get('full_text', '')
        f_tables = stage_f_payload.get('tables', [])

        source_inventory = []
        table_inventory = []
        ref_index = 1
        tbl_index = 1

        # post_body
        if post_body and post_body.get('text'):
            source_inventory.append({
                'ref_id': f'REF_{ref_index:03d}',
                'text': post_body['text'],
                'type': 'post_body',
                'source': 'post_body',
                'confidence': 'high'
            })
            ref_index += 1

        # E テキスト
        if e_content:
            paragraphs = self._split_paragraphs(e_content)
            for para in paragraphs:
                if para.strip():
                    source_inventory.append({
                        'ref_id': f'REF_{ref_index:03d}',
                        'text': para.strip(),
                        'type': 'paragraph',
                        'source': 'stage_e',
                        'confidence': 'medium'
                    })
                    ref_index += 1

        # F 表
        for tbl in f_tables:
            table_inventory.append({
                'ref_id': f'TBL_{tbl_index:03d}',
                'table_title': tbl.get('table_title', ''),
                'table_type': tbl.get('table_type', 'visual_table'),
                'headers': tbl.get('headers', tbl.get('columns', [])),
                'rows': tbl.get('rows', []),
                'row_count': len(tbl.get('rows', [])),
                'col_count': len(tbl.get('headers', tbl.get('columns', []))),
                'source': 'stage_f'
            })
            tbl_index += 1

        # unified_text
        unified_parts = [s['text'] for s in source_inventory]
        unified_text = '\n\n'.join(unified_parts)

        # 空の G1/G2 結果
        g1_result = {
            'tables': table_inventory,
            'validation_results': [],
            'table_page_context': {},
            'statistics': {'total_tables': len(table_inventory), 'valid_tables': len(table_inventory), 'total_rows': sum(t.get('row_count', 0) for t in table_inventory)}
        }
        g2_result = {
            'segments': source_inventory,
            'unified_text': unified_text,
            'dedup_stats': {'total_input': len(source_inventory), 'total_output': len(source_inventory), 'duplicates_removed': 0},
            'post_body': post_body or {}
        }

        return {
            'unified_text': unified_text,
            'source_inventory': source_inventory,
            'table_inventory': table_inventory,
            'cross_validation': {'mode': 'legacy_rule_based'},
            'ref_count': len(source_inventory) + len(table_inventory),
            'warnings': ['Used legacy rule-based merge as fallback'],
            'processing_mode': 'legacy_rule_based',
            'post_body': post_body or {},
            'g1_result': g1_result,
            'g2_result': g2_result
        }

    def _legacy_route_anchors(
        self,
        stage_g_result: Dict[str, Any],
        anchors: Optional[List[Dict]]
    ) -> Dict[str, Any]:
        """レガシーのアンカールーティング"""
        logger.info("[Stage G] レガシールーティング使用")

        h1_payload = {'heavy_tables': [], 'table_anchors': []}
        h2_payload = {'text_anchors': [], 'light_tables': [], 'reduced_text': ''}
        anchor_map = {}

        table_inventory = stage_g_result.get('table_inventory', [])
        source_inventory = stage_g_result.get('source_inventory', [])

        # 表をH1/H2に振り分け
        for tbl in table_inventory:
            ref_id = tbl.get('ref_id', '')
            rows = tbl.get('rows', [])
            headers = tbl.get('headers', [])
            is_heavy = len(rows) >= 20 or len(headers) >= 5

            if is_heavy:
                h1_payload['heavy_tables'].append(tbl)
                h1_payload['table_anchors'].append(ref_id)
                anchor_map[ref_id] = 'h1'
            else:
                h2_payload['light_tables'].append(tbl)
                anchor_map[ref_id] = 'h2'

        # テキストはH2
        for src in source_inventory:
            ref_id = src.get('ref_id', '')
            h2_payload['text_anchors'].append(src)
            anchor_map[ref_id] = 'h2'

        h2_payload['reduced_text'] = stage_g_result.get('unified_text', '')

        return {
            'h1_payload': h1_payload,
            'h2_payload': h2_payload,
            'anchor_map': anchor_map
        }

    def _split_paragraphs(self, text: str) -> List[str]:
        """テキストを段落に分割"""
        import re
        paragraphs = re.split(r'\n\s*\n|\r\n\s*\r\n', text)
        return [p.strip() for p in paragraphs if p.strip()]
