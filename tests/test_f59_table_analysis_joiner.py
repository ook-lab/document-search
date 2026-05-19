"""F59: 行分割サフィックス結合は F58 マーカー付きのみ（類似表の結合禁止）。"""

from __future__ import annotations

from dms.pipeline.stage_f.f59_table_analysis_joiner import join_table_analyses


def _sec(rows, **meta):
    return {'data': rows, 'metadata': dict(meta)}


def _analysis(tid: str, rows, **meta):
    return {
        'table_id': tid,
        'table_type': 'structured',
        'description': tid,
        'sections': [_sec(rows, **meta)],
        'metadata': dict(meta),
    }


def test_f59_does_not_merge_suffix_without_f58_marker():
    """同幅・連番 _S1 _S2 でも F58 行分割マーカーがなければ結合しない。"""
    a1 = _analysis('T_S1', [['h', 'h'], ['a', 'b']])
    a2 = _analysis('T_S2', [['h', 'h'], ['c', 'd']])
    out = join_table_analyses([a1, a2])
    assert len(out) == 2
    assert out[0]['table_id'] == 'T_S1'
    assert out[1]['table_id'] == 'T_S2'


def test_f59_merges_only_when_f58_row_split_marked():
    base = 'P0_X'
    meta = {'f58_row_split_sequence': True, 'f58_row_split_base_id': base}
    a1 = _analysis(f'{base}_S1', [['d1', 'd2'], ['1', '2']], **meta, f58_row_split_index=1)
    a2 = _analysis(f'{base}_S2', [['d1', 'd2'], ['3', '4']], **meta, f58_row_split_index=2)
    out = join_table_analyses([a1, a2])
    assert len(out) == 1
    assert out[0]['table_id'] == base


def test_f59_skips_when_base_id_mismatch():
    a1 = _analysis(
        'T_S1',
        [['a', 'b']],
        f58_row_split_sequence=True,
        f58_row_split_base_id='Other',
        f58_row_split_index=1,
    )
    a2 = _analysis(
        'T_S2',
        [['a', 'b']],
        f58_row_split_sequence=True,
        f58_row_split_base_id='Other',
        f58_row_split_index=2,
    )
    out = join_table_analyses([a1, a2])
    assert len(out) == 2
