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
    "G11": "表構造化", "G17": "表AI処理", "G21": "記事生成", "G22": "カレンダー抽出",
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


@app.route('/api/sessions/<session_id>/diagnosis')
def session_diagnosis(session_id):
    """Stage A JSONから診断情報を生成"""
    stage_a_file = DEBUG_OUTPUT / session_id / f"{session_id}_stage_a.json"
    if not stage_a_file.exists():
        return jsonify({'available': False, 'error': 'Stage A 未実行'}), 200

    with open(stage_a_file, 'r', encoding='utf-8') as f:
        a = json.load(f)

    a2 = a.get('a2_type') or {}
    a4 = a.get('a4_layout') or {}
    gk = a.get('a5_gatekeeper') or a.get('gatekeeper') or {}

    origin_app = a2.get('origin_app') or a.get('origin_app') or a.get('document_type', 'UNKNOWN')
    confidence = a2.get('confidence') or a.get('confidence', 'NONE')
    reason = a2.get('reason') or a.get('reason', '')
    raw_meta = a2.get('raw_metadata') or a.get('raw_metadata') or {}
    page_type_map = a.get('page_type_map') or {}
    layout_metrics = a4.get('layout_metrics') or a.get('layout_metrics') or {}
    layout_profile = a4.get('layout_profile') or a.get('layout_profile', '?')

    # TypeAnalyzer パターン・Gatekeeper 定数をソースから直接インポート（コピーなし）
    try:
        from shared.pipeline.stage_a.a5_type_analyzer import A5TypeAnalyzer
        from shared.pipeline.stage_a.a5_gatekeeper import A5Gatekeeper
        ta = A5TypeAnalyzer()
        gk_cls = A5Gatekeeper()
        meta_patterns = {
            'GOODNOTES': ta.GOODNOTES_KEYWORDS,
            'GOOGLE_DOCS': ta.GOOGLE_DOCS_KEYWORDS,
            'GOOGLE_SHEETS': ta.GOOGLE_SHEETS_KEYWORDS,
            'WORD': ta.WORD_KEYWORDS,
            'ILLUSTRATOR': ta.ILLUSTRATOR_KEYWORDS,
            'INDESIGN': ta.INDESIGN_KEYWORDS,
            'EXCEL': ta.EXCEL_KEYWORDS,
            'SCAN': ta.SCAN_KEYWORDS,
        }
        page_patterns = {
            'REPORT（WINJrフォント）': ta.PAGE_FONT_REPORT,
            'DTP（Illustrator/InDesignフォント）': ta.PAGE_FONT_DTP,
            'WORD（Officeフォント）': ta.PAGE_FONT_WORD,
        }
        allowed_combinations = gk_cls.ALLOWED_COMBINATIONS
        GK_MIN_WORDS   = gk_cls.MIN_AVG_WORDS_PER_PAGE
        GK_MIN_CHARS   = gk_cls.MIN_AVG_CHARS_PER_PAGE
        GK_MAX_IMAGES  = gk_cls.MAX_AVG_IMAGES_PER_PAGE
        GK_MAX_X_STD   = gk_cls.MAX_AVG_X_STD
        GK_MAX_VECTORS = gk_cls.MAX_AVG_VECTORS_PER_PAGE
    except Exception:
        meta_patterns = {}
        page_patterns = {}
        allowed_combinations = []
        GK_MIN_WORDS = GK_MIN_CHARS = GK_MAX_IMAGES = GK_MAX_X_STD = GK_MAX_VECTORS = None

    # ページ種別集計
    page_types = {}
    for t in page_type_map.values():
        page_types[t] = page_types.get(t, 0) + 1

    # メトリクス
    per_page = layout_metrics.get('per_page') or []
    avg_images = float(layout_metrics.get('avg_images_per_page') or 0)
    avg_words = float(layout_metrics.get('avg_words_per_page') or 0)
    avg_x_std = float(layout_metrics.get('avg_x_std') or 0)
    avg_chars = (sum(p.get('chars', 0) for p in per_page) / len(per_page)) if per_page else 0.0

    # 判定
    confidence_ok = (confidence == 'HIGH')
    in_allowlist = ((origin_app, layout_profile) in allowed_combinations)

    threshold_results = None
    if origin_app == 'WORD' and confidence_ok and in_allowlist:
        threshold_results = [
            {'name': 'avg_words/page', 'value': round(avg_words, 1),
             'condition': f'≥{GK_MIN_WORDS}', 'ok': avg_words >= GK_MIN_WORDS},
            {'name': 'avg_chars/page', 'value': round(avg_chars, 1),
             'condition': f'≥{GK_MIN_CHARS}', 'ok': avg_chars >= GK_MIN_CHARS},
            {'name': 'avg_images/page', 'value': round(avg_images, 1),
             'condition': f'≤{GK_MAX_IMAGES}', 'ok': avg_images <= GK_MAX_IMAGES},
            {'name': 'avg_x_std', 'value': round(avg_x_std, 1),
             'condition': f'≤{GK_MAX_X_STD}', 'ok': avg_x_std <= GK_MAX_X_STD},
        ]

    # 提案
    suggestions = []
    if not confidence_ok:
        if not any(raw_meta.get(k, '') for k in ('Creator', 'Producer')):
            detail = 'Creator/Producer が両方空です。ページフォント解析も不一致でした。'
        else:
            creator = raw_meta.get('Creator', '')
            producer = raw_meta.get('Producer', '')
            detail = f'Creator="{creator}" / Producer="{producer}" がどのパターンにも一致しません。ページフォント解析も不一致でした。'
        suggestions.append({'type': 'info', 'title': '種別を特定できなかった原因', 'text': detail})
        suggestions.append({'type': 'fix', 'title': '対処法',
            'text': 'ページに使用されているフォントが右記パターンに一致すれば自動で種別が確定します。'
                    '現在のページフォント種別: ' + (', '.join(f'{t}:{c}p' for t, c in page_types.items()) or 'なし')})
    elif not in_allowlist:
        allowed_apps = sorted(set(c[0] for c in allowed_combinations))
        suggestions.append({'type': 'info', 'title': 'Allowlist 外の理由',
            'text': f'種別 "{origin_app}" は許可リストに登録されていません。'})
        suggestions.append({'type': 'fix', 'title': '許可リスト',
            'text': '現在の許可: ' + ', '.join(allowed_apps)})
    elif threshold_results and not all(t['ok'] for t in threshold_results):
        failed = [f'{t["name"]}={t["value"]}（条件:{t["condition"]}）'
                  for t in threshold_results if not t['ok']]
        suggestions.append({'type': 'info', 'title': '閾値チェック失敗', 'text': '、'.join(failed)})

    # メタデータ照合詳細（a5_type_analyzer が記録した照合過程）
    meta_match_detail = a2.get('meta_match_detail') or a.get('meta_match_detail')
    # ページフォント詳細（実際に検出されたフォント名）
    page_font_detail = a2.get('page_font_detail') or a.get('page_font_detail') or {}

    return jsonify({
        'available': True,
        'origin_app': origin_app,
        'confidence': confidence,
        'reason': reason,
        'layout_profile': layout_profile,
        'decision': gk.get('decision', '?'),
        'block_code': gk.get('block_code'),
        'block_reason': gk.get('block_reason', ''),
        'metadata': {k: (raw_meta.get(k) or '(空)') for k in ('Creator', 'Producer', 'Title', 'Author')},
        'page_types': page_types,
        'layout_metrics': {
            'avg_images': round(avg_images, 1),
            'avg_words': round(avg_words, 1),
            'avg_chars': round(avg_chars, 1),
            'avg_x_std': round(avg_x_std, 1),
        },
        'checks': {
            'confidence_ok': confidence_ok,
            'allowlist_ok': in_allowlist,
            'threshold_results': threshold_results,
        },
        'suggestions': suggestions,
        'meta_patterns': meta_patterns,
        'page_patterns': page_patterns,
        # 照合根拠
        'meta_match_detail': meta_match_detail,   # 各パターンとの照合結果
        'page_font_detail': page_font_detail,     # ページ別検出フォント名
    })


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

    # 個別ステージログに流れる詳細処理はrun logとWebUIから除外（各ファイルで確認）
    _INDIVIDUAL_LOG_PREFIXES = (
        "[A-2]", "[A-3]", "[A-4]", "[A-5]", "[A-6]",
        "[B-1]", "[B-3]", "[B-4]", "[B-5]",
        "[B-11]", "[B-12]", "[B-14]",
        "[B-30]", "[B-31]", "[B-42]", "[B-90]",
        "[D-1]", "[D-3]", "[D-5]", "[D-8]", "[D-9]", "[D-10]",
        "[E-1]", "[E-5]", "[E-20]", "[E-21]", "[E-30]", "[E-31]", "[E-32]", "[E-37]", "[E-40]",
        "[F-1]", "[F-3]", "[F-5]",
        "[G-1]", "[G-3]", "[G-5]", "[G-11]", "[G-17]", "[G-21]", "[G-22]",
    )

    def _pipeline_only(r):
        return (
            r["level"].name in ("WARNING", "ERROR", "CRITICAL")
            or not any(p in r["message"] for p in _INDIVIDUAL_LOG_PREFIXES)
        )

    # loguru sink: queue に送る（WebUI表示）
    def queue_sink(message):
        text = str(message).strip()
        if text:
            log_queue.put(text)

    sink_id = logger.add(queue_sink, format="{time:HH:mm:ss} | {level:<5} | {message}", filter=_pipeline_only, level="DEBUG")

    # ログファイルにも保存
    session_dir = DEBUG_OUTPUT / session_id
    log_file = session_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_sink_id = logger.add(str(log_file), format="{time:HH:mm:ss} | {level:<5} | {message}", filter=_pipeline_only, level="DEBUG")

    try:
        pipeline = DebugPipeline(
            uuid=session_id,
            base_dir=str(DEBUG_OUTPUT),
        )

        # ──────────────────────────────────────────
        # Google Drive ステージ毎アップロード設定
        # ──────────────────────────────────────────
        drive_state = {
            'drive': None,
            'folder_id': None,
            'images_folder_id': None,
        }

        def on_stage_complete(stage_name, stage_file_path):
            """各ステージ完了後に Drive へ即時アップロード"""
            _upload_stage_to_drive(session_id, stage_name, stage_file_path, drive_state)

        result = pipeline.run(
            pdf_path=pdf_path,
            start=start,
            end=end,
            target=target,
            mode="only" if target else "all",
            force=force,
            on_stage_complete=on_stage_complete if GDRIVE_DEBUG_FOLDER_ID else None,
        )
        job['result'] = result
        if result.get('errors'):
            job['error'] = '; '.join(result['errors'])

        # ログファイルを Drive へアップロード（パイプライン完了後）
        if GDRIVE_DEBUG_FOLDER_ID and drive_state['folder_id']:
            _upload_log_to_drive(session_id, log_file, drive_state)

    except Exception as e:
        logger.error(f"パイプラインエラー: {e}")
        job['error'] = str(e)
    finally:
        job['done'] = True
        logger.remove(sink_id)
        logger.remove(file_sink_id)


# ────────────────────────────────────────
# Google Drive アップロード
# ────────────────────────────────────────

# ステージ → サブステージ対応表（アップロード対象ファイルの決定に使用）
_STAGE_SUBSTAGES = {
    "A": ["a3"],
    "B": ["b1"],
    "D": ["d3", "d5", "d8", "d9", "d10"],
    "E": ["e1"],
    "F": ["f1", "f3", "f5"],
    "G": ["g1", "g3", "g5", "g11", "g17", "g21", "g22"],
}


def _ensure_drive_folder(session_id: str, drive_state: dict):
    """Drive フォルダが未作成なら作成し drive_state を更新する（冪等）"""
    if drive_state.get('folder_id'):
        return True  # 既に作成済み
    try:
        from shared.common.connectors.google_drive import GoogleDriveConnector
        drive = GoogleDriveConnector()
        about = drive.service.about().get(fields='user').execute()
        logger.info(f"[Drive] 認証アカウント: {about['user']['emailAddress']}")

        session_dir = DEBUG_OUTPUT / session_id
        pdf_files = list(session_dir.glob('*.pdf'))
        folder_name = pdf_files[0].stem if pdf_files else session_id

        folder_id = drive.create_folder(folder_name, parent_folder_id=GDRIVE_DEBUG_FOLDER_ID)
        if not folder_id:
            logger.warning(f"[Drive] フォルダ作成失敗: {folder_name}")
            return False
        images_folder_id = drive.create_folder('images', parent_folder_id=folder_id)

        drive_state['drive'] = drive
        drive_state['folder_id'] = folder_id
        drive_state['images_folder_id'] = images_folder_id
        logger.info(f"[Drive] フォルダ作成: {folder_name} ({folder_id})")
        return True
    except Exception as e:
        logger.warning(f"[Drive] 初期化失敗: {e}")
        return False


def _upload_stage_to_drive(session_id: str, stage_name: str, stage_file_path: str, drive_state: dict):
    """ステージ完了直後に JSON（ステージ＋サブステージ）+ 画像を Drive にアップロード"""
    if not GDRIVE_DEBUG_FOLDER_ID:
        logger.warning("[Drive] GDRIVE_DEBUG_FOLDER_ID 未設定 → スキップ")
        return
    if not _ensure_drive_folder(session_id, drive_state):
        return

    drive = drive_state['drive']
    folder_id = drive_state['folder_id']
    images_folder_id = drive_state['images_folder_id'] or folder_id
    session_dir = DEBUG_OUTPUT / session_id
    upload_count = 0

    try:
        # ── ① ステージ JSON ──────────────────────────────
        f = Path(stage_file_path)
        if f.exists() and f.suffix == '.json':
            drive_name = _reorder_filename(f.name, session_id)
            drive.upload_file_from_path(str(f), folder_id=folder_id, file_name=drive_name)
            logger.info(f"[Drive] Stage {stage_name} JSON → {drive_name}")
            upload_count += 1

        # ── ② サブステージ JSON（全て）────────────────────
        for sub in _STAGE_SUBSTAGES.get(stage_name, []):
            sf = session_dir / f"{session_id}_substage_{sub}.json"
            if sf.exists():
                drive_name = _reorder_filename(sf.name, session_id)
                drive.upload_file_from_path(str(sf), folder_id=folder_id, file_name=drive_name)
                logger.info(f"[Drive] {sub.upper()} JSON → {drive_name}")
                upload_count += 1

        # ── ② 個別プロセッサログ（中身があるファイルのみ）────────────────
        # ステージプレフィックスで glob し、0バイトは除外（未使用プロセッサのログを除く）
        prefix = stage_name.lower()
        for lf in sorted(session_dir.glob(f"{prefix}*.log")):
            if lf.stat().st_size > 0:
                drive.upload_file_from_path(str(lf), folder_id=folder_id, file_name=lf.name)
                logger.info(f"[Drive] {stage_name} log → {lf.name}")
                upload_count += 1

        if stage_name == "B":
            # B Stage: 各プロセッサの purged PDF（b30/b42/b90 それぞれ）
            purged_dir = session_dir / "purged"
            if purged_dir.exists():
                for pdf in sorted(purged_dir.glob("*.pdf")):
                    drive.upload_file_from_path(str(pdf), folder_id=images_folder_id, file_name=pdf.name)
                    logger.info(f"[Drive] B purged PDF → {pdf.name}")
                    upload_count += 1

        # ── ③ Stage D: 全画像ファイル ────────────────────
        if stage_name == "D":
            # d1_purged_page_*.png（session_dir ルート: fitz で生成した中間画像）
            for img in sorted(session_dir.glob('d1_purged_page_*.png')):
                drive.upload_file_from_path(str(img), folder_id=images_folder_id, file_name=img.name)
                logger.info(f"[Drive] purged PNG → {img.name}")
                upload_count += 1

            # d10_*.png（session_dir ルート: サブステージモード時の D10 出力）
            for img in sorted(session_dir.glob('d10_*.png')):
                drive.upload_file_from_path(str(img), folder_id=images_folder_id, file_name=img.name)
                logger.info(f"[Drive] D10 画像 → {img.name}")
                upload_count += 1

            # page_*/ 配下（D10 出力: background_only.png, table_T*.png など）
            for page_dir in sorted(session_dir.glob('page_*')):
                if not page_dir.is_dir():
                    continue
                for img in sorted(page_dir.glob('*.png')):
                    rel = img.relative_to(session_dir)
                    drive_name = '_'.join(rel.parts)
                    drive.upload_file_from_path(str(img), folder_id=images_folder_id, file_name=drive_name)
                    logger.info(f"[Drive] D 画像 → {drive_name}")
                    upload_count += 1

        # ── ④ Stage G: ui_data / final_metadata JSON ─────
        if stage_name == "G":
            for extra in ['ui_data', 'final_metadata']:
                xf = session_dir / f"{session_id}_{extra}.json"
                if xf.exists():
                    drive_name = _reorder_filename(xf.name, session_id)
                    drive.upload_file_from_path(str(xf), folder_id=folder_id, file_name=drive_name)
                    logger.info(f"[Drive] Stage G {extra} → {drive_name}")
                    upload_count += 1

        if upload_count:
            logger.info(f"[Drive] Stage {stage_name} アップロード完了 ({upload_count}件)")
        else:
            logger.warning(f"[Drive] Stage {stage_name}: アップロードするファイルが見つかりません")
    except Exception as e:
        logger.warning(f"[Drive] Stage {stage_name} アップロード失敗: {e}")


def _upload_log_to_drive(session_id: str, log_file: Path, drive_state: dict):
    """パイプライン完了後にログファイルを Drive にアップロード"""
    if not drive_state.get('folder_id'):
        return
    try:
        drive = drive_state['drive']
        folder_id = drive_state['folder_id']
        if log_file.exists():
            drive_name = _reorder_filename(log_file.name, session_id)
            drive.upload_file_from_path(str(log_file), folder_id=folder_id, file_name=drive_name)
            logger.info(f"[Drive] ログ → {drive_name}")
    except Exception as e:
        logger.warning(f"[Drive] ログアップロード失敗: {e}")


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
