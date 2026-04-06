"""
Doda Scraper 管理UI - Flask app (port 5006)
"""
import os
import sys
from pathlib import Path

root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(root_dir / '.env')

from flask import Flask, Response, request, jsonify, render_template, stream_with_context
from flask_cors import CORS
# Note: Reuse runner.py logic from data-ingestion if possible, or create a simple version here
import runner

app = Flask(__name__)
CORS(app)

# 実行可能なスクリプト
SCRIPTS = {
    'scrape': {
        'name': '求人一覧・詳細取得 (scraper.py)',
        'script': 'services/doda-scraper/scraper.py',
        'desc': 'dodaマイページから求人一覧と詳細を取得します。※Cloud Run上ではブラウザの起動に失敗する可能性があります。'
    },
    'enrich': {
        'name': 'データ構造化 (enrich_jobs.py)',
        'script': 'services/doda-scraper/enrich_jobs.py',
        'desc': '取得済みの求人テキストをGeminiで構造化します。'
    }
}

@app.route('/')
def index():
    return render_template('index.html', scripts=SCRIPTS)

@app.route('/api/scripts')
def api_scripts():
    return jsonify(SCRIPTS)

@app.route('/api/run/<script_id>', methods=['POST'])
def api_run(script_id: str):
    if script_id not in SCRIPTS:
        return jsonify({'error': f'Unknown script: {script_id}'}), 404

    script = SCRIPTS[script_id]
    script_path = str(root_dir / script['script'])

    # POSTボディの extra_args
    body = request.get_json(silent=True) or {}
    extra_args = body.get('extra_args', [])
    if isinstance(extra_args, str):
        extra_args = extra_args.split()

    run_id = runner.start_run(script_id, script_path, extra_args)
    return jsonify({'run_id': run_id, 'script_id': script_id})

@app.route('/api/stream/<run_id>')
def api_stream(run_id: str):
    def generate():
        yield from runner.stream_log(run_id)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5006))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
