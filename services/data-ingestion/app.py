"""
Data Ingestion 管理UI - Flask app (port 5004)
"""
import os
import sys
import tempfile
from pathlib import Path

root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv
load_dotenv(root_dir / '.env')

from flask import Flask, Response, request, jsonify, render_template, stream_with_context
import runner
from shared.common.database.client import DatabaseClient
from shared.common.connectors.google_drive import GoogleDriveConnector

app = Flask(__name__)

# ソース定義（表示順 + グループ）
SOURCES = {
    'gmail':   {
        'name': 'Gmail取込',
        'script': 'services/data-ingestion/gmail/gmail_ingestion.py',
        'group': 'gmail',
        # Gmail固有オプション: mail-type選択肢
        'mail_types': ['DM', 'JOB'],
    },
    'waseda':  {
        'name': '早稲田アカデミー',
        'script': 'services/data-ingestion/waseda_academy/notice_ingestion.py',
        'group': 'school',
    },
    'daiei':   {
        'name': 'ダイエー',
        'script': 'scripts/processing/process_daiei.py',
        'group': 'super',
    },
    'rakuten': {
        'name': '楽天西友',
        'script': 'scripts/processing/process_rakuten_seiyu.py',
        'group': 'super',
    },
    'tokyu':   {
        'name': '東急ストア',
        'script': 'scripts/processing/process_tokyu_store.py',
        'group': 'super',
    },
    'tokubai': {
        'name': 'トクバイ',
        'script': 'services/data-ingestion/tokubai/flyer_ingestion.py',
        'group': 'super',
    },
    'inbox':   {
        'name': 'InBox監視',
        'script': 'services/data-ingestion/monitoring/inbox_monitor.py',
        'group': 'monitor',
    },
}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/sources')
def api_sources():
    return jsonify(SOURCES)


@app.route('/api/gmail/labels')
def api_gmail_labels():
    """Gmail ラベル一覧を取得（ユーザー定義ラベルのみ）"""
    try:
        import os
        from shared.common.connectors.gmail_connector import GmailConnector
        user_email = os.getenv('GMAIL_DM_USER_EMAIL') or os.getenv('GMAIL_USER_EMAIL')
        if not user_email:
            return jsonify({'error': 'GMAIL_USER_EMAIL が設定されていません'}), 500
        gmail = GmailConnector(user_email=user_email)
        all_labels = gmail.list_labels()
        # ユーザー作成ラベルのみ返す（システムラベル INBOX/SENT 等を除外）
        labels = [
            {'id': lb['id'], 'name': lb['name']}
            for lb in all_labels
            if lb.get('type') == 'user'
        ]
        labels.sort(key=lambda x: x['name'])
        return jsonify(labels)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/run/<source>', methods=['POST'])
def api_run(source: str):
    if source not in SOURCES:
        return jsonify({'error': f'Unknown source: {source}'}), 404

    src = SOURCES[source]
    script_path = str(root_dir / src['script'])

    # POSTボディの extra_args を優先、なければDB設定
    body = request.get_json(silent=True) or {}
    if 'extra_args' in body:
        extra_args = body['extra_args']
        if isinstance(extra_args, str):
            extra_args = extra_args.split()
    else:
        settings = runner.get_settings(source)
        extra_args = settings.get('extra_args', [])
        if isinstance(extra_args, str):
            extra_args = extra_args.split()

    run_id = runner.start_run(source, script_path, extra_args)
    return jsonify({'run_id': run_id, 'source': source})


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


@app.route('/api/history')
def api_history():
    limit = request.args.get('limit', 50, type=int)
    history = runner.get_history(limit)
    return jsonify(history)


@app.route('/api/settings/<source>', methods=['GET'])
def api_get_settings(source: str):
    if source not in SOURCES:
        return jsonify({'error': f'Unknown source: {source}'}), 404
    settings = runner.get_settings(source)
    return jsonify(settings)


@app.route('/api/settings/<source>', methods=['POST'])
def api_save_settings(source: str):
    if source not in SOURCES:
        return jsonify({'error': f'Unknown source: {source}'}), 404
    data = request.get_json()
    if data is None:
        return jsonify({'error': 'Invalid JSON'}), 400
    ok = runner.save_settings(source, data)
    return jsonify({'ok': ok})


def _get_or_create_folder(drive: GoogleDriveConnector, parent_id: str, name: str) -> str:
    """parent_id 配下に name フォルダが存在すれば id を返す。なければ作成して id を返す。"""
    files = drive.list_files_in_folder(
        parent_id,
        mime_type_filter="mimeType='application/vnd.google-apps.folder'"
    )
    for f in files:
        if f['name'] == name:
            return f['id']
    return drive.create_folder(name, parent_id)


@app.route('/api/workspace-hierarchy')
def api_workspace_hierarchy():
    db = DatabaseClient(use_service_role=True)
    hierarchy = db.get_workspace_hierarchy()
    return jsonify(hierarchy)


@app.route('/api/upload', methods=['POST'])
def api_upload():
    file     = request.files['file']
    person   = request.form['person']
    source   = request.form['source']
    category = request.form.get('category', '')

    root_id = os.getenv('FILE_UPLOAD_ROOT_FOLDER_ID')
    drive = GoogleDriveConnector()

    # フォルダ階層を取得or作成
    person_id   = _get_or_create_folder(drive, root_id,   person)
    source_id   = _get_or_create_folder(drive, person_id, source)
    category_id = _get_or_create_folder(drive, source_id, category) if category else source_id

    # 一時ファイルに保存してアップロード
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        file.save(tmp_path)

    try:
        file_size = os.path.getsize(tmp_path)
        file_id = drive.upload_file_from_path(
            tmp_path,
            folder_id=category_id,
            mime_type=file.mimetype,
            file_name=file.filename,
        )
        # webViewLink を取得
        meta = drive.service.files().get(
            fileId=file_id,
            fields='webViewLink',
            supportsAllDrives=True
        ).execute()
        file_url = meta.get('webViewLink', '')
    finally:
        os.unlink(tmp_path)

    # Supabase 登録
    db = DatabaseClient(use_service_role=True)
    raw = db.client.table('08_file_only_01_raw').insert({
        'person':    person,
        'source':    source,
        'category':  category,
        'file_name': file.filename,
        'file_url':  file_url,
        'file_id':   file_id,
        'mime_type': file.mimetype,
        'file_size': file_size,
    }).execute()
    raw_id = raw.data[0]['id']

    db.client.table('pipeline_meta').insert({
        'raw_id':               raw_id,
        'raw_table':            '08_file_only_01_raw',
        'person':               person,
        'source':               source,
        'processing_status':    'pending',
        'processing_progress':  0.0,
        'attempt_count':        0,
    }).execute()

    return jsonify({'success': True, 'file_url': file_url, 'file_id': file_id})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5004))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
