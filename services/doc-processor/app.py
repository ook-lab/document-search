"""
Flask Web Application - Document Processing System

【アーキテクチャ原則】
- Web (Cloud Run / localhost) は enqueue・閲覧・運用操作のみ
- 処理実行は Worker (CLI) のみ
- この原則は設定で変更不可（構造的に強制）

【このファイルの責務】
- DB からの読み取り（workspaces, run-requests）
- DB への書き込み（ops_requests への enqueue）
- 処理実行コードは一切含まない
"""
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from loguru import logger

# パス設定（Docker/ローカル両対応）
_file_dir = Path(__file__).resolve().parent
_project_root = _file_dir.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.environ.setdefault('PROJECT_ROOT', str(_project_root))

# DB クライアントのみインポート（処理系は一切インポートしない）
from shared.common.database.client import DatabaseClient

# ========== ビルド情報（環境指紋） ==========
# 3環境（Cloud Run / localhost / terminal）で同一コードが動いていることを確認するため

def _get_git_sha() -> str:
    """Git SHA を取得（取得失敗時は 'unknown'）"""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5,
            cwd=_project_root
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return os.getenv('GIT_SHA', 'unknown')

# 起動時に一度だけ取得
BUILD_INFO = {
    'git_sha': _get_git_sha(),
    'build_time': os.getenv('BUILD_TIME', datetime.now().isoformat()),
    'version': '2025-01-16-enqueue-only'
}

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
@app.route('/run-requests')
def run_requests_page():
    """Run Requests ページ（唯一のUI入口）"""
    return render_template('run_requests.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    """ヘルスチェックエンドポイント

    【環境指紋】
    git_sha と build_time を返すことで、3環境（Cloud Run / localhost / terminal）
    で同一のコードが動いていることを確認できる。
    """
    return jsonify({
        'status': 'ok',
        'message': 'Document Processing System is running',
        'version': BUILD_INFO['version'],
        'git_sha': BUILD_INFO['git_sha'],
        'build_time': BUILD_INFO['build_time'],
        'mode': 'enqueue_only',
        'note': 'Processing is only available via CLI Worker'
    })


# ========== ワークスペース（Run Requests UI 専用） ==========
# NOTE: /internal/ は運用・内部処理向け。UI向けの /api/workspaces とは別物。

@app.route('/internal/workspaces', methods=['GET'])
def get_workspaces():
    """ワークスペース一覧を取得

    【責務】
    - Run Requests UI のワークスペース選択ドロップダウン用
    - ワークスペース名の一覧を返すだけ（統計・進捗は扱わない）

    【DBアクセス】
    - service_role を使用（RLSバイパス、他APIと統一）
    """
    try:
        db = DatabaseClient(use_service_role=True)
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


# ========== 処理監視ダッシュボード ==========

@app.route('/internal/dashboard', methods=['GET'])
def get_dashboard():
    """処理監視ダッシュボード（キュー状態 + ワーカー状況）

    【責務】
    - キュー状態（pending/processing/completed/failed/retry_pending）の集計
    - アクティブワーカー数と各ワーカーの処理件数

    【パラメータ】
    - workspace: 対象ワークスペース（省略時は全体）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        workspace = request.args.get('workspace', '')

        # ベースクエリ
        table = db.client.table('Rawdata_FILE_AND_MAIL')

        # ワークスペースフィルタ
        if workspace and workspace != 'all':
            base_filter = lambda q: q.eq('workspace', workspace)
        else:
            base_filter = lambda q: q
            workspace = 'all'

        # キュー状態集計
        # pending
        pending_q = base_filter(table.select('id', count='exact').eq('processing_status', 'pending'))
        pending_result = pending_q.execute()
        pending_count = pending_result.count if pending_result.count else 0

        # queued（新ステータス）
        queued_q = base_filter(table.select('id', count='exact').eq('processing_status', 'queued'))
        queued_result = queued_q.execute()
        queued_count = queued_result.count if queued_result.count else 0

        # processing
        processing_q = base_filter(table.select('id', count='exact').eq('processing_status', 'processing'))
        processing_result = processing_q.execute()
        processing_count = processing_result.count if processing_result.count else 0

        # completed
        completed_q = base_filter(table.select('id', count='exact').eq('processing_status', 'completed'))
        completed_result = completed_q.execute()
        completed_count = completed_result.count if completed_result.count else 0

        # failed
        failed_q = base_filter(table.select('id', count='exact').eq('processing_status', 'failed'))
        failed_result = failed_q.execute()
        failed_count = failed_result.count if failed_result.count else 0

        # ワーカー状況（processing 中の lease_owner を集計）
        workers_q = base_filter(table.select('lease_owner').eq('processing_status', 'processing').not_.is_('lease_owner', 'null'))
        workers_result = workers_q.execute()

        # lease_owner ごとにカウント
        worker_counts = {}
        for row in workers_result.data or []:
            owner = row.get('lease_owner')
            if owner:
                worker_counts[owner] = worker_counts.get(owner, 0) + 1

        by_worker = [{'worker_id': k, 'count': v} for k, v in sorted(worker_counts.items(), key=lambda x: -x[1])]

        return jsonify({
            'success': True,
            'workspace': workspace,
            'queue': {
                'pending': pending_count,
                'queued': queued_count,
                'processing': processing_count,
                'completed': completed_count,
                'failed': failed_count
            },
            'workers': {
                'active_processing': processing_count,
                'by_worker': by_worker
            }
        })

    except Exception as e:
        logger.error(f"ダッシュボード取得エラー: {e}")
        return safe_error_response(e)


# ========== 運用操作（DB enqueue のみ） ==========
#
# 【設計原則】
# - Web は DB に「要求」を書くだけ（enqueue）
# - 実際の処理・状態変更は Worker/Ops が行う
# - "start" という概念は Web に存在しない（Worker CLI のみ）
# - 全ての運用操作は "request-" プレフィックスで統一

@app.route('/internal/ops/requests', methods=['GET'])
def get_ops_requests():
    """ops_requests の一覧を取得（読み取り専用）

    【責務】
    - ops_requests テーブルから未処理の要求一覧を取得
    - UI での運用状況の可視化に使用
    - 書き込みは行わない
    """
    try:
        db = DatabaseClient()
        status_filter = request.args.get('status', 'queued')
        limit = min(int(request.args.get('limit', 100)), 500)

        query = db.client.table('ops_requests').select('*')

        if status_filter and status_filter != 'all':
            query = query.eq('status', status_filter)

        result = query.order('created_at', desc=True).limit(limit).execute()

        return jsonify({
            'success': True,
            'requests': result.data or [],
            'count': len(result.data) if result.data else 0,
            'note': '適用するには: python scripts/ops.py requests --apply'
        })

    except Exception as e:
        # ops_requests テーブルが存在しない場合
        if 'relation' in str(e).lower() or 'does not exist' in str(e).lower():
            return jsonify({
                'success': True,
                'requests': [],
                'count': 0,
                'note': 'ops_requests テーブル未作成（マイグレーション未実行）'
            })
        logger.error(f"ops_requests 取得エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/ops/request-stop', methods=['POST'])
def request_stop():
    """停止要求を ops_requests に登録

    【責務】
    - ops_requests テーブルに STOP 要求を INSERT
    - Worker が ExecutionPolicy を通じてこれを検出して停止
    - Web は Worker を直接停止しない（できない）
    """
    import json

    try:
        db = DatabaseClient()
        data = request.get_json() or {}

        workspace = data.get('workspace')
        reason = data.get('reason', 'Web UIからの停止要求')

        scope_type = 'workspace' if workspace else 'global'
        scope_id = workspace

        # ops_requests に登録を試みる
        try:
            result = db.client.table('ops_requests').insert({
                'request_type': 'STOP',
                'scope_type': scope_type,
                'scope_id': scope_id,
                'requested_by': 'web_api',
                'payload': json.dumps({'reason': reason})
            }).execute()

            if result.data:
                return jsonify({
                    'success': True,
                    'message': '停止要求を登録しました',
                    'request_id': result.data[0]['id'],
                    'note': 'Worker が次の処理開始時に停止します'
                })
        except Exception:
            pass  # ops_requests がない場合はフォールバック

        # フォールバック: 旧方式（worker_state.stop_requested）
        db.client.table('worker_state').update({
            'stop_requested': True
        }).eq('id', 1).execute()

        return jsonify({
            'success': True,
            'message': '停止要求を登録しました（レガシーモード）',
            'note': 'Worker が次の lease 時に停止します'
        })

    except Exception as e:
        logger.error(f"停止要求エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/ops/request-release-lease', methods=['POST'])
def request_release_lease():
    """リース解放要求を ops_requests に登録（enqueue のみ）

    【責務】
    - ops_requests テーブルに RELEASE_LEASE 要求を INSERT
    - apply は ops.py のみが行う（Web は enqueue のみ）
    - 対象は workspace または doc_id で指定必須
    - "all" は禁止（事故防止）
    """
    import json

    try:
        db = DatabaseClient()
        data = request.get_json() or {}

        workspace = data.get('workspace')
        doc_id = data.get('doc_id')

        # スコープ必須チェック
        if not workspace and not doc_id:
            return jsonify({
                'success': False,
                'error': 'workspace または doc_id を指定してください',
                'hint': '全件解放は禁止されています（事故防止）'
            }), 400

        # "all" は禁止
        if workspace and workspace.lower() == 'all':
            return jsonify({
                'success': False,
                'error': 'workspace=all は禁止されています',
                'hint': '全件解放は事故の原因になります。特定のワークスペースを指定してください。'
            }), 400

        scope_type = 'document' if doc_id else 'workspace'
        scope_id = doc_id or workspace

        # ops_requests に登録（enqueue のみ、apply しない）
        try:
            result = db.client.table('ops_requests').insert({
                'request_type': 'RELEASE_LEASE',
                'scope_type': scope_type,
                'scope_id': scope_id,
                'requested_by': 'web_api',
                'payload': json.dumps({'force': data.get('force', False)})
            }).execute()

            if result.data:
                return jsonify({
                    'success': True,
                    'message': 'リース解放要求を登録しました（SSOT: ops_requests）',
                    'request_id': result.data[0]['id'],
                    'scope': f'{scope_type}:{scope_id}',
                    'note': '適用するには: python ops.py requests --apply'
                })
        except Exception as e:
            logger.warning(f"ops_requests への登録失敗: {e}")
            return jsonify({
                'success': False,
                'error': 'ops_requests テーブルが存在しません',
                'hint': 'マイグレーションを実行してください: database/migrations/create_ops_requests.sql'
            }), 500

        return jsonify({
            'success': False,
            'error': '要求の登録に失敗しました'
        }), 500

    except Exception as e:
        logger.error(f"リース解放エラー: {e}")
        return safe_error_response(e)


# ========== キュー操作（新方式） ==========
#
# 【設計原則】
# - ops_requests/run_executions は使わない
# - pending → queued → processing → completed/failed
# - 1件ずつ独立（束ねない）

@app.route('/internal/run-requests', methods=['GET'])
def get_queue_status_api():
    """キュー状態とqueuedドキュメント一覧を取得"""
    try:
        db = DatabaseClient(use_service_role=True)
        workspace = request.args.get('workspace', 'all')

        # キュー状態を取得
        status_result = db.client.rpc('get_queue_status', {
            'p_workspace': workspace
        }).execute()

        status_data = {}
        if status_result.data:
            data = status_result.data[0] if isinstance(status_result.data, list) else status_result.data
            status_data = {
                'pending': data.get('pending_count', 0),
                'queued': data.get('queued_count', 0),
                'processing': data.get('processing_count', 0),
                'completed': data.get('completed_count', 0),
                'failed': data.get('failed_count', 0)
            }

        # queuedドキュメント一覧を取得
        query = db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, title, file_name, workspace, doc_type, attempt_count, created_at'
        ).eq('processing_status', 'queued')

        if workspace and workspace != 'all':
            query = query.eq('workspace', workspace)

        queued_result = query.order('created_at', desc=False).limit(100).execute()
        queued_docs = queued_result.data or []

        return jsonify({
            'success': True,
            'status': status_data,
            'queued_docs': queued_docs,
            'count': len(queued_docs)
        })

    except Exception as e:
        logger.error(f"キュー状態取得エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/clear', methods=['POST'])
def clear_queue_api():
    """キューをクリア: queued → pending（停止）"""
    try:
        db = DatabaseClient(use_service_role=True)
        # クエリパラメータまたはJSONボディから取得
        workspace = request.args.get('workspace') or (request.get_json() or {}).get('workspace', 'all')

        result = db.client.rpc('clear_queue', {
            'p_workspace': workspace
        }).execute()

        cleared_count = 0
        if result.data:
            data = result.data[0] if isinstance(result.data, list) else result.data
            cleared_count = data.get('cleared_count', 0)

        return jsonify({
            'success': True,
            'message': f'{cleared_count}件をキューから削除しました',
            'cleared_count': cleared_count
        })

    except Exception as e:
        logger.error(f"キュークリアエラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/execute', methods=['POST'])
def execute_queue():
    """キュー内ドキュメントを処理（Worker をバックグラウンドで起動）

    【動作】
    - process_queued_documents.py をサブプロセスで起動
    - queued ステータスのドキュメントを処理
    - バックグラウンド実行（レスポンスは即座に返す）
    """
    try:
        data = request.get_json() or {}
        workspace = data.get('workspace', 'all')
        limit = min(int(data.get('limit', 10)), 100)

        # Worker スクリプトのパス
        worker_script = _project_root / 'scripts' / 'processing' / 'process_queued_documents.py'

        if not worker_script.exists():
            return jsonify({
                'success': False,
                'error': f'Worker スクリプトが見つかりません: {worker_script}'
            }), 500

        # バックグラウンドでWorkerを起動
        cmd = [
            sys.executable,
            str(worker_script),
            f'--workspace={workspace}',
            f'--limit={limit}',
            '--execute'
        ]

        logger.info(f"Worker 起動: {' '.join(cmd)}")

        # ログファイルに出力をリダイレクト
        log_dir = _project_root / 'logs' / 'worker'
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = log_dir / f'worker_{timestamp}.log'

        # ヘッダーを書き込み
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"# Worker started at {datetime.now()}\n")
            f.write(f"# Command: {' '.join(cmd)}\n\n")

        # Popen でバックグラウンド実行（ファイルは subprocess に任せる）
        import platform
        log_handle = open(log_file, 'a', encoding='utf-8')
        if platform.system() == 'Windows':
            process = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(_project_root),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            process = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(_project_root),
                start_new_session=True
            )
        # 注意: log_handle は subprocess が終了するまで開いたまま（意図的）

        return jsonify({
            'success': True,
            'message': f'Worker を起動しました（PID: {process.pid}）',
            'pid': process.pid,
            'workspace': workspace,
            'limit': limit,
            'log_file': str(log_file)
        })

    except Exception as e:
        logger.error(f"Worker 起動エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/remove/<doc_id>', methods=['POST'])
@app.route('/internal/run-requests/<doc_id>', methods=['DELETE'])
def remove_from_queue(doc_id):
    """特定ドキュメントをキューから削除: queued → pending"""
    try:
        db = DatabaseClient(use_service_role=True)

        # queued状態のドキュメントをpendingに戻す
        result = db.client.table('Rawdata_FILE_AND_MAIL').update({
            'processing_status': 'pending'
        }).eq('id', doc_id).eq('processing_status', 'queued').execute()

        if result.data:
            return jsonify({
                'success': True,
                'message': 'キューから削除しました',
                'doc_id': doc_id
            })
        else:
            return jsonify({
                'success': False,
                'error': 'ドキュメントが見つからないか、既にキューにありません'
            }), 404

    except Exception as e:
        logger.error(f"キューから削除エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/run-requests', methods=['POST'])
def create_run_request():
    """キューに追加: pending → queued（新方式）

    【新方式】
    - ops_requests/run_executions は使わない
    - 直接 enqueue_documents RPC を呼び出す
    - 1件ずつ独立（束ねない）

    【パラメータ】
    - limit / max_items: 追加件数（デフォルト10、上限100）
    - workspace: 対象ワークスペース（省略時は全体）
    - doc_ids: 特定ドキュメントID配列（省略時は自動選択）
    - doc_id: 特定ドキュメント1件（doc_ids優先）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json() or {}

        # パラメータ取得（limit と max_items 両方対応）
        limit = min(int(data.get('limit', data.get('max_items', 10))), 100)
        workspace = data.get('workspace') or 'all'

        # doc_ids 配列優先、なければ doc_id を配列に
        doc_ids = data.get('doc_ids')
        if not doc_ids and data.get('doc_id'):
            doc_ids = [data.get('doc_id')]

        # enqueue_documents RPC を呼び出し
        if doc_ids:
            # 特定ドキュメント指定
            result = db.client.rpc('enqueue_documents', {
                'p_workspace': workspace,
                'p_limit': len(doc_ids),
                'p_doc_ids': doc_ids
            }).execute()
        else:
            # 自動選択
            result = db.client.rpc('enqueue_documents', {
                'p_workspace': workspace,
                'p_limit': limit,
                'p_doc_ids': None
            }).execute()

        if result.data:
            data = result.data[0] if isinstance(result.data, list) else result.data
            enqueued_count = data.get('enqueued_count', 0)
            doc_ids = data.get('doc_ids', []) or []

            return jsonify({
                'success': True,
                'message': f'{enqueued_count}件をキューに追加しました',
                'enqueued_count': enqueued_count,
                'doc_ids': doc_ids,
                'note': 'Workerがqueuedを順番に処理します'
            })

        return jsonify({
            'success': False,
            'error': 'キューへの追加に失敗しました'
        }), 500

    except Exception as e:
        logger.error(f"RUN 要求作成エラー: {e}")
        return safe_error_response(e)


# ========== エントリーポイント ==========

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
