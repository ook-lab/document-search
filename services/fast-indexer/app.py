import os
import sys
import logging
from flask import Flask, render_template, request, jsonify

# ワークスペースルートをパスに追加
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# インポートの遅延実行とエラーハンドリング
def get_indexer_tools():
    try:
        from fast_indexer import FastIndexer
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

    try:
        db = DatabaseClientClass(use_service_role=True)
        # PDF埋め込み済み & 未完了
        res_embedded = db.client.table('pipeline_meta') \
            .select('id, raw_id, raw_table, source, person, created_at') \
            .eq('text_embedded', True) \
            .neq('processing_status', 'completed') \
            .execute()
        
        # テキストオンリー (ファイルなし & Gmail以外)
        res_text_only = db.client.table('pipeline_meta') \
            .select('id, raw_id, raw_table, source, person, created_at') \
            .is_('drive_file_id', 'null') \
            .neq('source', 'gmail') \
            .neq('processing_status', 'completed') \
            .execute()
            
        docs_map = {d['id']: d for d in (res_embedded.data or []) + (res_text_only.data or [])}
        pending_docs = sorted(docs_map.values(), key=lambda x: x['created_at'], reverse=True)
    except Exception as e:
        logger.error(f"Failed to fetch pending docs: {e}")
        pending_docs = []

    return render_template('fast_index.html', docs=pending_docs)

def _run_fast_index():
    data = request.get_json(silent=True) or {}
    pipeline_id = data.get('pipeline_id')
    if not pipeline_id:
        return jsonify({'success': False, 'error': 'Missing pipeline_id'}), 400

    FastIndexerClass, _ = get_indexer_tools()
    if not FastIndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500

    indexer = FastIndexerClass()
    success = indexer.process_document(pipeline_id)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Processing failed'})

@app.route('/process', methods=['POST'])
@app.route('/internal/fast_index', methods=['POST'])
def process():
    return _run_fast_index()

@app.route('/api/health', methods=['GET'])
def health_check():
    FastIndexerClass, DatabaseClientClass = get_indexer_tools()
    return jsonify({
        'status': 'ok',
        'service': 'fast-indexer',
        'dependencies_loaded': bool(FastIndexerClass and DatabaseClientClass)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
