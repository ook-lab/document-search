import os
import sys
import logging
from flask import Flask, render_template, request, jsonify

# ワークスペースルートをパスに追加
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from standalone import (
    RAG_PREPARE_VECTORIZE_RAW_TABLES,
    RagServiceDB,
    fetch_pending_search_data_prep_docs,
    resolve_pdf_toolbox_base,
)


def resolve_pipeline_lab_base(request_host: str = None) -> str:
    """PIPELINE_LAB_BASE 環境変数、またはホスト名から推測。"""
    explicit = os.environ.get('PIPELINE_LAB_BASE', '').strip().rstrip('/')
    if explicit:
        return explicit
    return ''

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# インポートの遅延実行とエラーハンドリング
def get_indexer_tools():
    try:
        from standalone.indexer import RagPrepareSearchIndexer

        return RagPrepareSearchIndexer, RagServiceDB
    except ImportError as e:
        logger.error(f"Import Error: {e}")
        return None, None

@app.route('/')
def index():
    _, DbClass = get_indexer_tools()
    if not DbClass:
        return "System Configuration Error: Missing dependencies.", 500

    pending_docs = []
    list_error = None
    try:
        db = DbClass()
        raw_tables = list(RAG_PREPARE_VECTORIZE_RAW_TABLES)
        pending_docs, list_error = fetch_pending_search_data_prep_docs(db.client, raw_tables)
        if list_error:
            logger.error("検索データ準備 一覧: %s", list_error)
    except Exception as e:
        logger.error(f"Failed to fetch pending docs: {e}")
        list_error = str(e)

    _fh = (request.headers.get("X-Forwarded-Host") or "").strip()
    req_host = (_fh.split(",")[0].strip() if _fh else "") or (request.host or "").strip()
    toolbox = resolve_pdf_toolbox_base(request_host=req_host or None)
    if not toolbox and os.environ.get("K_SERVICE"):
        logger.warning(
            "PDF ツールのベース URL を決められませんでした（環境変数 RAG_PREPARE_PDF_TOOLBOX_BASE 等、"
            "または Cloud Run の *-{プロジェクト番号}.{リージョン}.run.app 形式のホストが必要です）。"
            "カスタムドメインのみの場合は RAG_PREPARE_PDF_TOOLBOX_BASE を設定してください。"
        )

    pipeline_lab = resolve_pipeline_lab_base(request_host=req_host or None)

    return render_template(
        "search_data_prep.html",
        docs=pending_docs,
        list_error=list_error,
        pdf_toolbox_base=toolbox,
        pipeline_lab_base=pipeline_lab,
        process_post_url="/process",
    )

def _run_search_index_register():
    data = request.get_json(silent=True) or {}
    unified_doc_id = (data.get("unified_doc_id") or data.get("doc_id") or "").strip()
    raw_table = (data.get("raw_table") or "").strip()
    raw_id = (data.get("raw_id") or "").strip()
    if not unified_doc_id and not (raw_table and raw_id):
        return jsonify({"success": False, "error": "Missing unified_doc_id or (raw_table, raw_id)"}), 400

    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500

    indexer = IndexerClass()
    success, err_msg = indexer.process_document(
        unified_doc_id or None,
        raw_table=raw_table or None,
        raw_id=raw_id or None,
    )

    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': err_msg or 'Processing failed'})


def _run_date_signals_single():
    data = request.get_json(silent=True) or {}
    unified_doc_id = (data.get("unified_doc_id") or data.get("doc_id") or "").strip()
    raw_table = (data.get("raw_table") or "").strip()
    raw_id = (data.get("raw_id") or "").strip()
    if not unified_doc_id and not (raw_table and raw_id):
        return jsonify({"success": False, "error": "Missing unified_doc_id or (raw_table, raw_id)"}), 400
    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500
    indexer = IndexerClass()
    success, err_msg = indexer.process_date_signals_for_document(
        unified_doc_id or None,
        raw_table=raw_table or None,
        raw_id=raw_id or None,
    )
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': err_msg or 'Processing failed'})


def _run_date_signals_backfill():
    data = request.get_json(silent=True) or {}
    limit = int(data.get("limit") or 200)
    person = (data.get("person") or "").strip() or None
    source = (data.get("source") or "").strip() or None
    force = bool(data.get("force"))
    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500
    indexer = IndexerClass()
    result = indexer.backfill_date_signals(limit=limit, person=person, source=source, force=force)
    return jsonify(result)

@app.route('/process', methods=['POST'])
def process():
    return _run_search_index_register()


@app.route('/process-date-signals', methods=['POST'])
def process_date_signals():
    return _run_date_signals_single()


@app.route('/backfill-date-signals', methods=['POST'])
def backfill_date_signals():
    return _run_date_signals_backfill()

@app.route('/api/health', methods=['GET'])
def health_check():
    IndexerClass, DbClass = get_indexer_tools()
    return jsonify({
        'status': 'ok',
        'service': 'rag-prepare',
        'dependencies_loaded': bool(IndexerClass and DbClass)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
