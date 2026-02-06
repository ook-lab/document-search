"""
Stage G v2.0 テスト（G-Gate + G1 + G2）

テスト内容:
1. G-Gate: 表とテキストの仕分け
2. G1: 表の検算と整形
3. G2: テキストの重複排除
4. 統合: H1/H2 ルーティング
"""
import pytest
from shared.pipeline.stage_g_gate import StageGGate
from shared.pipeline.stage_g1_table_refiner import StageG1TableRefiner
from shared.pipeline.stage_g2_text_refiner import StageG2TextRefiner


class TestStageGGate:
    """G-Gate のテスト"""

    def test_route_empty_input(self):
        """空入力のテスト"""
        gate = StageGGate()
        g1_input, g2_input = gate.route(
            stage_e_result={},
            stage_f_payload={},
            post_body=None
        )

        assert g1_input['tables'] == []
        assert g2_input['segments'] == []

    def test_route_post_body_only(self):
        """投稿本文のみのテスト"""
        gate = StageGGate()
        g1_input, g2_input = gate.route(
            stage_e_result={},
            stage_f_payload={},
            post_body={'text': 'テスト投稿本文', 'source': 'post_body'}
        )

        assert len(g2_input['segments']) == 1
        assert g2_input['segments'][0]['text'] == 'テスト投稿本文'
        assert g2_input['segments'][0]['segment_type'] == 'post_body'

    def test_route_with_tables(self):
        """表データを含む場合のテスト"""
        gate = StageGGate()

        stage_f_payload = {
            'tables': [
                {
                    'block_id': 'p0_b1',
                    'page': 0,
                    'table_title': 'テスト表',
                    'table_type': 'visual_table',
                    'headers': ['列1', '列2'],
                    'rows': [['A', 'B'], ['C', 'D']],
                }
            ],
            'text_blocks': [
                {
                    'block_id': 'p0_b0',
                    'page': 0,
                    'text': '見出しテキスト',
                    'block_type': 'heading',
                }
            ],
            'anchors': []
        }

        g1_input, g2_input = gate.route(
            stage_e_result={'content': ''},
            stage_f_payload=stage_f_payload,
            post_body=None
        )

        # 表が G1 に振り分けられている
        assert len(g1_input['tables']) == 1
        assert g1_input['tables'][0]['title'] == 'テスト表'

        # テキストが G2 に振り分けられている
        assert any(s['text'] == '見出しテキスト' for s in g2_input['segments'])


class TestStageG1TableRefiner:
    """G1 のテスト"""

    def test_process_empty(self):
        """空入力のテスト"""
        g1 = StageG1TableRefiner()
        result = g1.process({'tables': [], 'table_page_context': {}})

        assert result['tables'] == []
        assert result['statistics']['total_tables'] == 0

    def test_process_valid_table(self):
        """有効な表のテスト"""
        g1 = StageG1TableRefiner()

        g1_input = {
            'tables': [
                {
                    'anchor_id': 'TBL_001',
                    'page': 0,
                    'title': '成績一覧',
                    'table_type': 'ranking',
                    'headers': ['順位', '氏名', '点数'],
                    'rows': [
                        ['1', '山田', '100'],
                        ['2', '田中', '95'],
                    ],
                    'row_count': 2,
                    'col_count': 3,
                }
            ],
            'table_page_context': {}
        }

        result = g1.process(g1_input)

        assert len(result['tables']) == 1
        assert result['tables'][0]['is_valid']
        assert result['statistics']['total_tables'] == 1
        assert result['statistics']['valid_tables'] == 1

    def test_validate_mismatched_cells(self):
        """セル数不一致の検出テスト"""
        g1 = StageG1TableRefiner()

        g1_input = {
            'tables': [
                {
                    'anchor_id': 'TBL_001',
                    'page': 0,
                    'headers': ['A', 'B', 'C'],
                    'rows': [
                        ['1', '2'],  # セル不足
                        ['3', '4', '5', '6'],  # セル過剰
                    ],
                    'row_count': 2,
                    'col_count': 3,
                }
            ],
            'table_page_context': {}
        }

        result = g1.process(g1_input)

        # 警告が出ているはず
        assert len(result['validation_results'][0]['warnings']) > 0


class TestStageG2TextRefiner:
    """G2 のテスト"""

    def test_process_empty(self):
        """空入力のテスト"""
        g2 = StageG2TextRefiner()
        result = g2.process({'segments': [], 'post_body': {}})

        assert result['segments'] == []
        assert result['unified_text'] == ''

    def test_process_with_segments(self):
        """セグメント処理のテスト"""
        g2 = StageG2TextRefiner()

        g2_input = {
            'segments': [
                {'ref_id': 'REF_001', 'page': 0, 'text': '段落1', 'segment_type': 'paragraph', 'source': 'stage_e'},
                {'ref_id': 'REF_002', 'page': 0, 'text': '段落2', 'segment_type': 'paragraph', 'source': 'stage_f'},
            ],
            'post_body': {}
        }

        result = g2.process(g2_input)

        assert len(result['segments']) == 2
        assert '段落1' in result['unified_text']
        assert '段落2' in result['unified_text']

    def test_deduplicate_exact_match(self):
        """完全一致の重複排除テスト"""
        g2 = StageG2TextRefiner()

        g2_input = {
            'segments': [
                {'ref_id': 'REF_001', 'page': 0, 'text': '同じテキスト', 'segment_type': 'paragraph', 'source': 'stage_e'},
                {'ref_id': 'REF_002', 'page': 0, 'text': '同じテキスト', 'segment_type': 'paragraph', 'source': 'stage_f'},
            ],
            'post_body': {}
        }

        result = g2.process(g2_input)

        # 重複が排除されている
        assert result['dedup_stats']['duplicates_removed'] == 1
        assert len(result['segments']) == 1

    def test_table_placeholder_preserved(self):
        """表プレースホルダーの保持テスト"""
        g2 = StageG2TextRefiner()

        g2_input = {
            'segments': [
                {'ref_id': 'REF_001', 'page': 0, 'text': '', 'segment_type': 'table_marker', 'source': 'g_gate', 'table_placeholder': '[→ TBL_001 参照]'},
                {'ref_id': 'REF_002', 'page': 0, 'text': 'テキスト', 'segment_type': 'paragraph', 'source': 'stage_e'},
            ],
            'post_body': {}
        }

        result = g2.process(g2_input)

        # プレースホルダーが保持されている
        assert any(s.get('segment_type') == 'table_marker' for s in result['segments'])
        assert '[→ TBL_001 参照]' in result['unified_text']


class TestIntegration:
    """統合テスト"""

    def test_full_flow(self):
        """G-Gate → G1 → G2 の完全フロー"""
        gate = StageGGate()
        g1 = StageG1TableRefiner()
        g2 = StageG2TextRefiner()

        # 入力データ
        stage_e_result = {
            'content': '見出し\n\nこれは本文です。\n\n項目1\t値1\n項目2\t値2',
            'metadata': {}
        }

        stage_f_payload = {
            'tables': [
                {
                    'block_id': 'p0_tbl1',
                    'page': 0,
                    'table_title': 'データ表',
                    'headers': ['項目', '値'],
                    'rows': [['項目1', '値1'], ['項目2', '値2']],
                }
            ],
            'text_blocks': [
                {'block_id': 'p0_b0', 'page': 0, 'text': '見出し', 'block_type': 'heading'},
                {'block_id': 'p0_b1', 'page': 0, 'text': 'これは本文です。', 'block_type': 'paragraph'},
            ],
            'anchors': []
        }

        post_body = {'text': '投稿本文テスト', 'source': 'post_body'}

        # Step 1: G-Gate
        g1_input, g2_input = gate.route(stage_e_result, stage_f_payload, post_body)

        # Step 2: G1
        g1_result = g1.process(g1_input)

        # Step 3: G2
        g2_result = g2.process(g2_input)

        # 検証
        assert len(g1_result['tables']) >= 1  # 表がある
        assert g1_result['statistics']['valid_tables'] >= 1  # 有効な表

        assert len(g2_result['segments']) >= 1  # テキストがある
        assert '投稿本文テスト' in g2_result['unified_text']  # post_body が含まれる


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
