"""

表・セル処理に「特定語彙だけで切る／展開する」実装が戻らないことを静的に守る。



設計: 表の row/col 分割は G26 layout_split のみ。G41 は機械適用のみ。セル展開・G36 は geometry / 構造記号のみ。

フォールバック経路でも語彙マッチ禁止。

"""



from __future__ import annotations



import re

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]



# (正規表現, 人間向け理由) — コメント行は除外

FORBIDDEN_PATTERNS: list[tuple[str, str]] = [

    (r"dual_section", "収支型 dual_section 列分割"),

    (r"_detect_dual_section", "収支型 dual_section 列分割"),

    (r'"支出"\s+in\s+', "見出し語「支出」での分岐"),

    (r'"収入"\s+in\s+.*col_split|col_split.*"収入"\s+in', "見出し語「収入」での col_split 分岐"),

    (r"収支型.*列分割|列分割.*収支型", "収支型と名指す列分割ロジック"),

    (r"_split_income_labels", "収支語彙によるラベル分割"),

    (r"\?=\(?:積立金\|転入\)", "積立金・転入でのセル分割"),

    (r're\.split\(r"\(\?=\(?:積立金', "積立金・転入でのセル分割"),

    (r'"朝"\s+not\s+in', "時間割語「朝」での分岐"),
    (r"_detect_class_pair_col_split", "G41 学級ラベル列分割（G26 専用設計違反）"),
    (r"_detect_twin_parallel_header_col_split", "G41 並列ヘッダー geometry 列分割"),

    (r'"限"\s+not\s+in', "時間割語「限」での分岐"),

]



SCAN_GLOBS = (

    "dms/pipeline/stage_g/g41*.py",

    "dms/pipeline/stage_g/g26*.py",

    "dms/pipeline/stage_g/g36*.py",

    "dms/pipeline/stage_g/merged_cell_grid.py",

    "dms/pipeline/stage_f/f41*.py",

    "dms/pipeline/stage_f/f55*.py",

    "dms/pipeline/stage_f/f51*.py",

    "dms/pipeline/stage_f/merged_cell_grid.py",

    "dms/pipeline/stage_f/lr_merged_vertical_grid.py",

)





def _is_comment_only(line: str) -> bool:

    s = line.strip()

    return not s or s.startswith("#")





def test_no_keyword_table_split_patterns_in_pipeline():

    violations: list[str] = []

    for pattern in SCAN_GLOBS:

        for path in ROOT.glob(pattern):

            if not path.is_file():

                continue

            text = path.read_text(encoding="utf-8")

            for i, line in enumerate(text.splitlines(), 1):

                if _is_comment_only(line):

                    continue

                for rx, reason in FORBIDDEN_PATTERNS:

                    if re.search(rx, line):

                        violations.append(f"{path.relative_to(ROOT)}:{i}: {reason} → {line.strip()[:100]}")

    assert not violations, "語彙ルールの禁止パターン:\n" + "\n".join(violations)

