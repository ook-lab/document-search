"""
rag-prepare: 検索データ準備でベクトル登録する raw テーブル範囲。

正本は 01–05 系 raw、生成テキストは 09・10。補助は 09_unified_documents_meta（ステータスのみ）。
検索データ準備は pipeline_meta を読まない。
"""

RAG_PREPARE_VECTORIZE_RAW_TABLES = frozenset(
    {
        "03_ema_classroom_01_raw",
        "04_ikuya_classroom_01_raw",
        "05_ikuya_waseaca_01_raw",
        "08_file_only_01_raw",
    }
)
