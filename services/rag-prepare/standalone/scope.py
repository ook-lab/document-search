"""
fast-index の対象スコープ（rag-prepare standalone）。

処理の論理対象は raw 行に紐づく pipeline_meta（raw_table が下記のいずれか）。
09_unified_documents はフルパイプライン後の統合テーブルであり、ここでの「処理対象」ではない。
"""

import os
import re
from typing import Optional

FAST_INDEX_RAW_TABLES = frozenset(
    {
        "03_ema_classroom_01_raw",
        "04_ikuya_classroom_01_raw",
        "05_ikuya_waseaca_01_raw",
        "08_file_only_01_raw",
    }
)

# Cloud Run の regional URL: {任意サービス名}-{プロジェクト番号}.{リージョン}.run.app
_RUN_LEGACY_HOST = re.compile(
    r"-(?P<num>\d+)\.(?P<region>[a-z0-9-]+)\.run\.app$",
    re.IGNORECASE,
)


def _pdf_toolbox_from_cloud_run_host(host: Optional[str]) -> str:
    """doc-processor-….run.app 形式のホストから、同一番号・リージョンの pdf-toolbox URL を組み立てる。"""
    if not host:
        return ""
    h = host.split(":")[0].strip().lower()
    m = _RUN_LEGACY_HOST.search(h)
    if not m:
        return ""
    num, region = m.group("num"), m.group("region")
    return f"https://pdf-toolbox-{num}.{region}.run.app".rstrip("/")


def resolve_pdf_toolbox_base(*, request_host: Optional[str] = None) -> str:
    """
    PDF ツールボックスのベース URL（末尾スラッシュなし）。

    優先順: FAST_INDEX_PDF_TOOLBOX_BASE / PDF_TOOLBOX_BASE_URL / PDF_TOOLBOX_URL。
    いずれも無く Cloud Run（K_SERVICE あり）のとき、リクエストホストが
    ``*-{プロジェクト番号}.{リージョン}.run.app`` なら pdf-toolbox の sibling URL を推定する。
    ローカル（K_SERVICE なし）で未設定なら http://127.0.0.1:{PDF_TOOLBOX_PORT|5050}。
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
    derived = _pdf_toolbox_from_cloud_run_host(request_host)
    if derived:
        return derived
    return ""
