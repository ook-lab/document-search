import os
import sys
import logging
from flask import Flask, render_template, request, jsonify

# ワークスペースルートをパスに追加
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from shared.fast_index import (
    FAST_INDEX_RAW_TABLES,
    fetch_pending_fast_index_docs,
    resolve_pdf_toolbox_base,
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# インポートの遅延実行とエラーハンドリング
def get_indexer_tools():
    try:
        from shared.fast_index import FastIndexer
        from shared.common.database.client import DatabaseClient
        return FastIndexer, DatabaseClient
    except ImportError as e:
        logger.error(f"Import Error: {e}")
        return None, None

@app.route('/')
def index():
    FastIndexerClass, DatabaseClientClass = get_indexer_tools()
    if not DatabaseClientClass:
        return "System Configuration Error: Missing dependencies.", 500

    pending_docs = []
    list_error = None
    try:
        db = DatabaseClientClass(use_service_role=True)
        raw_tables = list(FAST_INDEX_RAW_TABLES)
        pending_docs, list_error = fetch_pending_fast_index_docs(db.client, raw_tables)
        if list_error:
            logger.error("fast-index 一覧: %s", list_error)
    except Exception as e:
        logger.error(f"Failed to fetch pending docs: {e}")
        list_error = str(e)

    _fh = (request.headers.get("X-Forwarded-Host") or "").strip()
    req_host = (_fh.split(",")[0].strip() if _fh else "") or (request.host or "").strip()
    toolbox = resolve_pdf_toolbox_base(request_host=req_host or None)
    if not toolbox and os.environ.get("K_SERVICE"):
        logger.warning(
            "PDF ツールのベース URL を決められませんでした（環境変数 FAST_INDEX_PDF_TOOLBOX_BASE 等、"
            "または Cloud Run の *-{プロジェクト番号}.{リージョン}.run.app 形式のホストが必要です）。"
            "カスタムドメインのみの場合は FAST_INDEX_PDF_TOOLBOX_BASE を設定してください。"
        )

    return render_template(
        "fast_index.html",
        docs=pending_docs,
        list_error=list_error,
        pdf_toolbox_base=toolbox,
        fast_index_post_url="/process",
    )

def _run_fast_index():
    data = request.get_json(silent=True) or {}
    pipeline_id = data.get('pipeline_id')
    if not pipeline_id:
        return jsonify({'success': False, 'error': 'Missing pipeline_id'}), 400

    FastIndexerClass, _ = get_indexer_tools()
    if not FastIndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500

    indexer = FastIndexerClass()
    success, err_msg = indexer.process_document(pipeline_id)

    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': err_msg or 'Processing failed'})

@app.route('/process', methods=['POST'])
@app.route('/internal/fast_index', methods=['POST'])
def process():
    return _run_fast_index()

@app.route('/api/health', methods=['GET'])
def health_check():
    FastIndexerClass, DatabaseClientClass = get_indexer_tools()
    return jsonify({
        'status': 'ok',
        'service': 'rag-prepare',
        'dependencies_loaded': bool(FastIndexerClass and DatabaseClientClass)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
