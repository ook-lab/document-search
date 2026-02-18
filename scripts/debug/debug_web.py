#!/usr/bin/env python3
"""
Debug Pipeline Web UI

ブラウザから PDF アップロード → ステージ選択 → 実行 → ログ確認 を行う。
SSE でリアルタイムログ配信。

使い方:
    python debug_web.py
    # ブラウザで http://localhost:5050 を開く
"""

import os
import sys
import json
import uuid
import time
from pathlib import Path
from datetime import datetime
from queue import Queue, Empty
from threading import Thread

from flask import Flask, request, jsonify, render_template, Response
from dotenv import load_dotenv

# プロジェクトルートをパスに追加
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env を読み込み
load_dotenv(PROJECT_ROOT / '.env')

# shared.pipeline.__init__.py の壊れたインポートを回避
import types
_pipeline_pkg = types.ModuleType('shared.pipeline')
_pipeline_pkg.__path__ = [str(PROJECT_ROOT / 'shared' / 'pipeline')]
_pipeline_pkg.__package__ = 'shared.pipeline'
sys.modules.setdefault('shared', types.ModuleType('shared'))
sys.modules['shared'].__path__ = [str(PROJECT_ROOT / 'shared')]
sys.modules['shared.pipeline'] = _pipeline_pkg

from loguru import logger
from run_debug_pipeline import DebugPipeline

# ────────────────────────────────────────
# Flask App
# ────────────────────────────────────────

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

DEBUG_OUTPUT = Path(os.environ.get('DEBUG_OUTPUT_DIR', str(SCRIPT_DIR / 'debug_output')))
DEBUG_OUTPUT.mkdir(exist_ok=True)

GDRIVE_DEBUG_FOLDER_ID = os.environ.get('GDRIVE_DEBUG_FOLDER_ID')

# ジョブ管理
_jobs = {}

# ステージ前提条件
STAGE_DEPS = {
    "A": [], "B": ["A"], "D": ["A", "B"],
    "E": ["A", "B", "D"], "F": ["A", "B", "D", "E"],
    "G": ["A", "B", "D", "E", "F"]
}

# サブステージ表示名（DebugPipeline.ALL_SUBSTAGES と同期すること）
SUBSTAGE_LABELS = {
    # Stage A
    "A3": "Entry Point",
    # Stage B
    "B1": "Controller（MIXED対応）",
    # Stage D
    "D3": "罫線抽出", "D5": "ラスター検出", "D8": "格子解析",
    "D9": "セル特定", "D10": "画像分割",
    # Stage E（E1Controller が E21/E30/E32/E37/E40 を内包）
    "E1": "AI抽出（全処理）",
    # Stage F
    "F1": "データ統合", "F3": "日付正規化", "F5": "表結合",
    # Stage G
    "G1": "表再現", "G3": "ブロック整頓", "G5": "ノイズ除去",
    "G11": "表構造化", "G12": "表AI処理", "G21": "記事生成", "G22": "カレンダー抽出",
}


# ────────────────────────────────────────
# Routes
# ────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html',
                           stages=DebugPipeline.STAGES,
                           substages=DebugPipeline.SUBSTAGES,
                           labels=SUBSTAGE_LABELS,
                           stage_deps=STAGE_DEPS)


@app.route('/api/sessions')
def list_sessions():
    """既存セッション一覧（完了ステージ情報付き）"""
    sessions = []
    if not DEBUG_OUTPUT.exists():
        return jsonify(sessions)

    for d in sorted(DEBUG_OUTPUT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        sid = d.name
        completed = _check_completed_stages(sid)
        pdf_files = list(d.glob('*.pdf'))
        sessions.append({
            'id': sid,
            'completed_stages': completed,
            'pdf': pdf_files[0].name if pdf_files else None,
            'created': datetime.fromtimestamp(d.stat().st_ctime).strftime('%Y-%m-%d %H:%M'),
        })
    return jsonify(sessions)


@app.route('/api/sessions/<session_id>/check')
def check_session(session_id):
    """特定セッションの完了ステージ + 実行可能ステージ"""
    completed = _check_completed_stages(session_id)
    runnable = _get_runnable_stages(completed)
    files = _list_output_files(session_id)
    return jsonify({
        'completed': completed,
        'runnable': runnable,
        'files': files,
    })


@app.route('/api/run', methods=['POST'])
def run_pipeline():
    """パイプライン実行開始"""
    data = request.form
    pdf_file = request.files.get('pdf')

    mode = data.get('mode', 'new')  # 'new' or 'existing'
    session_id = data.get('session_id', '')
    start = data.get('start') or None
    end = data.get('end') or None
    target = data.get('target') or None  # 単一ステージ/サブステージ指定（--stage 相当）
    force = data.get('force') == 'true'

    # 新規セッション
    if mode == 'new':
        if not pdf_file or not pdf_file.filename:
            return jsonify({'error': 'PDF ファイルが必要です'}), 400
        session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        session_dir = DEBUG_OUTPUT / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = session_dir / pdf_file.filename
        pdf_file.save(str(pdf_path))
    else:
        # 既存セッション
        if not session_id:
            return jsonify({'error': 'セッション ID が必要です'}), 400
        session_dir = DEBUG_OUTPUT / session_id
        if not session_dir.exists():
            return jsonify({'error': f'セッション {session_id} が見つかりません'}), 404
        pdf_files = list(session_dir.glob('*.pdf'))
        pdf_path = pdf_files[0] if pdf_files else None

    # 前提条件チェック
    if start and not force:
        stage_key = start[0] if len(start) > 1 else start
        completed = _check_completed_stages(session_id)
        for dep in STAGE_DEPS.get(stage_key, []):
            if dep not in completed:
                return jsonify({'error': f'前提ステージ {dep} が未完了です'}), 400

    # target 指定時は start/end を無視（--stage 相当）
    if target:
        if target not in DebugPipeline.VALID_TARGETS:
            return jsonify({'error': f'無効なターゲット: {target}。有効値: {DebugPipeline.VALID_TARGETS}'}), 400
        start = None
        end = None

    # ジョブ作成
    job_id = str(uuid.uuid4())[:8]
    log_queue = Queue()
    _jobs[job_id] = {
        'queue': log_queue,
        'done': False,
        'error': None,
        'result': None,
        'session_id': session_id,
    }

    # バックグラウンド実行
    thread = Thread(
        target=_run_pipeline_job,
        args=(job_id, session_id, str(pdf_path) if pdf_path else None, start, end, target, force),
        daemon=True
    )
    thread.start()

    return jsonify({'job_id': job_id, 'session_id': session_id})


@app.route('/api/logs/<job_id>')
def stream_logs(job_id):
    """SSE でリアルタイムログ配信"""
    job = _jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    def generate():
        while True:
            try:
                line = job['queue'].get(timeout=1)
                yield f"data: {json.dumps({'type': 'log', 'line': line}, ensure_ascii=False)}\n\n"
            except Empty:
                if job['done']:
                    payload = {
                        'type': 'done',
                        'session_id': job['session_id'],
                        'error': job.get('error'),
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    break
                # keep-alive
                yield f": keepalive\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/files/<session_id>/<filename>')
def get_file(session_id, filename):
    """結果 JSON ファイルの内容を返す"""
    file_path = DEBUG_OUTPUT / session_id / filename
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404
    if not file_path.suffix == '.json':
        return jsonify({'error': 'JSON ファイルのみ対応'}), 400
    # パストラバーサル防止
    try:
        file_path.resolve().relative_to(DEBUG_OUTPUT.resolve())
    except ValueError:
        return jsonify({'error': 'Invalid path'}), 400

    with open(file_path, 'r', encoding='utf-8') as f:
        content = json.load(f)
    return jsonify(content)


@app.route('/api/test-drive')
def test_drive():
    """Google Drive 接続テスト（パイプライン不要）"""
    if not GDRIVE_DEBUG_FOLDER_ID:
        return jsonify({'error': 'GDRIVE_DEBUG_FOLDER_ID 未設定'}), 500
    try:
        from shared.common.connectors.google_drive import GoogleDriveConnector
        drive = GoogleDriveConnector()
        about = drive.service.about().get(fields='user').execute()
        email = about['user']['emailAddress']
        folder_id = drive.create_folder('_test_', parent_folder_id=GDRIVE_DEBUG_FOLDER_ID)
        if folder_id:
            drive.trash_file(folder_id)
            return jsonify({'ok': True, 'account': email, 'folder_id': GDRIVE_DEBUG_FOLDER_ID})
        return jsonify({'ok': False, 'account': email, 'error': 'フォルダ作成失敗'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ────────────────────────────────────────
# Pipeline 実行
# ────────────────────────────────────────

def _run_pipeline_job(job_id, session_id, pdf_path, start, end, target, force):
    """バックグラウンドスレッドでパイプラインを実行"""
    job = _jobs[job_id]
    log_queue = job['queue']

    # loguru sink: queue に送る
    def queue_sink(message):
        text = str(message).strip()
        if text:
            log_queue.put(text)

    sink_id = logger.add(queue_sink, format="{time:HH:mm:ss} | {level:<5} | {message}", level="DEBUG")

    # ログファイルにも保存
    session_dir = DEBUG_OUTPUT / session_id
    log_file = session_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_sink_id = logger.add(str(log_file), format="{time:HH:mm:ss} | {level:<5} | {message}", level="DEBUG")

    try:
        pipeline = DebugPipeline(
            uuid=session_id,
            base_dir=str(DEBUG_OUTPUT),
        )
        result = pipeline.run(
            pdf_path=pdf_path,
            start=start,
            end=end,
            target=target,
            mode="only" if target else "all",
            force=force,
        )
        job['result'] = result
        if result.get('errors'):
            job['error'] = '; '.join(result['errors'])
    except Exception as e:
        logger.error(f"パイプラインエラー: {e}")
        job['error'] = str(e)
    finally:
        _upload_to_drive(session_id)
        job['done'] = True
        logger.remove(sink_id)
        logger.remove(file_sink_id)


# ────────────────────────────────────────
# Google Drive アップロード
# ────────────────────────────────────────

def _upload_to_drive(session_id: str):
    """セッションの JSON/LOG/画像ファイルを Google Drive にアップロード"""
    logger.info(f"[Drive] GDRIVE_DEBUG_FOLDER_ID={GDRIVE_DEBUG_FOLDER_ID!r}")
    if not GDRIVE_DEBUG_FOLDER_ID:
        logger.warning("[Drive] GDRIVE_DEBUG_FOLDER_ID が未設定のためスキップ")
        return
    try:
        from shared.common.connectors.google_drive import GoogleDriveConnector
        drive = GoogleDriveConnector()
        # 認証アカウント確認
        about = drive.service.about().get(fields='user').execute()
        logger.info(f"[Drive] 認証アカウント: {about['user']['emailAddress']}")
        session_dir = DEBUG_OUTPUT / session_id
        if not session_dir.exists():
            return

        # フォルダ名 = PDFファイル名（拡張子なし）
        pdf_files = list(session_dir.glob('*.pdf'))
        folder_name = pdf_files[0].stem if pdf_files else session_id
        folder_id = drive.create_folder(folder_name, parent_folder_id=GDRIVE_DEBUG_FOLDER_ID)
        if not folder_id:
            logger.warning("Google Drive フォルダ作成失敗")
            return

        # 中間画像用サブフォルダを Drive 上に作成
        images_folder_id = drive.create_folder('images', parent_folder_id=folder_id)

        upload_count = 0
        for f in session_dir.rglob('*'):
            if not f.is_file() or f.name.endswith('.bak'):
                continue

            if f.suffix in ('.json', '.log'):
                # JSON/LOG → ルートフォルダ
                drive_name = _reorder_filename(f.name, session_id)
                drive.upload_file_from_path(str(f), folder_id=folder_id, file_name=drive_name)
            elif f.suffix in ('.png', '.pdf'):
                # 画像/PDF → images サブフォルダ
                # サブディレクトリ内のファイルはプレフィックスを付与
                rel = f.relative_to(session_dir)
                if len(rel.parts) > 1:
                    drive_name = '_'.join(rel.parts)
                else:
                    drive_name = f.name
                target_folder = images_folder_id or folder_id
                drive.upload_file_from_path(str(f), folder_id=target_folder, file_name=drive_name)
            else:
                continue

            upload_count += 1
            logger.info(f"[Drive] アップロード: {f.name}")

        logger.info(f"Google Drive アップロード完了 (folder: {folder_name}, {upload_count}ファイル)")
    except Exception as e:
        logger.warning(f"Google Drive アップロード失敗: {e}")


def _reorder_filename(filename: str, session_id: str) -> str:
    """20260210_203441_stage_g.json → stage_g_20260210_203441.json"""
    if not filename.startswith(session_id + '_'):
        return filename
    rest = filename[len(session_id) + 1:]  # "stage_g.json"
    stem, ext = os.path.splitext(rest)      # "stage_g", ".json"
    return f"{stem}_{session_id}{ext}"


# ────────────────────────────────────────
# ヘルパー
# ────────────────────────────────────────

def _check_completed_stages(session_id):
    """完了済みステージを返す"""
    session_dir = DEBUG_OUTPUT / session_id
    completed = []
    for stage in DebugPipeline.STAGES:
        fp = session_dir / f"{session_id}_stage_{stage.lower()}.json"
        if fp.exists():
            completed.append(stage)
    return completed


def _get_runnable_stages(completed):
    """実行可能ステージを返す"""
    runnable = []
    for stage in DebugPipeline.STAGES:
        deps = STAGE_DEPS.get(stage, [])
        if all(d in completed for d in deps):
            runnable.append(stage)
    return runnable


def _list_output_files(session_id):
    """セッションの出力ファイル一覧"""
    session_dir = DEBUG_OUTPUT / session_id
    if not session_dir.exists():
        return []
    files = []
    for f in sorted(session_dir.iterdir()):
        if f.suffix == '.json' and not f.name.endswith('.bak'):
            files.append({
                'name': f.name,
                'size': f.stat().st_size,
                'modified': datetime.fromtimestamp(f.stat().st_mtime).strftime('%H:%M:%S'),
            })
    return files


# ────────────────────────────────────────
# Entry Point
# ────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    logger.info(f"Debug Pipeline Web UI starting on http://localhost:{port}")
    logger.info(f"Output directory: {DEBUG_OUTPUT}")
    logger.info(f"Google Drive folder: {GDRIVE_DEBUG_FOLDER_ID or '未設定'}")
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)
