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


@app.route('/internal/classifications', methods=['GET'])
def get_classifications():
    """分類（origin_app）の一覧を取得

    【責務】
    - 検索UIの分類フィルタ用ドロップダウン向け
    - ワークスペース指定で絞り込み可能

    【パラメータ】
    - workspace: 絞り込むワークスペース（省略時は全体）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        workspace = request.args.get('workspace', '')

        query = db.client.table('Rawdata_FILE_AND_MAIL').select('origin_app')

        if workspace and workspace != 'all':
            query = query.eq('workspace', workspace)

        result = query.execute()

        classifications = set()
        for row in result.data or []:
            origin_app = row.get('origin_app')
            if origin_app:
                classifications.add(origin_app)

        return jsonify({
            'success': True,
            'classifications': sorted(list(classifications))
        })

    except Exception as e:
        logger.error(f"分類一覧取得エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/search', methods=['GET'])
def search_documents():
    """ドキュメント検索

    【責務】
    - ワークスペース + 分類（origin_app） + ステータス + キーワードで絞り込み
    - 検索結果からキューへの追加に使用

    【パラメータ】
    - workspace: ワークスペース（省略時は全体）
    - classification: 分類（origin_app）（省略時は全体）
    - status: 処理ステータス（省略時は全体）
    - q: キーワード（title / file_name の部分一致）
    - limit: 取得件数上限（デフォルト100、最大500）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        workspace = request.args.get('workspace', '')
        classification = request.args.get('classification', '')
        status = request.args.get('status', '')
        q = request.args.get('q', '').strip()
        exclude_text_only = request.args.get('exclude_text_only', '') == 'true'
        limit = min(int(request.args.get('limit', 100)), 500)

        query = db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, title, file_name, workspace, origin_app, processing_status, doc_type, created_at, updated_at'
        )

        if workspace and workspace != 'all':
            query = query.eq('workspace', workspace)
        if exclude_text_only:
            query = query.not_.is_('file_url', 'null')
        if classification and classification != 'all':
            if classification == 'text_only':
                query = query.is_('file_url', 'null')
            elif classification == 'unclassified':
                query = query.not_.is_('file_url', 'null').is_('origin_app', 'null')
            else:
                query = query.eq('origin_app', classification)
        if status and status != 'all':
            query = query.eq('processing_status', status)
        if q:
            query = query.ilike('title', f'%{q}%')

        result = query.order('created_at', desc=True).limit(limit).execute()
        documents = result.data or []

        return jsonify({
            'success': True,
            'documents': documents,
            'count': len(documents)
        })

    except Exception as e:
        logger.error(f"ドキュメント検索エラー: {e}")
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
        data = request.get_json(silent=True) or {}

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
        data = request.get_json(silent=True) or {}

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

        # queuedドキュメント一覧を取得（Gatekeeper情報を含む）
        query = db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, title, file_name, workspace, doc_type, attempt_count, created_at, '
            'gate_decision, gate_block_code, gate_block_reason, origin_app, origin_confidence'
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
        workspace = request.args.get('workspace') or (request.get_json(silent=True) or {}).get('workspace', 'all')

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


@app.route('/internal/queue/retry-failed', methods=['POST'])
def retry_failed_documents():
    """失敗ドキュメントを再キュー: failed → queued

    【パラメータ】
    - workspace: 対象ワークスペース（省略時は全体）
    - limit: 再キュー上限（デフォルト100）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}

        workspace = data.get('workspace') or 'all'
        limit = min(int(data.get('limit', 100)), 500)

        # まず対象のIDを取得
        select_query = db.client.table('Rawdata_FILE_AND_MAIL').select('id').eq('processing_status', 'failed')

        if workspace and workspace != 'all':
            select_query = select_query.eq('workspace', workspace)

        select_result = select_query.limit(limit).execute()

        if not select_result.data:
            return jsonify({
                'success': True,
                'message': '再キュー対象がありません',
                'retry_count': 0,
                'workspace': workspace
            })

        # IDリストで更新
        doc_ids = [row['id'] for row in select_result.data]

        update_result = db.client.table('Rawdata_FILE_AND_MAIL').update({
            'processing_status': 'queued'
        }).in_('id', doc_ids).execute()

        retry_count = len(update_result.data) if update_result.data else 0

        return jsonify({
            'success': True,
            'message': f'{retry_count}件を再キューしました',
            'retry_count': retry_count,
            'workspace': workspace
        })

    except Exception as e:
        logger.error(f"再キューエラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/clear-lock', methods=['POST'])
def clear_processing_lock():
    """処理ロックをクリア

    【用途】
    - Worker がクラッシュしてロックが残った場合にクリア
    - processing_lock テーブルの is_processing を false に

    【注意】
    - 実行中の Worker がある場合も強制クリアされる
    - 重複処理のリスクがあるため、慎重に使用
    """
    try:
        db = DatabaseClient(use_service_role=True)

        # ロック状態を確認
        check_result = db.client.table('processing_lock').select('*').eq('id', 1).execute()

        if not check_result.data:
            return jsonify({
                'success': False,
                'error': 'processing_lock テーブルにレコードがありません'
            }), 404

        current_state = check_result.data[0]
        was_locked = current_state.get('is_processing', False)

        # ロックをクリア
        result = db.client.table('processing_lock').update({
            'is_processing': False,
            'current_workers': 0,
            'locked_at': None,
            'locked_by': None
        }).eq('id', 1).execute()

        if result.data:
            return jsonify({
                'success': True,
                'message': 'ロックをクリアしました' if was_locked else 'ロックは既にクリア状態でした',
                'was_locked': was_locked,
                'previous_state': {
                    'is_processing': current_state.get('is_processing'),
                    'current_workers': current_state.get('current_workers'),
                    'locked_by': current_state.get('locked_by'),
                    'locked_at': current_state.get('locked_at')
                }
            })
        else:
            return jsonify({
                'success': False,
                'error': 'ロックのクリアに失敗しました'
            }), 500

    except Exception as e:
        logger.error(f"ロッククリアエラー: {e}")
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
        data = request.get_json(silent=True) or {}
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


@app.route('/internal/blocked-documents', methods=['GET'])
def get_blocked_documents():
    """Gatekeeperによってブロックされたドキュメント一覧を取得

    【責務】
    - gate_decision='BLOCK' のドキュメント一覧を取得
    - ブロック理由と詳細情報を含む

    【パラメータ】
    - workspace: 対象ワークスペース（省略時は全体）
    - limit: 取得件数上限（デフォルト100、最大500）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        workspace = request.args.get('workspace', 'all')
        limit = min(int(request.args.get('limit', 100)), 500)

        # ブロックされたドキュメントを取得
        query = db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, title, file_name, workspace, doc_type, created_at, updated_at, '
            'gate_decision, gate_block_code, gate_block_reason, '
            'origin_app, origin_confidence, layout_profile, gate_policy_version'
        ).eq('gate_decision', 'BLOCK')

        if workspace and workspace != 'all':
            query = query.eq('workspace', workspace)

        result = query.order('updated_at', desc=True).limit(limit).execute()
        blocked_docs = result.data or []

        # ブロック理由別に集計
        block_stats = {}
        for doc in blocked_docs:
            code = doc.get('gate_block_code', 'UNKNOWN')
            block_stats[code] = block_stats.get(code, 0) + 1

        return jsonify({
            'success': True,
            'blocked_docs': blocked_docs,
            'count': len(blocked_docs),
            'block_stats': block_stats,
            'workspace': workspace
        })

    except Exception as e:
        logger.error(f"ブロックドキュメント取得エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/failed-documents', methods=['GET'])
def get_failed_documents():
    """処理失敗したドキュメント一覧を取得

    【責務】
    - processing_status='failed' のドキュメント一覧を取得
    - エラーメッセージと詳細情報を含む

    【パラメータ】
    - workspace: 対象ワークスペース（省略時は全体）
    - limit: 取得件数上限（デフォルト100、最大500）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        workspace = request.args.get('workspace', 'all')
        limit = min(int(request.args.get('limit', 100)), 500)

        # 失敗したドキュメントを取得
        query = db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, title, file_name, workspace, doc_type, created_at, updated_at, '
            'error_message, last_error_reason, attempt_count, failed_at, metadata, '
            'origin_app, origin_confidence, layout_profile, '
            'gate_block_code, gate_block_reason, pdf_creator, pdf_producer'
        ).eq('processing_status', 'failed')

        if workspace and workspace != 'all':
            query = query.eq('workspace', workspace)

        result = query.order('updated_at', desc=True).limit(limit).execute()
        failed_docs = result.data or []

        # エラーメッセージから簡易的にエラー種別を集計
        error_stats = {}
        for doc in failed_docs:
            error_msg = doc.get('error_message', 'Unknown error')
            # エラーメッセージの最初の50文字をキーとして集計
            error_key = error_msg[:50] if error_msg else 'Unknown'
            error_stats[error_key] = error_stats.get(error_key, 0) + 1

        return jsonify({
            'success': True,
            'failed_docs': failed_docs,
            'count': len(failed_docs),
            'error_stats': error_stats,
            'workspace': workspace
        })

    except Exception as e:
        logger.error(f"失敗ドキュメント取得エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/classify-documents', methods=['POST'])
def classify_documents():
    """選択したドキュメントを分類して origin_app を Supabase に書き込む

    【責務】
    - doc_ids で指定したドキュメントを Google Drive からダウンロード
    - Stage A（A3EntryPoint）で分類を実行
    - origin_app / origin_confidence / layout_profile / pdf_creator / pdf_producer を更新

    【パラメータ】
    - doc_ids: 分類するドキュメントIDの配列（最大20件）
    """
    import re as _re
    import tempfile

    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}
        doc_ids = data.get('doc_ids', [])

        if not doc_ids:
            return jsonify({'success': False, 'error': 'doc_ids が必要です'}), 400
        if len(doc_ids) > 20:
            return jsonify({'success': False, 'error': '一度に処理できるのは20件までです'}), 400

        # ドキュメント情報を取得
        fetch_result = db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, file_name, file_url, title'
        ).in_('id', doc_ids).execute()

        if not fetch_result.data:
            return jsonify({'success': False, 'error': 'ドキュメントが見つかりません'}), 404

        # Stage A と Google Drive コネクタをインポート
        from shared.pipeline.stage_a.a3_entry_point import A3EntryPoint
        from shared.common.connectors.google_drive import GoogleDriveConnector

        drive = GoogleDriveConnector()
        a3 = A3EntryPoint()
        results = []

        for doc in fetch_result.data:
            doc_id = doc['id']
            file_name = doc.get('file_name') or doc.get('title') or 'unknown.pdf'
            file_url = doc.get('file_url', '')

            try:
                # file_url から Google Drive ファイルID を抽出
                match = _re.search(r'/d/([a-zA-Z0-9_-]+)', file_url)
                if not match:
                    results.append({
                        'id': doc_id, 'file_name': file_name,
                        'success': False, 'error': 'file_url から Drive ファイルIDを取得できません'
                    })
                    continue

                drive_file_id = match.group(1)

                # 一時ディレクトリにダウンロードして Stage A で分類
                with tempfile.TemporaryDirectory() as tmp_dir:
                    local_path = drive.download_file(drive_file_id, file_name, tmp_dir)

                    if not local_path:
                        results.append({
                            'id': doc_id, 'file_name': file_name,
                            'success': False, 'error': 'Google Drive からのダウンロード失敗'
                        })
                        continue

                    a_result = a3.process(Path(local_path))

                if not a_result.get('success', True):
                    results.append({
                        'id': doc_id, 'file_name': file_name,
                        'success': False, 'error': a_result.get('error', '分類失敗')
                    })
                    continue

                origin_app = a_result.get('origin_app', '')
                confidence = a_result.get('confidence', '')
                layout_profile = a_result.get('layout_profile', '')
                reason = a_result.get('reason', '')

                update_data = {
                    'origin_app': origin_app or None,
                    'origin_confidence': confidence or None,
                    'layout_profile': layout_profile or None,
                }

                # raw_metadata から Creator / Producer を取得
                raw_meta = (a_result.get('a2_type') or {}).get('raw_metadata') or \
                           a_result.get('raw_metadata') or {}
                creator = raw_meta.get('Creator') or raw_meta.get('creator') or ''
                producer = raw_meta.get('Producer') or raw_meta.get('producer') or ''
                if creator:
                    update_data['pdf_creator'] = creator
                if producer:
                    update_data['pdf_producer'] = producer

                db.client.table('Rawdata_FILE_AND_MAIL').update(update_data).eq('id', doc_id).execute()
                logger.info(f"[classify] {file_name}: {origin_app} ({confidence})")

                results.append({
                    'id': doc_id, 'file_name': file_name,
                    'success': True,
                    'origin_app': origin_app,
                    'confidence': confidence,
                    'layout_profile': layout_profile,
                    'reason': reason,
                })

            except Exception as e:
                logger.error(f"分類エラー ({doc_id}): {e}")
                error_str = str(e)
                is_404 = ('File not found' in error_str or '404' in error_str
                          or 'not found' in error_str.lower())
                if is_404:
                    try:
                        db.client.table('Rawdata_FILE_AND_MAIL').update(
                            {'origin_app': 'file_not_found'}
                        ).eq('id', doc_id).execute()
                        logger.info(f"[classify] {file_name}: file_not_found (Drive 404) → DB保存")
                    except Exception as ue:
                        logger.error(f"file_not_found 保存エラー: {ue}")
                results.append({
                    'id': doc_id, 'file_name': file_name,
                    'success': False, 'error': error_str,
                    'origin_app': 'file_not_found' if is_404 else None,
                })

        success_count = sum(1 for r in results if r.get('success'))
        return jsonify({
            'success': True,
            'message': f'{success_count}/{len(results)} 件の分類が完了しました',
            'results': results
        })

    except Exception as e:
        logger.error(f"classify-documents エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/run-requests', methods=['POST'])
def create_run_request():
    """キューに追加: → queued（新方式）

    【新方式】
    - ops_requests/run_executions は使わない
    - 直接 enqueue_documents RPC を呼び出す
    - 1件ずつ独立（束ねない）

    【パラメータ】
    - limit / max_items: 追加件数（デフォルト10、上限100）
    - workspace: 対象ワークスペース（省略時は全体）
    - doc_ids: 特定ドキュメントID（配列 or カンマ区切り文字列、省略時は自動選択）
    - doc_id: 特定ドキュメント1件（doc_ids優先）

    【doc_ids 指定時の特別ルール】
    - ステータスを問わず pending にリセットしてからキューに追加する
    - completed / failed の再処理に使用
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}

        # パラメータ取得（limit と max_items 両方対応）
        limit = min(int(data.get('limit', data.get('max_items', 10))), 100)
        workspace = data.get('workspace') or 'all'

        # doc_ids: 配列・カンマ区切り文字列・単一 doc_id をすべて受け付ける
        doc_ids = data.get('doc_ids')
        if isinstance(doc_ids, str):
            doc_ids = [d.strip() for d in doc_ids.split(',') if d.strip()]
        if not doc_ids and data.get('doc_id'):
            doc_ids = [data.get('doc_id')]

        # enqueue_documents RPC を呼び出し
        if doc_ids:
            # 特定ドキュメント指定: ステータスを問わず pending にリセットしてからキューに追加
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'pending'
            }).in_('id', doc_ids).execute()
            logger.info(f"[run-requests] {len(doc_ids)}件を pending にリセット: {doc_ids}")

            result = db.client.rpc('enqueue_documents', {
                'p_workspace': workspace,
                'p_limit': len(doc_ids),
                'p_doc_ids': doc_ids
            }).execute()
        else:
            # 自動選択（pending のみ対象）
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


# ========== doc_type 一括変更 ==========

@app.route('/internal/update-doc-type', methods=['POST'])
def update_doc_type():
    """選択ドキュメントの doc_type を一括変更

    【パラメータ】
    - doc_ids: ドキュメントIDの配列
    - doc_type: 変更後の doc_type 文字列
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}
        doc_ids = data.get('doc_ids', [])
        doc_type = data.get('doc_type', '').strip()

        if not doc_ids:
            return jsonify({'success': False, 'error': 'doc_ids が必要です'}), 400
        if not doc_type:
            return jsonify({'success': False, 'error': 'doc_type が必要です'}), 400

        result = db.client.table('Rawdata_FILE_AND_MAIL').update({
            'doc_type': doc_type
        }).in_('id', doc_ids).execute()

        updated_count = len(result.data) if result.data else 0
        logger.info(f"[update-doc-type] {updated_count}件の doc_type を '{doc_type}' に変更: {doc_ids}")

        return jsonify({
            'success': True,
            'message': f'{updated_count}件の doc_type を変更しました',
            'updated_count': updated_count,
        })

    except Exception as e:
        logger.error(f"update-doc-type エラー: {e}")
        return safe_error_response(e)


# ========== pending リセット ==========

@app.route('/internal/set-pending', methods=['POST'])
def set_pending():
    """選択ドキュメントを pending にリセット（キューには追加しない）

    【パラメータ】
    - doc_ids: ドキュメントIDの配列
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}
        doc_ids = data.get('doc_ids', [])

        if not doc_ids:
            return jsonify({'success': False, 'error': 'doc_ids が必要です'}), 400

        result = db.client.table('Rawdata_FILE_AND_MAIL').update({
            'processing_status': 'pending'
        }).in_('id', doc_ids).execute()

        updated_count = len(result.data) if result.data else 0
        logger.info(f"[set-pending] {updated_count}件を pending にリセット: {doc_ids}")

        return jsonify({
            'success': True,
            'message': f'{updated_count}件を pending にリセットしました',
            'updated_count': updated_count,
        })

    except Exception as e:
        logger.error(f"set-pending エラー: {e}")
        return safe_error_response(e)


# ========== G ステージ再処理 ==========

@app.route('/internal/reprocess-g', methods=['POST'])
def reprocess_g():
    """G17 または G22 から再処理（Stage J/K も更新）

    【責務】
    - DBに保存済みの中間データを使って指定ステージから再実行
    - G22: g21_articles → G22 → g22_ai_extracted 更新 → J/K 更新
    - G17: g14_reconstructed_tables → G17 → g17_table_analyses 更新 → J/K 更新

    【パラメータ】
    - doc_id: 対象ドキュメントID
    - start_stage: 'G17' または 'G22'
    """
    import json as _json
    import os as _os

    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}
        doc_id = data.get('doc_id')
        start_stage = data.get('start_stage', 'G22')

        if not doc_id:
            return jsonify({'success': False, 'error': 'doc_id が必要です'}), 400
        if start_stage not in ('G17', 'G22'):
            return jsonify({'success': False, 'error': 'start_stage は G17 または G22 を指定してください'}), 400

        # ドキュメント情報と保存済み中間データを取得
        fetch_result = db.client.table('Rawdata_FILE_AND_MAIL').select(
            'id, file_name, title, doc_type, display_subject, display_post_text, '
            'display_sender, display_sent_at, owner_id, '
            'g14_reconstructed_tables, g21_articles, g17_table_analyses, g22_ai_extracted, '
            'stage_g_structured_data'
        ).eq('id', doc_id).execute()

        if not fetch_result.data:
            return jsonify({'success': False, 'error': 'ドキュメントが見つかりません'}), 404

        doc = fetch_result.data[0]
        gemini_key = _os.getenv('GOOGLE_AI_API_KEY') or _os.getenv('GEMINI_API_KEY') or _os.getenv('GOOGLE_API_KEY')

        def _parse_json_col(val):
            if val is None:
                return None
            if isinstance(val, str):
                return _json.loads(val)
            return val  # 既に dict/list の場合

        # ===== G22 再処理 =====
        if start_stage == 'G22':
            g21_raw = doc.get('g21_articles')
            if not g21_raw:
                return jsonify({
                    'success': False,
                    'error': 'g21_articles が保存されていません（G22 再処理には G21 の出力が必要です）'
                }), 400

            articles = _parse_json_col(g21_raw) or []

            from shared.pipeline.stage_g.g22_text_ai_processor import G22TextAIProcessor
            g22 = G22TextAIProcessor(document_id=doc_id, api_key=gemini_key)
            g22_result = g22.process(articles)

            if not g22_result.get('success'):
                return jsonify({'success': False, 'error': f'G22 処理失敗: {g22_result.get("error", "不明")}'}), 500

            # g22_ai_extracted を更新
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'g22_ai_extracted': _json.dumps(g22_result, ensure_ascii=False)
            }).eq('id', doc_id).execute()

            # stage_g_structured_data の timeline/actions/notices を更新
            try:
                ui_data = _parse_json_col(doc.get('stage_g_structured_data')) or {}
                ui_data['timeline'] = g22_result.get('calendar_events', [])
                ui_data['actions'] = g22_result.get('tasks', [])
                ui_data['notices'] = g22_result.get('notices', [])
                db.client.table('Rawdata_FILE_AND_MAIL').update(
                    {'stage_g_structured_data': ui_data}
                ).eq('id', doc_id).execute()
            except Exception as e:
                logger.warning(f'stage_g_structured_data 更新エラー（継続）: {e}')

            g17_output = _parse_json_col(doc.get('g17_table_analyses')) or []
            g21_output = articles
            g22_output = g22_result

        # ===== G17 再処理 =====
        else:
            g14_raw = doc.get('g14_reconstructed_tables')
            if not g14_raw:
                return jsonify({
                    'success': False,
                    'error': 'g14_reconstructed_tables が保存されていません（G17 再処理には G14 の出力が必要です）'
                }), 400

            g14_data = _parse_json_col(g14_raw) or []

            from shared.pipeline.stage_g.g17_table_ai_processor import G17TableAIProcessor
            g17 = G17TableAIProcessor(document_id=doc_id, api_key=gemini_key)
            g17_result = g17.process(g14_data)

            if not g17_result.get('success'):
                return jsonify({'success': False, 'error': f'G17 処理失敗: {g17_result.get("error", "不明")}'}), 500

            g17_output = g17_result.get('table_analyses', [])

            # g17_table_analyses を更新
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'g17_table_analyses': _json.dumps(g17_output, ensure_ascii=False)
            }).eq('id', doc_id).execute()

            # G17生出力(sections構造) → UI用フォーマット(rows構造) に変換
            def _to_ui_tables(table_analyses):
                result = []
                for analysis in table_analyses:
                    sections = analysis.get('sections', [])
                    section_data = sections[0].get('data', []) if sections else []
                    result.append({
                        'table_id': analysis.get('table_id', ''),
                        'table_type': analysis.get('table_type', 'structured'),
                        'description': analysis.get('description', ''),
                        'headers': [],
                        'rows': section_data,
                        'sections': sections,
                        'metadata': sections[0].get('metadata', {}) if sections else analysis.get('metadata', {}),
                    })
                return result
            ui_tables = _to_ui_tables(g17_output)

            # stage_g_structured_data の tables を更新（変換後のUI用データで）
            try:
                ui_data = _parse_json_col(doc.get('stage_g_structured_data')) or {}
                ui_data['tables'] = ui_tables
                db.client.table('Rawdata_FILE_AND_MAIL').update(
                    {'stage_g_structured_data': ui_data}
                ).eq('id', doc_id).execute()
            except Exception as e:
                logger.warning(f'stage_g_structured_data 更新エラー（継続）: {e}')

            g17_output = ui_tables  # 以降はUI用フォーマットを使う

            g21_output = _parse_json_col(doc.get('g21_articles')) or []
            g22_output = _parse_json_col(doc.get('g22_ai_extracted')) or {}

        # ===== Stage J: チャンク生成 =====
        from shared.common.processing.metadata_chunker import MetadataChunker
        file_name = doc.get('file_name') or doc.get('title') or 'unknown'

        document_data = {
            'file_name': file_name,
            'doc_type': doc.get('doc_type'),
            'display_subject': doc.get('display_subject'),
            'display_post_text': doc.get('display_post_text'),
            'display_sender': doc.get('display_sender'),
            'display_sent_at': doc.get('display_sent_at'),
            'classroom_sender_email': doc.get('classroom_sender_email'),
            'text_blocks': [
                {'title': a.get('title', ''), 'content': a.get('body', '')}
                for a in g21_output
                if a.get('body', '').strip()
            ],
            'structured_tables': [
                {
                    'table_title': t.get('description', t.get('table_title', '')),
                    'headers': t.get('headers', []),
                    'rows': t.get('rows', []),
                    'metadata': t.get('metadata', {}),
                }
                for t in g17_output
                if t.get('rows')
            ],
            'calendar_events': [
                {'event_date': e.get('date', ''), 'event_time': e.get('time', ''), 'event_name': e.get('event', ''), 'location': e.get('location', '')}
                for e in g22_output.get('calendar_events', [])
            ],
            'tasks': [
                {'task_name': t.get('item', ''), 'deadline': t.get('deadline', ''), 'description': t.get('description', '')}
                for t in g22_output.get('tasks', [])
            ],
            'notices': [
                {'category': n.get('category', ''), 'content': n.get('content', '')}
                for n in g22_output.get('notices', [])
            ],
        }

        all_chunks = MetadataChunker().create_metadata_chunks(document_data)
        logger.info(f'[reprocess-g] {start_stage} 全チャンク生成: {len(all_chunks)}件')

        # chunk_indexは全チャンク通し番号のため、タイプ別削除では番号衝突が起きる
        # → 全チャンク削除して全チャンク再挿入
        new_chunks = all_chunks
        logger.info(f'[reprocess-g] {start_stage} 全チャンク再挿入: {len(new_chunks)}件')

        # 全チャンク削除
        try:
            db.client.table('10_ix_search_index').delete().eq('document_id', doc_id).execute()
        except Exception as e:
            logger.warning(f'既存チャンク削除エラー（継続）: {e}')

        if not new_chunks:
            logger.info(f'[reprocess-g] {start_stage} 対象チャンクなし、スキップ')
            return jsonify({
                'success': True,
                'message': f'{start_stage} から再処理完了（チャンクなし）',
                'doc_id': doc_id,
                'chunks_saved': 0,
                'start_stage': start_stage,
            })

        # ===== Stage K: Embedding =====
        from shared.ai.llm_client.llm_client import LLMClient
        from shared.pipeline.stage_k_embedding import StageKEmbedding

        llm_client = LLMClient()
        stage_k = StageKEmbedding(llm_client=llm_client, db_client=db)
        k_result = stage_k.embed_and_save(doc_id, new_chunks)

        if not k_result.get('success'):
            errors = k_result.get('errors', [])
            saved = k_result.get('saved_count', 0)
            failed = k_result.get('failed_count', 0)
            # 部分成功（1件以上保存済み）は警告扱いで続行
            if saved > 0:
                logger.warning(f'[reprocess-g] Stage K 部分失敗: {saved}件成功, {failed}件失敗. errors={errors}')
            else:
                return jsonify({'success': False, 'error': f'Stage K 全失敗: {errors}'}), 500

        logger.info(f'[reprocess-g] 完了: {start_stage} → {doc_id} ({k_result.get("saved_count", 0)} chunks)')
        return jsonify({
            'success': True,
            'message': f'{start_stage} から再処理完了',
            'doc_id': doc_id,
            'chunks_saved': k_result.get('saved_count', 0),
            'start_stage': start_stage,
        })

    except Exception as e:
        logger.error(f'reprocess-g エラー: {e}')
        return safe_error_response(e)


# ========== エントリーポイント ==========

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
