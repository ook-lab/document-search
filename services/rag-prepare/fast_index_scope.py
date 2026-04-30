"""
RAG 準備 (services/rag-prepare) の対象スコープ。

処理の論理対象は raw 行に紐づく pipeline_meta（raw_table が下記のいずれか）。
09_unified_documents はフルパイプライン後の統合テーブルであり、ここでの「処理対象」ではない。
"""

import os

FAST_INDEX_RAW_TABLES = frozenset(
    {
        "03_ema_classroom_01_raw",
        "04_ikuya_classroom_01_raw",
        "05_ikuya_waseaca_01_raw",
        "08_file_only_01_raw",
    }
)


def resolve_pdf_toolbox_base() -> str:
    """
    PDF ツールボックスのベース URL（末尾スラッシュなし）。

    FAST_INDEX_PDF_TOOLBOX_BASE / PDF_TOOLBOX_BASE_URL / PDF_TOOLBOX_URL のいずれか。
    ローカル（K_SERVICE なし）で未設定のときは pdf-toolbox の既定ポートへ（PORT 未設定時 5050）。
    Cloud Run 上では必ずいずれかの環境変数で本番 URL を渡すこと。
    """
    for key in (
        "FAST_INDEX_PDF_TOOLBOX_BASE",
        "PDF_TOOLBOX_BASE_URL",
        "PDF_TOOLBOX_URL",
    ):
        raw = os.environ.get(key)
        if raw:
            s = str(raw).strip().rstrip("/")
            if s:
                return s
    if not os.environ.get("K_SERVICE"):
        port = (os.environ.get("PDF_TOOLBOX_PORT") or "5050").strip()
        return f"http://127.0.0.1:{port}".rstrip("/")
    return ""
