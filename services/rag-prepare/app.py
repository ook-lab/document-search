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
)


def resolve_pipeline_lab_base() -> str:
    """
    pipeline-lab のベース URL（末尾スラッシュなし）。

    優先順: PIPELINE_LAB_BASE 環境変数
    → ローカル（K_SERVICE なし）: http://127.0.0.1:{PIPELINE_LAB_PORT|5055}
    → Cloud Run（K_SERVICE あり）: 推測は廃止し PIPELINE_LAB_BASE の明示設定を必須とする（未設定時は空文字）。
    """
    explicit = os.environ.get('PIPELINE_LAB_BASE', '').strip().rstrip('/')
    if explicit:
        return explicit
    if not os.environ.get('K_SERVICE'):
        port = (os.environ.get('PIPELINE_LAB_PORT') or '5055').strip()
        return f'http://127.0.0.1:{port}'
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
        pending_docs, list_error = fetch_pending_search_data_prep_docs(
            db.client, raw_tables, include_vectorized=True
        )
        if list_error:
            logger.error("検索データ準備 一覧: %s", list_error)
    except Exception as e:
        logger.error(f"Failed to fetch pending docs: {e}")
        list_error = str(e)

    pipeline_lab = resolve_pipeline_lab_base()
    pipeline_lab_error = None
    if not pipeline_lab:
        pipeline_lab_error = (
            "環境変数 PIPELINE_LAB_BASE が設定されていません。"
            "Cloud Run の環境変数を設定してください。"
        )
        logger.error(pipeline_lab_error)

    return render_template(
        "search_data_prep.html",
        docs=pending_docs,
        list_error=list_error,
        pipeline_lab_base=pipeline_lab,
        pipeline_lab_error=pipeline_lab_error,
        process_post_url="/process",
    )

def _run_search_index_register():
    data = request.get_json(silent=True) or {}
    unified_doc_id = str(data.get("unified_doc_id") or "").strip()
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
    unified_doc_id = str(data.get("unified_doc_id") or "").strip()
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

@app.route('/skip-ix', methods=['POST'])
def skip_ix():
    data = request.get_json(silent=True) or {}
    raw_table = (data.get("raw_table") or "").strip()
    raw_id = (data.get("raw_id") or "").strip()
    if not raw_table or not raw_id:
        return jsonify({"success": False, "error": "Missing raw_table or raw_id"}), 400
    try:
        from standalone.ud_meta import UD_META_TABLE
        from datetime import datetime, timezone
        db = RagServiceDB()
        now_iso = datetime.now(timezone.utc).isoformat()
        db.client.table(UD_META_TABLE).update(
            {"ix_skip_pdf": True, "updated_at": now_iso}
        ).eq("raw_table", raw_table).eq("raw_id", raw_id).select("raw_id").execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error("skip_ix failed: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)})


@app.route('/reset-ix-all', methods=['POST'])
def reset_ix_all():
    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500
    indexer = IndexerClass()
    result = indexer.reset_all_ix_vectorized_at(list(RAG_PREPARE_VECTORIZE_RAW_TABLES))
    return jsonify(result)


@app.route('/api/health', methods=['GET'])
def health_check():
    IndexerClass, DbClass = get_indexer_tools()
    return jsonify({
        'status': 'ok',
        'service': 'rag-prepare',
        'dependencies_loaded': bool(IndexerClass and DbClass)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), threaded=True)
