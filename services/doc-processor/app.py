"""
Flask Web Application - Document Processing System

サービスのAPIインターフェース
ロジックは shared/processing に委譲し、このファイルはAPIエンドポイントのみ提供

設計原則:
- 状態管理: StateManager（SSOT）に一元化
- 処理ロジック: DocumentProcessorに委譲
- このファイル: HTTPインターフェースのみ
"""
import os
import sys
import threading
import asyncio
from pathlib import Path
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from loguru import logger

# パス設定（Docker/ローカル両対応）
# Docker: PYTHONPATH=/app が設定済み
# ローカル: 以下で自動設定
_file_dir = Path(__file__).resolve().parent
_project_root = _file_dir.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.environ.setdefault('PROJECT_ROOT', str(_project_root))

# shared モジュールをインポート
from shared.processing import (
    StateManager,
    get_state_manager,
    DocumentProcessor,
    get_cgroup_memory,
    get_cgroup_cpu
)
from shared.common.database.client import DatabaseClient

# ========== Flaskアプリケーション設定 ==========
app = Flask(__name__)

# CORS設定
ALLOWED_ORIGINS = os.getenv(
    'ALLOWED_ORIGINS',
    'https://doc-processor-*.run.app,https://docs.ookubotechnologies.com'
).split(',')

if os.getenv('FLASK_ENV') == 'development' or os.getenv('DEBUG') == 'true':
    CORS(app)
else:
    CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ========== 認証設定 ==========
API_KEY = os.getenv('DOC_PROCESSOR_API_KEY', '')
REQUIRE_AUTH = os.getenv('REQUIRE_AUTH', 'true').lower() == 'true'


def require_api_key(f):
    """APIキー認証デコレーター"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not REQUIRE_AUTH:
            return f(*args, **kwargs)

        if not API_KEY:
            logger.warning("DOC_PROCESSOR_API_KEY is not set. Skipping authentication.")
            return f(*args, **kwargs)

        provided_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if provided_key != API_KEY:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401

        return f(*args, **kwargs)
    return decorated_function


def safe_error_response(error: Exception, status_code: int = 500):
    """安全なエラーレスポンスを生成"""
    import traceback
    tb = traceback.format_exc()
    logger.error(f"ERROR: {error}\n{tb}")
    return jsonify({'success': False, 'error': str(error), 'traceback': tb}), status_code


# ========== ルートエンドポイント ==========

@app.route('/')
def index():
    """メインページ"""
    return render_template('processing.html',
        supabase_url=os.getenv('SUPABASE_URL', ''),
        supabase_anon_key=os.getenv('SUPABASE_KEY', ''))


@app.route('/processing')
def processing():
    """ドキュメント処理システムのメインページ"""
    return render_template('processing.html',
        supabase_url=os.getenv('SUPABASE_URL', ''),
        supabase_anon_key=os.getenv('SUPABASE_KEY', ''))


@app.route('/api/health', methods=['GET'])
def health_check():
    """ヘルスチェックエンドポイント"""
    return jsonify({
        'status': 'ok',
        'message': 'Document Processing System is running',
        'version': '2025-01-13-refactored'
    })


# ========== ワークスペース ==========

@app.route('/api/workspaces', methods=['GET'])
def get_workspaces():
    """ワークスペース一覧を取得"""
    try:
        db = DatabaseClient()
        query = db.client.table('Rawdata_FILE_AND_MAIL').select('workspace').execute()

        workspaces = set()
        for row in query.data:
            workspace = row.get('workspace')
            if workspace:
                workspaces.add(workspace)

        return jsonify({
            'success': True,
            'workspaces': sorted(list(workspaces))
        })

    except Exception as e:
        logger.error(f"ワークスペース取得エラー: {e}")
        return safe_error_response(e)


# ========== 処理統計 ==========

@app.route('/api/process/stats', methods=['GET'])
def get_process_stats():
    """処理キューの統計情報を取得"""
    try:
        processor = DocumentProcessor()
        workspace = request.args.get('workspace', 'all')
        stats = processor.get_queue_stats(workspace)

        # フロントエンド用に 'failed' を 'error' に変換
        return jsonify({
            'success': True,
            'stats': {
                'pending': stats.get('pending', 0),
                'processing': stats.get('processing', 0),
                'completed': stats.get('completed', 0),
                'error': stats.get('failed', 0)
            }
        })

    except Exception as e:
        logger.error(f"統計取得エラー: {e}")
        return safe_error_response(e)


# ========== 処理進捗 ==========

@app.route('/api/process/progress', methods=['GET'])
def get_process_progress():
    """処理進捗とシステムリソースを取得（DBから共有状態を取得）"""
    try:
        state_manager = get_state_manager()

        # DBから最新状態を取得（マルチインスタンス対応）
        db_status = state_manager.get_status_from_db()

        # 現在処理中のドキュメントの進捗を取得
        current_stage = ''
        stage_progress = 0.0
        try:
            db = DatabaseClient()
            processing_doc = db.client.table('Rawdata_FILE_AND_MAIL').select(
                'processing_stage, processing_progress'
            ).eq('processing_status', 'processing').order(
                'created_at', desc=False
            ).limit(1).execute()

            if processing_doc.data:
                current_stage = processing_doc.data[0].get('processing_stage', '')
                stage_progress = processing_doc.data[0].get('processing_progress', 0.0)
        except Exception as e:
            logger.error(f"進捗取得エラー: {e}")

        return jsonify({
            'success': True,
            'processing': db_status.get('is_processing', False),
            'current_index': db_status.get('current_index', 0),
            'total_count': db_status.get('total_count', 0),
            'current_file': db_status.get('current_file', ''),
            'success_count': db_status.get('success_count', 0),
            'error_count': db_status.get('error_count', 0),
            'logs': db_status.get('logs', [])[-50:],
            'current_stage': current_stage,
            'stage_progress': stage_progress,
            'system': {
                'cpu_percent': db_status.get('cpu_percent', 0.0),
                'memory_percent': db_status.get('memory_percent', 0.0),
                'memory_used_gb': db_status.get('memory_used_gb', 0.0),
                'memory_total_gb': db_status.get('memory_total_gb', 0.0)
            },
            'resource_control': {
                'current_workers': db_status.get('current_workers', 0),
                'max_parallel': db_status.get('max_parallel', 1),
                'throttle_delay': db_status.get('throttle_delay', 0.0),
                'adjustment_count': db_status.get('adjustment_count', 0)
            }
        })

    except Exception as e:
        return safe_error_response(e)


# ========== 処理制御 ==========

@app.route('/api/process/start', methods=['POST'])
@require_api_key
def start_processing():
    """ドキュメント処理を開始（バックグラウンド実行）"""
    state_manager = get_state_manager()

    # ロック状態をチェック
    if state_manager.check_lock():
        return jsonify({
            'success': False,
            'error': '既に処理が実行中です'
        }), 400

    try:
        data = request.get_json() or {}
        workspace = data.get('workspace', 'all')
        limit = data.get('limit', 100)
        preserve_workspace = data.get('preserve_workspace', True)

        # ドキュメント数を事前確認
        processor = DocumentProcessor()
        docs = processor.get_pending_documents(workspace, limit)

        if not docs:
            return jsonify({
                'success': True,
                'message': '処理対象のドキュメントがありません',
                'processed': 0
            })

        # バックグラウンド処理関数
        def background_processing():
            try:
                proc = DocumentProcessor()
                asyncio.run(proc.run_batch(workspace, limit, preserve_workspace))
            except Exception as e:
                logger.error(f"バックグラウンド処理エラー: {e}")
                state_manager.reset()

        # 別スレッドで処理を開始
        thread = threading.Thread(target=background_processing, daemon=False)
        thread.start()

        return jsonify({
            'success': True,
            'message': '処理を開始しました',
            'total_count': len(docs)
        })

    except Exception as e:
        logger.error(f"処理開始エラー: {e}")
        return safe_error_response(e)


@app.route('/api/process/stop', methods=['POST'])
@require_api_key
def stop_processing():
    """処理を停止"""
    state_manager = get_state_manager()

    if not state_manager.check_lock():
        return jsonify({
            'success': False,
            'error': '実行中の処理がありません'
        }), 400

    state_manager.stop_processing()

    return jsonify({
        'success': True,
        'message': '停止リクエストを送信しました'
    })


@app.route('/api/process/reset', methods=['POST'])
def reset_processing():
    """処理フラグを強制リセット（緊急用）"""
    state_manager = get_state_manager()
    state_manager.reset()

    return jsonify({
        'success': True,
        'message': '処理フラグをリセットしました'
    })


# ========== エントリーポイント ==========

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
