"""F-57 が F-55 AI メタをプロンプト用に整形する（配線の単体テスト）"""

from dms.pipeline.stage_f.f46_semantic_estimator import _f13_layout_hint_section


def test_f13_hints_empty_without_detection():
    assert _f13_layout_hint_section(None, sub_index=0, n_subs=1) == ""
    assert _f13_layout_hint_section({}, sub_index=0, n_subs=1) == ""


def test_f13_hints_intent_only_when_summaries_mismatch():
    det = {
        "ai_whole_table_intent": "  全体の予定表  ",
        "ai_block_summaries": ["a", "b"],
    }
    out = _f13_layout_hint_section(det, sub_index=0, n_subs=3)
    assert "全体の予定表" in out
    assert "このブロック" not in out


def test_f13_hints_includes_block_when_lengths_match():
    det = {
        "ai_whole_table_intent": "月別スケジュール",
        "ai_block_summaries": ["4月分", "5月分"],
    }
    out0 = _f13_layout_hint_section(det, sub_index=0, n_subs=2)
    out1 = _f13_layout_hint_section(det, sub_index=1, n_subs=2)
    assert "4月分" in out0
    assert "5月分" in out1
    assert "月別スケジュール" in out0 and "月別スケジュール" in out1
