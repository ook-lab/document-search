"""
高速インデックス (services/fast-indexer) の対象スコープ。

処理の論理対象は raw 行に紐づく pipeline_meta（raw_table が下記のいずれか）。
09_unified_documents はフルパイプライン後の統合テーブルであり、ここでの「処理対象」ではない。
"""

FAST_INDEX_RAW_TABLES = frozenset(
    {
        "03_ema_classroom_01_raw",
        "04_ikuya_classroom_01_raw",
        "05_ikuya_waseaca_01_raw",
        "08_file_only_01_raw",
    }
)
