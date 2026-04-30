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
import logging
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from loguru import logger
from shared.pipeline.pipeline_manager import PipelineManager

# ロガー設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# パス設定（Docker/ローカル両対応）
_file_dir = Path(__file__).resolve().parent
_project_root = _file_dir.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.environ.setdefault('PROJECT_ROOT', str(_project_root))

_rag_prepare_dir = _project_root / "services" / "rag-prepare"
if str(_rag_prepare_dir) not in sys.path:
    sys.path.insert(0, str(_rag_prepare_dir))

# DB クライアントのみインポート（処理系は一切インポートしない）
from shared.common.database.client import DatabaseClient
from fast_indexer import FastIndexer
from fast_index_queries import fetch_pending_fast_index_docs
from fast_index_scope import FAST_INDEX_RAW_TABLES, resolve_pdf_toolbox_base

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

# raw テーブルごとのタイトルカラム名
_RAW_TABLE_TITLE_COLUMN = {
    '01_gmail_01_raw':         'header_subject',
    '02_gcal_01_raw':          'summary',
    '05_ikuya_waseaca_01_raw': 'title',
}

def _get_document_titles(db, pm_rows):
    """raw_table からタイトルを取得する。"""
    if not pm_rows:
        return {}

    by_table = {}
    for r in pm_rows:
        raw_id = str(r.get('raw_id'))
        raw_table = r.get('raw_table')
        if not raw_id or not raw_table:
            continue
        if raw_table not in by_table:
            by_table[raw_table] = []
        by_table[raw_table].append(raw_id)

    title_map = {}
    for rt, ids in by_table.items():
        col = _RAW_TABLE_TITLE_COLUMN.get(rt)
        if not col:
            continue
        try:
            result = db.client.table(rt).select(f'id, {col}').in_('id', ids).execute()
            for r in (result.data or []):
                if r.get(col):
                    title_map[(str(r['id']), rt)] = r[col]
        except Exception as e:
            logger.error(f"{rt} タイトル取得エラー: {e}")

    return title_map



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
    """ソース（source）一覧を取得

    【責務】
    - Run Requests UI のフィルタ選択ドロップダウン用
    - pipeline_meta.source の一覧を返す

    【DBアクセス】
    - service_role を使用（RLSバイパス、他APIと統一）
    """
    try:
        db = DatabaseClient(use_service_role=True)

        result = db.client.table('pipeline_meta').select('source').execute()
        sources = sorted(set(
            r['source'] for r in (result.data or []) if r.get('source')
        ))

        return jsonify({
            'success': True,
            'workspaces': sources  # UI後方互換のため key は workspaces のまま
        })

    except Exception as e:
        logger.error(f"ソース取得エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/classifications', methods=['GET'])
def get_classifications():
    """分類（origin_app）の一覧を取得

    【責務】
    - 検索UIの分類フィルタ用ドロップダウン向け
    - source 指定で絞り込み可能

    【パラメータ】
    - source: 絞り込むソース（省略時は全体）
    - workspace: source の別名（後方互換）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        source = request.args.get('source') or request.args.get('workspace', '')

        query = db.client.table('pipeline_meta').select('origin_app')

        if source and source != 'all':
            query = query.eq('source', source)

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
    - person / source / category / classification（origin_app） + ステータス + キーワードで絞り込み
    - 検索結果からキューへの追加に使用

    【パラメータ】
    - person: 絞り込み（省略時は全体）
    - source: 絞り込み（省略時は全体）
    - category: 絞り込み（省略時は全体）
    - classification: origin_app（省略時は全体）
    - status: 処理ステータス（省略時は全体）
    - q: キーワード（09_unified_documents.title の部分一致）
    - limit: 取得件数上限（デフォルト100、最大500）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        person         = request.args.get('person', '')
        source         = request.args.get('source') or request.args.get('workspace', '')
        category       = request.args.get('category', '')
        classification = request.args.get('classification', '')
        status         = request.args.get('status', '')
        q              = request.args.get('q', '').strip()
        limit          = min(int(request.args.get('limit', 100)), 500)

        # キーワード検索: 09_unified_documents.title で raw_id を絞り込む
        raw_id_filter = None
        if q:
            try:
                ud_result = db.client.table('09_unified_documents').select('raw_id') \
                    .ilike('title', f'%{q}%').execute()
                if not ud_result.data:
                    return jsonify({'success': True, 'documents': [], 'count': 0})
                raw_id_filter = [str(r['raw_id']) for r in ud_result.data]
            except Exception as e:
                logger.warning(f"title 検索エラー: {e}")

        # pipeline_meta を検索
        query = db.client.table('pipeline_meta').select(
            'id, raw_id, raw_table, person, source, '
            'origin_app, processing_status, attempt_count, created_at, updated_at, '
            'drive_file_id'
        )

        if person and person != 'all':
            query = query.eq('person', person)
        if source and source != 'all':
            query = query.eq('source', source)
        if category and category != 'all':
            query = query.eq('category', category)
        if classification and classification != 'all':
            if classification == 'unclassified':
                query = query.is_('origin_app', 'null')
            else:
                query = query.eq('origin_app', classification)
        if status and status != 'all':
            query = query.eq('processing_status', status)
        if raw_id_filter is not None:
            query = query.in_('raw_id', raw_id_filter)

        result = query.order('created_at', desc=True).limit(limit).execute()
        pm_rows = result.data or []

        # 09_unified_documents と raw_tables からタイトルを取得
        title_map = _get_document_titles(db, pm_rows)

        documents = []
        for r in pm_rows:
            title = title_map.get((str(r.get('raw_id')), r.get('raw_table')))
            documents.append({
                'id':                r['id'],
                'title':             title,
                'file_name':         title,
                'person':            r.get('person'),
                'source':            r.get('source'),
                'origin_app':        r.get('origin_app'),
                'processing_status': r.get('processing_status'),
                'attempt_count':     r.get('attempt_count', 0),
                'created_at':        r.get('created_at'),
                'updated_at':        r.get('updated_at'),
                'drive_file_id':     r.get('drive_file_id'),
            })

        return jsonify({'success': True, 'documents': documents, 'count': len(documents)})

    except Exception as e:
        logger.error(f"ドキュメント検索エラー: {e}")
        return safe_error_response(e)

@app.route('/internal/fast_index', methods=['POST'])
def fast_index():
    """軽量版高速インデックス実行"""
    try:
        data = request.json
        pipeline_id = data.get('pipeline_id')
        if not pipeline_id:
            return jsonify({'error': 'Missing pipeline_id'}), 400

        indexer = FastIndexer()
        success, err_msg = indexer.process_document(pipeline_id)

        if success:
            return jsonify({'success': True, 'message': f'Document {pipeline_id} indexed successfully'})
        return jsonify({'success': False, 'error': err_msg or 'Indexing failed'}), 500
    except Exception as e:
        logger.error(f"Fast index API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/fast-index-ui')
def fast_index_ui():
    """軽量版プロセッサー専用画面（rag-prepare と同一一覧ロジック）"""
    db = DatabaseClient(use_service_role=True)
    list_error = None
    try:
        pending_docs, list_error = fetch_pending_fast_index_docs(
            db.client, list(FAST_INDEX_RAW_TABLES)
        )
    except Exception as e:
        logger.error(f"Failed to fetch pending docs: {e}")
        pending_docs, list_error = [], str(e)

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
        docs=pending_docs or [],
        list_error=list_error,
        pdf_toolbox_base=toolbox,
        fast_index_post_url="/internal/fast_index",
    )


# ========== 処理監視ダッシュボード ==========

@app.route('/internal/dashboard', methods=['GET'])
def get_dashboard():
    """処理監視ダッシュボード（キュー状態 + ワーカー状況）

    【責務】
    - キュー状態（pending/processing/completed/failed）の集計
    - アクティブワーカー数と各ワーカーの処理件数

    【パラメータ】
    - source: 対象ソース（省略時は全体）
    - workspace: source の別名（後方互換）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        source = request.args.get('source') or request.args.get('workspace', '') or 'all'

        def _pm_count(s):
            q = db.client.table('pipeline_meta').select('id', count='exact').eq('processing_status', s)
            if source != 'all':
                q = q.eq('source', source)
            r = q.execute()
            return r.count or 0

        pending_count    = _pm_count('pending')
        processing_count = _pm_count('processing')
        completed_count  = _pm_count('completed')
        failed_count     = _pm_count('failed')

        workers_q = db.client.table('pipeline_meta').select('lease_owner') \
            .eq('processing_status', 'processing').not_.is_('lease_owner', 'null')
        if source != 'all':
            workers_q = workers_q.eq('source', source)
        workers_result = workers_q.execute()

        worker_counts = {}
        for row in workers_result.data or []:
            owner = row.get('lease_owner')
            if owner:
                worker_counts[owner] = worker_counts.get(owner, 0) + 1
        by_worker = [
            {'worker_id': k, 'count': v}
            for k, v in sorted(worker_counts.items(), key=lambda x: -x[1])
        ]

        return jsonify({
            'success': True,
            'workspace': source,
            'queue': {
                'pending':    pending_count,
                'queued':     _pm_count('queued'),
                'processing': processing_count,
                'completed':  completed_count,
                'failed':     failed_count
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
        status_filter = request.args.get('status', 'pending')
        limit = min(int(request.args.get('limit', 100)), 500)

        query = db.client.table('pipeline_meta').select('*')

        if status_filter and status_filter != 'all':
            query = query.eq('processing_status', status_filter)

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
                'hint': 'マイグレーションを実行してください: supabase db push'
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
    """キュー状態と pending ドキュメント一覧を取得

    【パラメータ】
    - source: 対象ソース（省略時は全体）
    - workspace: source の別名（後方互換）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        source = request.args.get('source') or request.args.get('workspace', 'all')

        def _pm_count(s):
            q = db.client.table('pipeline_meta').select('id', count='exact').eq('processing_status', s)
            if source != 'all':
                q = q.eq('source', source)
            r = q.execute()
            return r.count or 0

        status_data = {
            'pending':    _pm_count('pending'),
            'queued':     _pm_count('queued'),
            'processing': _pm_count('processing'),
            'completed':  _pm_count('completed'),
            'failed':     _pm_count('failed'),
        }

        # queued 一覧を返す
        pm_q = db.client.table('pipeline_meta').select(
            'id, raw_id, raw_table, person, source, attempt_count, '
            'gate_decision, gate_block_code, origin_app, origin_confidence, created_at, processing_status'
        ).eq('processing_status', 'queued')
        if source != 'all':
            pm_q = pm_q.eq('source', source)
        pm_result = pm_q.order('created_at', desc=False).limit(100).execute()
        pm_rows = pm_result.data or []

        # 09_unified_documents と raw_tables からタイトルを取得（無題防止）
        title_map = _get_document_titles(db, pm_rows)

        queued_docs = []
        for r in pm_rows:
            title = title_map.get((str(r.get('raw_id')), r.get('raw_table')))
            queued_docs.append({
                'id':                r['id'],
                'title':             title,
                'file_name':         title,
                'source':            r.get('source'),
                'person':            r.get('person'),
                'attempt_count':     r.get('attempt_count', 0),
                'gate_decision':     r.get('gate_decision'),
                'gate_block_code':   r.get('gate_block_code'),
                'origin_app':        r.get('origin_app'),
                'created_at':        r.get('created_at'),
                'processing_status': r.get('processing_status'),
            })

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
    """キューをクリア: pending → failed（旧 clear_queue RPC は Rawdata_FILE_AND_MAIL 参照のため廃止）"""
    try:
        db = DatabaseClient(use_service_role=True)
        workspace = request.args.get('workspace') or (request.get_json(silent=True) or {}).get('workspace', 'all')

        query = db.client.table('pipeline_meta').update({
            'processing_status': 'failed',
            'last_error_reason':  'キューから手動除外',
        }).eq('processing_status', 'queued')

        if workspace and workspace != 'all':
            query = query.eq('source', workspace)

        result = query.execute()
        cleared_count = len(result.data) if result.data else 0
        logger.info(f"キュークリア: {cleared_count}件を failed に移動 (workspace={workspace})")

        return jsonify({
            'success': True,
            'message': f'{cleared_count}件をキューから除外しました',
            'cleared_count': cleared_count
        })

    except Exception as e:
        logger.error(f"キュークリアエラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/retry-failed', methods=['POST'])
def retry_failed_documents():
    """失敗ドキュメントを再試行: failed → pending

    【パラメータ】
    - source: 対象ソース（省略時は全体）
    - workspace: source の別名（後方互換）
    - limit: 再試行上限（デフォルト100）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}

        source = data.get('source') or data.get('workspace') or 'all'
        limit = min(int(data.get('limit', 100)), 500)

        select_q = db.client.table('pipeline_meta').select('id').eq('processing_status', 'failed')
        if source != 'all':
            select_q = select_q.eq('source', source)
        select_result = select_q.limit(limit).execute()

        if not select_result.data:
            return jsonify({
                'success': True,
                'message': '再試行対象がありません',
                'retry_count': 0,
                'source': source
            })

        meta_ids = [row['id'] for row in select_result.data]
        update_result = db.client.table('pipeline_meta').update({
            'processing_status': 'pending',
            'last_error_reason': None,
        }).in_('id', meta_ids).execute()
        retry_count = len(update_result.data) if update_result.data else 0

        return jsonify({
            'success': True,
            'message': f'{retry_count}件を pending に戻しました',
            'retry_count': retry_count,
            'source': source
        })

    except Exception as e:
        logger.error(f"再試行エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/clear-lock', methods=['POST'])
def clear_processing_lock():
    """処理ロッククリア（processing_lock テーブル廃止により無効）"""
    return jsonify({'success': True, 'message': 'processing_lock は廃止済みです。操作不要です。'})


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
        source = data.get('source') or workspace
        cmd = [
            sys.executable,
            str(worker_script),
            f'--limit={limit}',
            '--execute'
        ]
        if source and source != 'all':
            cmd.insert(2, f'--source={source}')

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
        worker_env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        log_handle = open(log_file, 'a', encoding='utf-8')
        if platform.system() == 'Windows':
            process = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(_project_root),
                env=worker_env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            process = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(_project_root),
                env=worker_env,
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
    """特定ドキュメントを pending にリセット（doc_id は pipeline_meta.id）"""
    try:
        db = DatabaseClient(use_service_role=True)

        result = db.client.table('pipeline_meta').update({
            'processing_status': 'pending'
        }).eq('id', doc_id).execute()

        if result.data:
            return jsonify({
                'success': True,
                'message': 'pending にリセットしました',
                'doc_id': doc_id
            })
        else:
            return jsonify({
                'success': False,
                'error': 'ドキュメントが見つかりません'
            }), 404

    except Exception as e:
        logger.error(f"pending リセットエラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/skip-all', methods=['POST'])
def skip_all_from_queue():
    """pending 全件を failed に移動してキューリストから除外。失敗一覧から再実行可能。"""
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}
        workspace = data.get('workspace') or data.get('source')

        query = db.client.table('pipeline_meta').update({
            'processing_status': 'failed',
            'last_error_reason':  'キューから手動除外',
        }).eq('processing_status', 'queued')

        if workspace and workspace != 'all':
            query = query.eq('source', workspace)

        result = query.execute()
        skipped_count = len(result.data) if result.data else 0
        logger.info(f"キュー全件除外: {skipped_count}件 (workspace={workspace})")
        return jsonify({'success': True, 'skipped_count': skipped_count})

    except Exception as e:
        logger.error(f"キュー全件除外エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/queue/skip/<doc_id>', methods=['POST'])
def skip_from_queue(doc_id):
    """pending アイテムをキューから除外（failed にセット）。失敗一覧から再実行可能。"""
    try:
        db = DatabaseClient(use_service_role=True)

        result = db.client.table('pipeline_meta').update({
            'processing_status': 'failed',
            'last_error_reason':  'キューから手動除外',
        }).eq('id', doc_id).eq('processing_status', 'queued').execute()

        if result.data:
            logger.info(f"キューから除外（failed）: {doc_id}")
            return jsonify({'success': True, 'doc_id': doc_id})
        else:
            return jsonify({'success': False, 'error': 'レコードが見つからないか既に処理中です'}), 404

    except Exception as e:
        logger.error(f"キュー除外エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/pipeline-meta/<doc_id>', methods=['DELETE'])
def delete_pipeline_meta(doc_id):
    """pipeline_meta レコードを完全削除（doc_id は pipeline_meta.id）"""
    try:
        db = DatabaseClient(use_service_role=True)

        result = db.client.table('pipeline_meta').delete().eq('id', doc_id).execute()

        if result.data:
            logger.info(f"pipeline_meta 削除: {doc_id}")
            return jsonify({'success': True, 'doc_id': doc_id})
        else:
            return jsonify({'success': False, 'error': 'レコードが見つかりません'}), 404

    except Exception as e:
        logger.error(f"pipeline_meta 削除エラー: {e}")
        return safe_error_response(e)


@app.route('/internal/blocked-documents/<doc_id>/reset', methods=['POST'])
def reset_blocked_document(doc_id):
    """GATE_BLOCKED ドキュメントを pending にリセット（ゲート判定をクリア）"""
    try:
        db = DatabaseClient(use_service_role=True)

        result = db.client.table('pipeline_meta').update({
            'gate_decision':    None,
            'gate_block_code':  None,
            'gate_block_reason': None,
            'processing_status': 'pending',
        }).eq('id', doc_id).execute()

        if result.data:
            logger.info(f"ゲートリセット: {doc_id}")
            return jsonify({'success': True, 'doc_id': doc_id})
        else:
            return jsonify({'success': False, 'error': 'レコードが見つかりません'}), 404

    except Exception as e:
        logger.error(f"ゲートリセットエラー: {e}")
        return safe_error_response(e)


@app.route('/internal/blocked-documents', methods=['GET'])
def get_blocked_documents():
    """Gatekeeperによってブロックされたドキュメント一覧を取得

    【責務】
    - gate_decision='BLOCK' のドキュメント一覧を取得
    - ブロック理由と詳細情報を含む

    【パラメータ】
    - source: 対象ソース（省略時は全体）
    - workspace: source の別名（後方互換）
    - limit: 取得件数上限（デフォルト100、最大500）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        source = request.args.get('source') or request.args.get('workspace', 'all')
        limit = min(int(request.args.get('limit', 100)), 500)

        query = db.client.table('pipeline_meta').select(
            'id, raw_id, raw_table, person, source, created_at, updated_at, '
            'gate_decision, gate_block_code, gate_block_reason, gate_policy_version, '
            'origin_app, origin_confidence, layout_profile'
        ).eq('gate_decision', 'BLOCK')

        if source and source != 'all':
            query = query.eq('source', source)

        result = query.order('updated_at', desc=True).limit(limit).execute()
        blocked_docs = result.data or []

        # 09_unified_documents と raw_tables からタイトルを取得
        if blocked_docs:
            title_map = _get_document_titles(db, blocked_docs)
            for doc in blocked_docs:
                doc['title'] = title_map.get((str(doc.get('raw_id')), doc.get('raw_table')))

        block_stats = {}
        for doc in blocked_docs:
            code = doc.get('gate_block_code', 'UNKNOWN')
            block_stats[code] = block_stats.get(code, 0) + 1

        return jsonify({
            'success': True,
            'blocked_docs': blocked_docs,
            'count': len(blocked_docs),
            'block_stats': block_stats,
            'source': source
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
    - source: 対象ソース（省略時は全体）
    - workspace: source の別名（後方互換）
    - limit: 取得件数上限（デフォルト100、最大500）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        source = request.args.get('source') or request.args.get('workspace', 'all')
        limit = min(int(request.args.get('limit', 100)), 500)

        query = db.client.table('pipeline_meta').select(
            'id, raw_id, raw_table, person, source, created_at, updated_at, '
            'error_message, last_error_reason, attempt_count, failed_at, metadata, '
            'origin_app, origin_confidence, layout_profile, '
            'gate_block_code, gate_block_reason, pdf_creator, pdf_producer'
        ).eq('processing_status', 'failed')

        if source and source != 'all':
            query = query.eq('source', source)

        result = query.order('updated_at', desc=True).limit(limit).execute()
        failed_docs = result.data or []

        # 09_unified_documents と raw_tables からタイトルを取得
        if failed_docs:
            title_map = _get_document_titles(db, failed_docs)
            for doc in failed_docs:
                doc['title'] = title_map.get((str(doc.get('raw_id')), doc.get('raw_table')))

        error_stats = {}
        for doc in failed_docs:
            error_msg = doc.get('last_error_reason') or doc.get('error_message') or 'Unknown error'
            error_key = error_msg[:50] if error_msg else 'Unknown'
            error_stats[error_key] = error_stats.get(error_key, 0) + 1

        return jsonify({
            'success': True,
            'failed_docs': failed_docs,
            'count': len(failed_docs),
            'error_stats': error_stats,
            'source': source
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

        # pipeline_meta からドキュメント情報を取得（doc_ids は pipeline_meta.id）
        pm_result = db.client.table('pipeline_meta').select(
            'id, raw_id, raw_table'
        ).in_('id', doc_ids).execute()

        if not pm_result.data:
            return jsonify({'success': False, 'error': 'ドキュメントが見つかりません'}), 404

        # 各 raw_table の raw_id → file_url, title を取得
        raw_info = {}  # (raw_id, raw_table) → {file_url, title}
        by_table = {}
        for row in pm_result.data:
            rt = row['raw_table']
            if rt not in by_table:
                by_table[rt] = []
            by_table[rt].append(str(row['raw_id']))

        for rt, ids in by_table.items():
            try:
                rt_result = db.client.table(rt).select('id, title, file_url') \
                    .in_('id', ids).execute()
                for r in (rt_result.data or []):
                    raw_info[(str(r['id']), rt)] = {
                        'file_url': r.get('file_url', ''),
                        'title':    r.get('title', ''),
                    }
            except Exception as e:
                logger.warning(f"raw table {rt} 取得エラー: {e}")

        # Stage A と Google Drive コネクタをインポート
        from shared.pipeline.stage_a.a3_entry_point import A3EntryPoint
        from shared.common.connectors.google_drive import GoogleDriveConnector

        drive = GoogleDriveConnector()
        a3 = A3EntryPoint()
        results = []

        for pm_row in pm_result.data:
            meta_id   = pm_row['id']
            raw_id    = str(pm_row['raw_id'])
            raw_table = pm_row['raw_table']
            raw_data  = raw_info.get((raw_id, raw_table), {})
            file_name = raw_data.get('title') or 'unknown.pdf'
            file_url  = raw_data.get('file_url', '')

            try:
                match = _re.search(r'/d/([a-zA-Z0-9_-]+)', file_url)
                if not match:
                    results.append({
                        'id': meta_id, 'file_name': file_name,
                        'success': False, 'error': 'file_url から Drive ファイルIDを取得できません'
                    })
                    continue

                drive_file_id = match.group(1)

                with tempfile.TemporaryDirectory() as tmp_dir:
                    local_path = drive.download_file(drive_file_id, file_name, tmp_dir)

                    if not local_path:
                        results.append({
                            'id': meta_id, 'file_name': file_name,
                            'success': False, 'error': 'Google Drive からのダウンロード失敗'
                        })
                        continue

                    a_result = a3.process(Path(local_path))

                if not a_result.get('success', True):
                    results.append({
                        'id': meta_id, 'file_name': file_name,
                        'success': False, 'error': a_result.get('error', '分類失敗')
                    })
                    continue

                origin_app = a_result.get('origin_app', '')
                confidence = a_result.get('confidence', '')
                layout_profile = a_result.get('layout_profile', '')
                reason = a_result.get('reason', '')

                update_data = {
                    'origin_app':         origin_app or None,
                    'origin_confidence':  confidence or None,
                    'layout_profile':     layout_profile or None,
                }

                raw_meta = (a_result.get('a2_type') or {}).get('raw_metadata') or \
                           a_result.get('raw_metadata') or {}
                creator  = raw_meta.get('Creator') or raw_meta.get('creator') or ''
                producer = raw_meta.get('Producer') or raw_meta.get('producer') or ''
                if creator:
                    update_data['pdf_creator'] = creator
                if producer:
                    update_data['pdf_producer'] = producer

                db.client.table('pipeline_meta').update(update_data).eq('id', meta_id).execute()
                logger.info(f"[classify] {file_name}: {origin_app} ({confidence})")

                results.append({
                    'id': meta_id, 'file_name': file_name,
                    'success': True,
                    'origin_app': origin_app,
                    'confidence': confidence,
                    'layout_profile': layout_profile,
                    'reason': reason,
                })

            except Exception as e:
                logger.error(f"分類エラー ({meta_id}): {e}")
                error_str = str(e)
                is_404 = ('File not found' in error_str or '404' in error_str
                          or 'not found' in error_str.lower())
                if is_404:
                    try:
                        db.client.table('pipeline_meta').update(
                            {'origin_app': 'file_not_found'}
                        ).eq('id', meta_id).execute()
                        logger.info(f"[classify] {file_name}: file_not_found (Drive 404) → DB保存")
                    except Exception as ue:
                        logger.error(f"file_not_found 保存エラー: {ue}")
                results.append({
                    'id': meta_id, 'file_name': file_name,
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
    """ドキュメントを pending にリセット（pipeline_meta ベース）

    【パラメータ】
    - limit: 確認件数（デフォルト10、上限100）
    - source: 対象ソース（省略時は全体）
    - workspace: source の別名（後方互換）
    - doc_ids: 特定ドキュメントID（pipeline_meta.id の配列 or カンマ区切り）
    - doc_id: 特定ドキュメント1件

    【doc_ids 指定時】
    - ステータスを問わず pending にリセット（completed / failed の再処理）
    【doc_ids 省略時】
    - 現在の pending 件数を返す（既に pending なので操作不要）
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}

        limit = min(int(data.get('limit', data.get('max_items', 10))), 100)
        source = data.get('source') or data.get('workspace') or 'all'

        doc_ids = data.get('doc_ids')
        if isinstance(doc_ids, str):
            doc_ids = [d.strip() for d in doc_ids.split(',') if d.strip()]
        if not doc_ids and data.get('doc_id'):
            doc_ids = [data.get('doc_id')]

        if doc_ids:
            # 特定ドキュメント: queued にセット
            result = db.client.table('pipeline_meta').update({
                'processing_status':  'queued',
                'processing_progress': 0.0,
            }).in_('id', doc_ids).execute()
            enqueued_count = len(result.data) if result.data else 0
            logger.info(f"[run-requests] {enqueued_count}件を queued にセット: {doc_ids}")

            return jsonify({
                'success': True,
                'message': f'{enqueued_count}件をキューに追加しました',
                'enqueued_count': enqueued_count,
                'doc_ids': doc_ids,
                'note': 'Worker が queued を順番に処理します'
            })
        else:
            # 自動: pending から limit 件を取得して queued に更新
            q = db.client.table('pipeline_meta').select('id').eq('processing_status', 'pending')
            if source != 'all':
                q = q.eq('source', source)
            
            result = q.limit(limit).execute()
            pending_docs = result.data or []
            
            enqueued_count = 0
            doc_ids_to_queue = []
            if pending_docs:
                doc_ids_to_queue = [doc['id'] for doc in pending_docs]
                update_res = db.client.table('pipeline_meta').update({
                    'processing_status': 'queued',
                    'processing_progress': 0.0,
                }).in_('id', doc_ids_to_queue).execute()
                enqueued_count = len(update_res.data) if update_res.data else 0
                logger.info(f"[run-requests] {enqueued_count}件を pending から queued に自動追加 (limit={limit})")

            return jsonify({
                'success': True,
                'message': f'{enqueued_count}件をキューに追加しました' if enqueued_count > 0 else 'キューに追加するpendingドキュメントがありません',
                'enqueued_count': enqueued_count,
                'doc_ids': doc_ids_to_queue,
                'note': 'Worker が queued を順番に処理します'
            })

    except Exception as e:
        logger.error(f"RUN 要求作成エラー: {e}")
        return safe_error_response(e)


# ========== doc_type 一括変更 ==========

@app.route('/internal/update-doc-type', methods=['POST'])
def update_doc_type():
    """選択ドキュメントの category を一括変更

    【パラメータ】
    - doc_ids: pipeline_meta.id の配列
    - doc_type / category: 変更後の category 文字列
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}
        doc_ids = data.get('doc_ids', [])
        category = (data.get('category') or data.get('doc_type', '')).strip()

        if not doc_ids:
            return jsonify({'success': False, 'error': 'doc_ids が必要です'}), 400
        if not category:
            return jsonify({'success': False, 'error': 'category が必要です'}), 400

        # pipeline_meta の raw_id/raw_table を取得して 09_unified_documents を更新
        pm_result = db.client.table('pipeline_meta').select('raw_id, raw_table') \
            .in_('id', doc_ids).execute()

        if not pm_result.data:
            return jsonify({'success': False, 'error': 'ドキュメントが見つかりません'}), 404

        raw_ids = [str(r['raw_id']) for r in pm_result.data if r.get('raw_id')]
        result = db.client.table('09_unified_documents').update({
            'category': category
        }).in_('raw_id', raw_ids).execute()

        updated_count = len(result.data) if result.data else 0
        logger.info(f"[update-doc-type] {updated_count}件の category を '{category}' に変更: {doc_ids}")

        return jsonify({
            'success': True,
            'message': f'{updated_count}件の category を変更しました',
            'updated_count': updated_count,
        })

    except Exception as e:
        logger.error(f"update-doc-type エラー: {e}")
        return safe_error_response(e)


# ========== pending リセット ==========

@app.route('/internal/set-pending', methods=['POST'])
def set_pending():
    """選択ドキュメントを pending にリセット（doc_ids は pipeline_meta.id）

    【パラメータ】
    - doc_ids: pipeline_meta.id の配列
    """
    try:
        db = DatabaseClient(use_service_role=True)
        data = request.get_json(silent=True) or {}
        doc_ids = data.get('doc_ids', [])

        if not doc_ids:
            return jsonify({'success': False, 'error': 'doc_ids が必要です'}), 400

        result = db.client.table('pipeline_meta').update({
            'processing_status':   'pending',
            'processing_progress':  0.0,
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

        # pipeline_meta からドキュメント情報と中間データを取得（doc_id は pipeline_meta.id）
        pm_result = db.client.table('pipeline_meta').select(
            'id, raw_id, raw_table, person, source, owner_id, '
            'g14_reconstructed_tables, g21_articles, g17_table_analyses, g22_ai_extracted'
        ).eq('id', doc_id).execute()

        if not pm_result.data:
            return jsonify({'success': False, 'error': 'ドキュメントが見つかりません'}), 404

        pm = pm_result.data[0]
        raw_id    = pm['raw_id']
        raw_table = pm['raw_table']

        # 09_unified_documents から ui_data と title を取得
        ud_result = db.client.table('09_unified_documents').select(
            'id, title, category, ui_data'
        ).eq('raw_id', raw_id).eq('raw_table', raw_table).execute()
        ud = ud_result.data[0] if ud_result.data else {}



        def _parse_json_col(val):
            if val is None:
                return None
            if isinstance(val, str):
                return _json.loads(val)
            return val

        # ===== G22 再処理 =====
        if start_stage == 'G22':
            g21_raw = pm.get('g21_articles')
            if not g21_raw:
                return jsonify({
                    'success': False,
                    'error': 'g21_articles が保存されていません（G22 再処理には G21 の出力が必要です）'
                }), 400

            articles = _parse_json_col(g21_raw) or []

            from shared.pipeline.stage_g.g22_text_ai_processor import G22TextAIProcessor
            g22 = G22TextAIProcessor(document_id=doc_id)
            g22_result = g22.process(articles)

            if not g22_result.get('success'):
                return jsonify({'success': False, 'error': f'G22 処理失敗: {g22_result.get("error", "不明")}'}), 500

            # pipeline_meta の g22_ai_extracted を更新
            db.client.table('pipeline_meta').update({
                'g22_ai_extracted': g22_result
            }).eq('id', doc_id).execute()

            # 09_unified_documents の ui_data を更新
            try:
                ui_data = _parse_json_col(ud.get('ui_data')) or {}
                ui_data['timeline'] = g22_result.get('calendar_events', [])
                ui_data['actions']  = g22_result.get('tasks', [])
                ui_data['notices']  = g22_result.get('notices', [])
                db.client.table('09_unified_documents').update(
                    {'ui_data': ui_data}
                ).eq('raw_id', raw_id).eq('raw_table', raw_table).execute()
            except Exception as e:
                logger.warning(f'ui_data 更新エラー（継続）: {e}')

            g17_output = _parse_json_col(pm.get('g17_table_analyses')) or []
            g21_output = articles
            g22_output = g22_result

        # ===== G17 再処理 =====
        else:
            g14_raw = pm.get('g14_reconstructed_tables')
            if not g14_raw:
                return jsonify({
                    'success': False,
                    'error': 'g14_reconstructed_tables が保存されていません（G17 再処理には G14 の出力が必要です）'
                }), 400

            g14_data = _parse_json_col(g14_raw) or []

            from shared.pipeline.stage_g.g17_table_ai_processor import G17TableAIProcessor
            g17 = G17TableAIProcessor(document_id=doc_id)
            g17_result = g17.process(g14_data)

            if not g17_result.get('success'):
                return jsonify({'success': False, 'error': f'G17 処理失敗: {g17_result.get("error", "不明")}'}), 500

            g17_output = g17_result.get('table_analyses', [])

            # pipeline_meta の g17_table_analyses を更新
            db.client.table('pipeline_meta').update({
                'g17_table_analyses': g17_output
            }).eq('id', doc_id).execute()

            def _to_ui_tables(table_analyses):
                result = []
                for analysis in table_analyses:
                    sections = analysis.get('sections', [])
                    section_data = sections[0].get('data', []) if sections else []
                    result.append({
                        'table_id':   analysis.get('table_id', ''),
                        'table_type': analysis.get('table_type', 'structured'),
                        'description': analysis.get('description', ''),
                        'headers':    [],
                        'rows':       section_data,
                        'sections':   sections,
                        'metadata':   sections[0].get('metadata', {}) if sections else analysis.get('metadata', {}),
                    })
                return result
            ui_tables = _to_ui_tables(g17_output)

            # 09_unified_documents の ui_data を更新
            try:
                ui_data = _parse_json_col(ud.get('ui_data')) or {}
                ui_data['tables'] = ui_tables
                db.client.table('09_unified_documents').update(
                    {'ui_data': ui_data}
                ).eq('raw_id', raw_id).eq('raw_table', raw_table).execute()
            except Exception as e:
                logger.warning(f'ui_data 更新エラー（継続）: {e}')

            g17_output = ui_tables

            g21_output = _parse_json_col(pm.get('g21_articles')) or []
            g22_output = _parse_json_col(pm.get('g22_ai_extracted')) or {}

        # ===== G31: 09_unified_documents を更新し unified_doc_id を取得 =====
        from shared.pipeline.stage_g.g31_unified_writer import G31UnifiedWriter

        # 最新 ui_data を DB から再取得（G17/G22 の更新を反映）
        refreshed_ud = db.client.table('09_unified_documents').select(
            'id, title, category, ui_data'
        ).eq('raw_id', raw_id).eq('raw_table', raw_table).execute()
        updated_ud = refreshed_ud.data[0] if refreshed_ud.data else ud
        updated_ui_data = _parse_json_col(updated_ud.get('ui_data'))

        # G31 用 raw_data を pipeline_meta から構築
        raw_data_for_g31 = {
            'id':       pm['id'],
            'raw_id':   raw_id,
            'raw_table': raw_table,
            'person':   pm.get('person'),
            'source':   pm.get('source'),
            'title':    updated_ud.get('title'),
            'category': updated_ud.get('category'),
            'owner_id': pm.get('owner_id'),
        }

        g31 = G31UnifiedWriter(db_client=db)
        g31_result = g31.process(
            raw_data=raw_data_for_g31,
            raw_table=raw_table,
            ui_data=updated_ui_data,
        )
        if not g31_result.get('success'):
            return jsonify({'success': False, 'error': f'G31 失敗: {g31_result.get("error")}'}), 500

        unified_doc_id = g31_result['doc_id']
        logger.info(f'[reprocess-g] G31 完了: unified_doc_id={unified_doc_id}')

        # ===== Stage J: チャンク生成 =====
        from shared.common.processing.metadata_chunker import MetadataChunker
        file_name = updated_ud.get('title') or 'unknown'

        document_data = {
            'file_name': file_name,
            'doc_type':  updated_ud.get('category'),
            'text_blocks': [
                {'title': a.get('title', ''), 'content': a.get('body', '')}
                for a in g21_output
                if a.get('body', '').strip()
            ],
            'structured_tables': [
                {
                    'table_title': t.get('description', t.get('table_title', '')),
                    'headers':     t.get('headers', []),
                    'rows':        t.get('rows', []),
                    'metadata':    t.get('metadata', {}),
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

        new_chunks = all_chunks
        logger.info(f'[reprocess-g] {start_stage} 全チャンク再挿入: {len(new_chunks)}件')

        # 全チャンク削除（unified_doc_id = 09_unified_documents.id を使用）
        try:
            db.client.table('10_ix_search_index').delete().eq('doc_id', unified_doc_id).execute()
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
        k_result = stage_k.embed_and_save(unified_doc_id, new_chunks)

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
