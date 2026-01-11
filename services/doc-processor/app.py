"""
Flask Web Application - Document Processing System
ドキュメント処理システムのWebインターフェース（処理専用）
"""
import os
import sys
from pathlib import Path

# プロジェクトルートをPythonパスに追加（ローカル実行時用）
# Docker環境では PYTHONPATH=/app が設定済みなので、parent.parentが/になる場合はスキップ
project_root = Path(__file__).parent.parent
if str(project_root) != '/' and str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from loguru import logger
import psutil
import time

# ========== 定数定義 ==========
# ロック関連
LOCK_TIMEOUT_SECONDS = 300  # ロックの有効期限（5分）
LOCK_RETRY_COUNT = 3  # ロック設定時の最大リトライ回数
LOCK_RETRY_DELAY = 1.0  # リトライ間隔（秒）

# リソース監視関連
RESOURCE_UPDATE_INTERVAL = 5.0  # リソース情報更新間隔（秒）
MAX_LOG_ENTRIES = 300  # 保持するログの最大件数
MAX_LOG_ENTRIES_SUPABASE = 150  # Supabaseに保存するログの最大件数

# 並列処理関連
DEFAULT_MAX_PARALLEL = 1  # デフォルト並列数
MIN_PARALLEL = 1  # 最小並列数
MAX_PARALLEL_LIMIT = 100  # 最大並列数上限

# メモリ閾値（16GB環境用デフォルト）
MEMORY_LOW_THRESHOLD = 60.0  # 余裕あり（並列数増加可）
MEMORY_HIGH_THRESHOLD = 85.0  # 逼迫（減速開始）
MEMORY_CRITICAL_THRESHOLD = 90.0  # 危険（並列数削減）
MEMORY_RECOVER_THRESHOLD = 70.0  # 回復（減速緩和）

# スロットル関連
THROTTLE_STEP = 0.5  # スロットル調整ステップ（秒）
MAX_THROTTLE_DELAY = 3.0  # 最大スロットル遅延（秒）

# タイムアウト関連
DOCUMENT_PROCESS_TIMEOUT = int(os.getenv('DOC_PROCESS_TIMEOUT', '1800'))  # ドキュメント処理タイムアウト（秒）

app = Flask(__name__)

# CORS設定: 環境変数で許可オリジンを指定（デフォルトは本番環境のみ）
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'https://doc-processor-*.run.app,https://docs.ookubotechnologies.com').split(',')
# 開発環境では全許可
if os.getenv('FLASK_ENV') == 'development' or os.getenv('DEBUG') == 'true':
    CORS(app)
else:
    CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ========== 認証設定 ==========
API_KEY = os.getenv('DOC_PROCESSOR_API_KEY', '')
REQUIRE_AUTH = os.getenv('REQUIRE_AUTH', 'true').lower() == 'true'

def require_api_key(f):
    """APIキー認証デコレーター"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not REQUIRE_AUTH:
            return f(*args, **kwargs)

        # APIキーが設定されていない場合は認証をスキップ（開発環境用）
        if not API_KEY:
            logger.warning("DOC_PROCESSOR_API_KEY is not set. Skipping authentication.")
            return f(*args, **kwargs)

        # ヘッダーまたはクエリパラメータからAPIキーを取得
        provided_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if provided_key != API_KEY:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401

        return f(*args, **kwargs)
    return decorated_function

def safe_error_response(error: Exception, status_code: int = 500):
    """安全なエラーレスポンスを生成（本番環境ではスタックトレースを隠す）"""
    is_development = os.getenv('FLASK_ENV') == 'development' or os.getenv('DEBUG') == 'true'
    if is_development:
        return jsonify({'success': False, 'error': str(error)}), status_code
    else:
        # 本番環境では詳細を隠す
        logger.error(f"Error: {error}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), status_code

# Supabaseクライアント（処理ロック用）
_supabase_client = None

def get_supabase_client():
    """Supabaseクライアントを取得（シングルトン）"""
    global _supabase_client
    if _supabase_client is None:
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)
        _supabase_client = db.client
    return _supabase_client


def get_processing_lock():
    """Supabaseから処理ロック状態を取得"""
    try:
        client = get_supabase_client()
        result = client.table('processing_lock').select('*').eq('id', 1).execute()
        if result.data:
            lock = result.data[0]
            # LOCK_TIMEOUT_SECONDS以上更新がなければ期限切れとみなす
            if lock.get('is_processing') and lock.get('updated_at'):
                from datetime import datetime, timezone
                updated_at = datetime.fromisoformat(lock['updated_at'].replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                if (now - updated_at).total_seconds() > LOCK_TIMEOUT_SECONDS:
                    logger.warning(f"処理ロックが期限切れ（{(now - updated_at).total_seconds():.0f}秒経過）。自動リセット。")
                    set_processing_lock(False)
                    return False
            return lock.get('is_processing', False)
        return False
    except Exception as e:
        logger.error(f"処理ロック取得エラー: {e}")
        return False


def set_processing_lock(is_processing: bool, max_retries: int = 3, timeout_sec: float = 10.0):
    """Supabaseに処理ロック状態を設定（タイムアウトとリトライ付き）

    Args:
        is_processing: ロック状態
        max_retries: 最大リトライ回数（デフォルト: 3）
        timeout_sec: 1回あたりのタイムアウト秒数（デフォルト: 10秒）

    Returns:
        bool: 成功した場合True
    """
    import time as time_module

    for attempt in range(max_retries):
        try:
            start_time = time_module.time()
            client = get_supabase_client()

            data = {
                'is_processing': is_processing,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            if is_processing:
                data['started_at'] = datetime.now(timezone.utc).isoformat()
                # 処理開始時: processing状態のまま残っているドキュメントをpendingにリセット
                reset_stuck_documents()
            else:
                # 処理終了時: 全ワーカーをクリア & processing状態のドキュメントをリセット
                clear_all_workers()
                reset_stuck_documents()

            # タイムアウトチェック
            elapsed = time_module.time() - start_time
            if elapsed > timeout_sec:
                raise TimeoutError(f"Processing lock operation timed out after {elapsed:.1f}s")

            client.table('processing_lock').update(data).eq('id', 1).execute()
            logger.info(f"処理ロック設定: {is_processing}")
            return True

        except Exception as e:
            logger.warning(f"処理ロック設定エラー (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time_module.sleep(1.0)  # リトライ前に1秒待機
            else:
                logger.error(f"処理ロック設定に失敗しました（{max_retries}回試行）: {e}")
                return False

    return False


def update_processing_lock():
    """処理中のロックタイムスタンプを更新（ハートビート）"""
    try:
        client = get_supabase_client()
        client.table('processing_lock').update({
            'updated_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', 1).execute()
        return True
    except Exception as e:
        logger.error(f"ロック更新エラー: {e}")
        return False


# ========== ワーカー管理（複数インスタンス対応） ==========

import uuid
_instance_id = str(uuid.uuid4())[:8]  # このインスタンスのID


def register_worker(doc_id: str, doc_title: str) -> bool:
    """ワーカーを登録（処理開始時）- active_tasksのみ使用"""
    # active_tasksへの追加は呼び出し元で実施済み
    # ここでは何もしない（互換性のため関数は残す）
    return True


def unregister_worker(doc_id: str) -> bool:
    """ワーカーを解除（処理終了時）- active_tasksのみ使用"""
    # active_tasksからの削除は呼び出し元で実施済み
    # ここでは何もしない（互換性のため関数は残す）
    return True


def clear_all_workers() -> bool:
    """全ワーカーをクリア（処理終了時）- active_tasksのみ使用"""
    global active_tasks
    # active_tasksをクリア
    count = len(active_tasks)
    active_tasks.clear()
    logger.info(f"全ワーカーをクリアしました（削除件数: {count}件）")
    return True




def reset_stuck_documents() -> int:
    """
    processing状態でスタックしているドキュメントをpendingにリセット

    スタック判定:
    - processing_status='processing'
    - かつ active_tasks に存在しない

    Returns:
        リセットした件数
    """
    global active_tasks
    try:
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)

        # processing状態のドキュメントを取得
        result = db.client.table('Rawdata_FILE_AND_MAIL').select('id').eq('processing_status', 'processing').execute()
        processing_ids = [row['id'] for row in result.data] if result.data else []

        if not processing_ids:
            return 0

        # active_tasksから実際に処理中のdoc_idを取得
        active_doc_ids = list(active_tasks.keys())

        # スタックしているドキュメント = processingだがactive_tasksに存在しない
        stuck_ids = [doc_id for doc_id in processing_ids if doc_id not in active_doc_ids]

        if stuck_ids:
            # pendingにリセット
            db.client.table('Rawdata_FILE_AND_MAIL').update({
                'processing_status': 'pending'
            }).in_('id', stuck_ids).execute()
            logger.info(f"スタック状態のドキュメントをリセットしました（{len(stuck_ids)}件）")
            return len(stuck_ids)

        return 0
    except Exception as e:
        logger.error(f"reset_stuck_documents エラー: {e}")
        return 0

def update_worker_count() -> int:
    """現在のワーカー数を返す - active_tasksのみ使用"""
    global active_tasks
    # active_tasksから直接カウント（DB更新不要）
    return len(active_tasks)


def update_progress_to_supabase(current_index: int, total_count: int, current_file: str,
                                 success_count: int, error_count: int, logs: list):
    """進捗情報をSupabaseに保存（複数インスタンス共有用）"""
    global resource_manager, active_tasks
    try:
        client = get_supabase_client()
        # 最新のログのみ保存
        latest_logs = logs[-MAX_LOG_ENTRIES_SUPABASE:] if len(logs) > MAX_LOG_ENTRIES_SUPABASE else logs

        # システムリソース情報も含める（Cloud Run環境対応）
        cpu_percent = get_cgroup_cpu()
        memory_info = get_cgroup_memory()

        # 実際のワーカー数をactive_tasksから取得
        actual_workers = len(active_tasks)

        # グローバルなresource_managerが存在すれば、リソース調整を実行
        if resource_manager is not None:
            # メモリ使用率に基づいてリソース調整（実際のワーカー数を使用）
            adjust_result = resource_manager.adjust_resources(memory_info['percent'], actual_workers)
            # 調整結果をprocessing_statusに反映（whileループで参照される）
            processing_status['resource_control']['throttle_delay'] = adjust_result['throttle_delay']
            processing_status['resource_control']['adjustment_count'] = resource_manager.adjustment_count

        # resource_managerから最新値を取得（存在する場合）
        if resource_manager is not None:
            throttle_delay = resource_manager.throttle_delay
            max_parallel = resource_manager.max_parallel
            adjustment_count = resource_manager.adjustment_count
        else:
            resource_control = processing_status.get('resource_control', {})
            throttle_delay = resource_control.get('throttle_delay', 0.0)
            max_parallel = resource_control.get('max_parallel', 1)
            adjustment_count = resource_control.get('adjustment_count', 0)

        client.table('processing_lock').update({
            'current_index': current_index,
            'total_count': total_count,
            'current_file': current_file,
            'success_count': success_count,
            'error_count': error_count,
            'logs': latest_logs,
            'cpu_percent': cpu_percent,
            'memory_percent': memory_info['percent'],
            'memory_used_gb': memory_info['used_gb'],
            'memory_total_gb': memory_info['total_gb'],
            'throttle_delay': throttle_delay,
            'max_parallel': max_parallel,
            'current_workers': actual_workers,  # active_tasksから取得した実際のワーカー数
            'adjustment_count': adjustment_count,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', 1).execute()
        return True
    except Exception as e:
        logger.error(f"進捗更新エラー: {e}")
        return False


def get_progress_from_supabase() -> dict:
    """Supabaseから進捗情報を取得（複数インスタンス共有用）"""
    try:
        client = get_supabase_client()
        result = client.table('processing_lock').select('*').eq('id', 1).execute()
        if result.data:
            data = result.data[0]
            return {
                'current_index': data.get('current_index', 0),
                'total_count': data.get('total_count', 0),
                'current_file': data.get('current_file', ''),
                'success_count': data.get('success_count', 0),
                'error_count': data.get('error_count', 0),
                'logs': data.get('logs', []),
                # バックグラウンド処理インスタンスのリソース情報
                'cpu_percent': data.get('cpu_percent', 0.0),
                'memory_percent': data.get('memory_percent', 0.0),
                'memory_used_gb': data.get('memory_used_gb', 0.0),
                'memory_total_gb': data.get('memory_total_gb', 0.0),
                'throttle_delay': data.get('throttle_delay', 0.0),
                'adjustment_count': data.get('adjustment_count', 0)
            }
        return {
            'current_index': 0,
            'total_count': 0,
            'current_file': '',
            'success_count': 0,
            'error_count': 0,
            'logs': [],
            'cpu_percent': 0.0,
            'memory_percent': 0.0,
            'memory_used_gb': 0.0,
            'memory_total_gb': 0.0,
            'throttle_delay': 0.0,
            'adjustment_count': 0
        }
    except Exception as e:
        logger.error(f"進捗取得エラー: {e}")
        return {
            'current_index': 0,
            'total_count': 0,
            'current_file': '',
            'success_count': 0,
            'error_count': 0,
            'logs': [],
            'cpu_percent': 0.0,
            'memory_percent': 0.0,
            'memory_used_gb': 0.0,
            'memory_total_gb': 0.0,
            'throttle_delay': 0.0,
            'adjustment_count': 0
        }


def get_worker_status() -> dict:
    """現在のワーカー状況を取得 - active_tasksのみ使用"""
    global resource_manager, active_tasks
    try:
        # active_tasksから実際のワーカー数を取得
        current_workers = len(active_tasks)

        # resource_managerからmax_parallelを取得
        max_parallel = resource_manager.max_parallel if resource_manager else 1

        # processing_lockからis_processingのみ取得
        client = get_supabase_client()
        lock_result = client.table('processing_lock').select('is_processing').eq('id', 1).execute()
        lock_data = lock_result.data[0] if lock_result.data else {}

        # active_tasksの内容をworkers形式に変換
        workers = [
            {
                'doc_id': doc_id,
                'doc_title': task_info.get('title', ''),
                'started_at': task_info.get('started_at', '')
            }
            for doc_id, task_info in active_tasks.items()
        ]

        return {
            'max_parallel': max_parallel,
            'current_workers': current_workers,  # active_tasksから取得した実際のワーカー数
            'is_processing': lock_data.get('is_processing', False),
            'workers': workers
        }
    except Exception as e:
        logger.error(f"ワーカー状況取得エラー: {e}")
        return {'max_parallel': 1, 'current_workers': 0, 'is_processing': False, 'workers': []}


def adjust_max_parallel(memory_percent: float) -> int:
    """メモリ使用率に基づいてmax_parallelを調整

    重要: 実行数が上限に達している場合のみ並列数を増やす
    （実際にフル稼働している状態で余裕があることを確認してから増やす）
    """
    global resource_manager, active_tasks
    try:
        if not resource_manager:
            return 3

        current_max = resource_manager.max_parallel
        current_workers = len(active_tasks)

        new_max = current_max

        # メモリに余裕があり、ワーカーが上限に達している場合のみ増加
        # current_workers >= current_max - 1 で、ほぼフル稼働していることを確認
        if memory_percent < 60 and current_workers >= current_max - 1:
            new_max = min(current_max + 1, 20)  # 最大20
        # メモリが逼迫している場合は減少
        elif memory_percent > 85:
            new_max = max(current_max - 1, 1)  # 最小1

        if new_max != current_max:
            resource_manager.max_parallel = new_max
            logger.info(f"max_parallel調整: {current_max} → {new_max} (メモリ: {memory_percent:.1f}%)")

        return new_max
    except Exception as e:
        logger.error(f"max_parallel調整エラー: {e}")
        return 3


def can_start_new_worker() -> bool:
    """新しいワーカーを開始できるか確認 - active_tasksのみ使用"""
    global resource_manager, active_tasks
    current_workers = len(active_tasks)
    max_parallel = resource_manager.max_parallel if resource_manager else 1
    return current_workers < max_parallel


# ========== 処理進捗の管理（ローカルキャッシュ、表示用） ==========
processing_status = {
    'is_processing': False,
    'current_index': 0,
    'total_count': 0,
    'current_file': '',
    'success_count': 0,
    'error_count': 0,
    'logs': [],
    # ステージ進捗（ドキュメント内の進捗）
    'current_stage': '',
    'stage_progress': 0.0,  # 0.0 - 1.0
    # アダプティブリソース制御情報
    'resource_control': {
        'throttle_delay': 0.0,
        'adjustment_count': 0
    }
}

# CPU使用率計算用の前回の値
_last_cpu_stats = {'usage_usec': 0, 'timestamp': time.time()}

# グローバルなリソースマネージャー（update_progress_to_supabaseからアクセス用）
resource_manager = None

# アクティブタスク管理（辞書型: {doc_id: {'title': str, 'started_at': str}}）
active_tasks = {}


def get_cgroup_memory():
    """cgroupからメモリ使用率を取得（Cloud Run Gen2 cgroup v2対応）"""
    import os

    try:
        current = None
        max_mem = None
        inactive_file = 0

        # cgroup v2 パスを優先的に試行（Cloud Run Gen2）
        v2_current_path = '/sys/fs/cgroup/memory.current'
        v2_max_path = '/sys/fs/cgroup/memory.max'
        v2_stat_path = '/sys/fs/cgroup/memory.stat'

        # cgroup v1 パス（フォールバック用）
        v1_current_path = '/sys/fs/cgroup/memory/memory.usage_in_bytes'
        v1_max_path = '/sys/fs/cgroup/memory/memory.limit_in_bytes'
        v1_stat_path = '/sys/fs/cgroup/memory/memory.stat'

        # cgroup v2 を試行
        if os.path.exists(v2_current_path):
            logger.debug("[MEMORY] Using cgroup v2")
            with open(v2_current_path, 'r') as f:
                current = int(f.read().strip())
            with open(v2_max_path, 'r') as f:
                max_val = f.read().strip()
                # "max" は無制限を意味する
                if max_val == 'max':
                    max_mem = psutil.virtual_memory().total
                else:
                    max_mem = int(max_val)

            # cgroup v2 の memory.stat から inactive_file を取得
            if os.path.exists(v2_stat_path):
                with open(v2_stat_path, 'r') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) == 2:
                            key, value = parts
                            if key == 'inactive_file':
                                inactive_file = int(value)

        # cgroup v1 にフォールバック
        elif os.path.exists(v1_current_path):
            logger.debug("[MEMORY] Using cgroup v1")
            with open(v1_current_path, 'r') as f:
                current = int(f.read().strip())
            with open(v1_max_path, 'r') as f:
                max_mem = int(f.read().strip())
                # 非常に大きい値の場合は無制限
                if max_mem > 1e15:
                    max_mem = psutil.virtual_memory().total

            # cgroup v1 の memory.stat から inactive_file を取得
            if os.path.exists(v1_stat_path):
                with open(v1_stat_path, 'r') as f:
                    stat_content = f.read()
                    for line in stat_content.split('\n'):
                        parts = line.strip().split()
                        if len(parts) == 2:
                            key, value = parts
                            # total_inactive_file または inactive_file を探す
                            if key in ('total_inactive_file', 'inactive_file'):
                                inactive_file = int(value)
                                logger.debug(f"[MEMORY] Found {key}={value}")
                                break
                    # デバッグ: 見つからなかった場合、最初の10行をログ
                    if inactive_file == 0:
                        lines = stat_content.split('\n')[:10]
                        logger.warning(f"[MEMORY] inactive_file not found. First 10 lines: {lines}")

        if current is None or max_mem is None:
            raise FileNotFoundError("cgroup memory files not found")

        # キャッシュを除いた実際の使用量（Kubernetes標準）
        actual_used = current - inactive_file
        percent = (actual_used / max_mem) * 100
        used_gb = actual_used / (1024 ** 3)
        total_gb = max_mem / (1024 ** 3)

        logger.debug(f"[MEMORY] current={current/(1024**3):.2f}GB, inactive={inactive_file/(1024**3):.2f}GB, max={max_mem/(1024**3):.2f}GB, percent={percent:.1f}%")

        return {
            'percent': round(percent, 1),
            'used_gb': round(used_gb, 2),
            'total_gb': round(total_gb, 2)
        }
    except Exception as e:
        # cgroupが使えない場合はpsutilにフォールバック
        logger.warning(f"cgroup memory読み取り失敗、psutilを使用: {e}")
        try:
            memory = psutil.virtual_memory()
            return {
                'percent': round(memory.percent, 1),
                'used_gb': round(memory.used / (1024 ** 3), 2),
                'total_gb': round(memory.total / (1024 ** 3), 2)
            }
        except Exception as e2:
            # psutilも失敗した場合はデフォルト値を返す
            logger.error(f"psutil memory読み取りも失敗、デフォルト値を使用: {e2}")
            return {
                'percent': 50.0,  # 安全側のデフォルト値
                'used_gb': 8.0,
                'total_gb': 16.0
            }


def get_cgroup_cpu():
    """CPU使用率を取得（Cloud Run対応）"""
    global _last_cpu_stats

    try:
        # Cloud Run Gen2: cgroup v2 または v1 のパスを試行
        usage_nsec = None
        cpu_usage_paths = [
            '/sys/fs/cgroup/cpu.stat',  # cgroup v2 (Gen2)
            '/sys/fs/cgroup/cpuacct/cpuacct.usage'  # cgroup v1 (Gen1)
        ]

        for path in cpu_usage_paths:
            try:
                if path.endswith('cpu.stat'):
                    # cgroup v2: cpu.statから usage_usec を読み取る
                    with open(path, 'r') as f:
                        for line in f:
                            if line.startswith('usage_usec'):
                                usage_nsec = int(line.split()[1]) * 1000  # マイクロ秒→ナノ秒
                                break
                else:
                    # cgroup v1: cpuacct.usageから直接読み取る
                    with open(path, 'r') as f:
                        usage_nsec = int(f.read().strip())

                if usage_nsec is not None:
                    break
            except Exception:
                continue

        if usage_nsec is None:
            # cgroupから読み取れない場合はpsutilにフォールバック
            return round(psutil.cpu_percent(interval=0.1), 1)

        current_time = time.time()

        # 初回呼び出しの場合は初期化してpsutilで取得
        if _last_cpu_stats['usage_usec'] == 0:
            _last_cpu_stats = {'usage_usec': usage_nsec, 'timestamp': current_time}
            # psutilで即座の値を返す
            return round(psutil.cpu_percent(interval=0.1), 1)

        # 前回との差分から使用率を計算
        time_delta = current_time - _last_cpu_stats['timestamp']
        usage_delta = usage_nsec - _last_cpu_stats['usage_usec']

        if time_delta > 0:
            # usage_deltaはナノ秒、time_deltaは秒
            # CPU使用率 = (使用時間の増加 / 経過時間) / CPU数
            cpu_count = psutil.cpu_count() or 4
            cpu_percent = (usage_delta / (time_delta * 1_000_000_000)) * 100 / cpu_count
            cpu_percent = max(0.0, min(cpu_percent, 100.0))  # 0-100%の範囲
        else:
            cpu_percent = 0.0

        # 次回のために保存
        _last_cpu_stats = {'usage_usec': usage_nsec, 'timestamp': current_time}

        return round(cpu_percent, 1)
    except Exception as e:
        # 失敗時はpsutilにフォールバック
        logger.debug(f"cgroup CPU読み取り失敗、psutilを使用: {e}")
        return round(psutil.cpu_percent(interval=0.1), 1)


class AdaptiveResourceManager:
    """アダプティブリソース制御マネージャー

    メモリ使用率に基づいて並列数とスロットル遅延を動的に調整します。

    制御ロジック（総メモリ量に応じて閾値を自動調整）:

    16GB環境（保守的）:
    - 並列数制御: memory < 60% → 増加, > 90% → 削減
    - スロットル: > 85% → 減速開始, < 70% → 減速緩和

    32GB環境（攻めの設定）:
    - 並列数制御: memory < 70% → 増加, > 95% → 削減
    - スロットル: > 90% → 減速開始, < 80% → 減速緩和

    32GBの利点:
    - 95%まで使っても残り1.6GB（16GBの90%と同等）
    - 並列数を最大100まで増やせる可能性
    - 処理時間短縮でコストメリット
    """

    def __init__(self, initial_max_parallel=1, min_parallel=1, max_parallel=100, history_size=3):
        """初期化

        Args:
            initial_max_parallel: 初期並列数（デフォルト: 1）
            min_parallel: 最小並列数（デフォルト: 1）
            max_parallel: 最大並列数（デフォルト: 100）
            history_size: 移動平均用の履歴サイズ（デフォルト: 3）
        """
        self.max_parallel = initial_max_parallel
        self.min_parallel = min_parallel
        self.max_parallel_limit = max_parallel
        self.throttle_delay = 0.0  # スロットル遅延（秒）

        # 総メモリ量を取得して閾値を動的に設定
        total_memory_gb = 16.0  # デフォルト
        try:
            mem_info = get_cgroup_memory()
            total_memory_gb = mem_info['total_gb']
        except:
            pass

        # しきい値をメモリサイズに応じて調整
        if total_memory_gb >= 32:
            # 32GB以上: 攻めの設定（残り1.6GB = 95%まで許容）
            self.memory_low = 70.0   # 余裕あり → 並列数増加
            self.memory_high = 90.0  # 逼迫 → 減速開始
            self.memory_critical = 95.0  # 危険 → 並列数削減
            self.memory_recover = 80.0  # 回復 → 減速緩和
        elif total_memory_gb >= 24:
            # 24GB: 中間設定
            self.memory_low = 65.0
            self.memory_high = 87.0
            self.memory_critical = 92.0
            self.memory_recover = 75.0
        else:
            # 16GB以下: 保守的設定（デフォルト）
            self.memory_low = 60.0   # 余裕あり → 並列数増加
            self.memory_high = 85.0  # 逼迫 → 減速開始
            self.memory_critical = 90.0  # 危険 → 並列数削減
            self.memory_recover = 70.0  # 回復 → 減速緩和

        from loguru import logger
        self.logger = logger
        self.logger.info(f"[INIT] AdaptiveResourceManager: total_memory={total_memory_gb:.1f}GB, thresholds=[{self.memory_low}%, {self.memory_high}%, {self.memory_critical}%]")

        # 調整ステップ
        self.parallel_step = 1
        self.throttle_step = 0.5  # 秒
        self.max_throttle = 3.0   # 最大スロットル遅延

        # 移動平均用の履歴
        self.history_size = history_size
        self.memory_history = []

        # 統計
        self.adjustment_count = 0
        self.last_adjustment_time = time.time()

        from loguru import logger
        self.logger = logger

    def adjust_resources(self, memory_percent, current_workers=0):
        """リソース使用率に基づいて並列数とスロットルを調整

        Args:
            memory_percent: 現在のメモリ使用率（%）
            current_workers: 現在の実行中ワーカー数

        Returns:
            dict: 調整情報 {'max_parallel': int, 'throttle_delay': float, 'adjusted': bool}
        """
        # 履歴に追加
        self.memory_history.append(memory_percent)
        if len(self.memory_history) > self.history_size:
            self.memory_history.pop(0)

        # 移動平均を計算（直近3回の平均）
        memory_avg = sum(self.memory_history) / len(self.memory_history)

        # 移動平均で判断（瞬間値ではなく）
        memory_percent = memory_avg

        original_parallel = self.max_parallel
        original_throttle = self.throttle_delay
        adjusted = False

        # フェーズ3: 並列数削減（緊急時）- 最優先
        if memory_percent > self.memory_critical:
            if self.max_parallel > self.min_parallel:
                self.max_parallel = max(self.max_parallel - self.parallel_step, self.min_parallel)
                adjusted = True
                self.logger.warning(
                    f"[リソース制御] 🚨 メモリ逼迫 ({memory_percent:.1f}%) → 並列数削減: {original_parallel} → {self.max_parallel}"
                )

        # フェーズ2: 減速制御
        if memory_percent > self.memory_high:
            # 逼迫 → 減速
            if self.throttle_delay < self.max_throttle:
                self.throttle_delay = min(self.throttle_delay + self.throttle_step, self.max_throttle)
                adjusted = True
                self.logger.info(
                    f"[リソース制御] ⚠️ メモリ高使用 ({memory_percent:.1f}%) → 減速: {original_throttle:.1f}秒 → {self.throttle_delay:.1f}秒"
                )
        elif memory_percent < self.memory_recover and self.throttle_delay > 0:
            # 回復 → 減速緩和
            self.throttle_delay = max(self.throttle_delay - self.throttle_step, 0.0)
            adjusted = True
            self.logger.info(
                f"[リソース制御] ✅ メモリ回復 ({memory_percent:.1f}%) → 減速緩和: {original_throttle:.1f}秒 → {self.throttle_delay:.1f}秒"
            )

        # max_parallel = current_workers + 1 (常に実行数+1が上限)
        # 実行数が減ればmax_parallelも減る、増えれば増える
        new_max_parallel = min(current_workers + 1, self.max_parallel_limit)
        new_max_parallel = max(new_max_parallel, self.min_parallel)  # 最小値を保証

        if new_max_parallel != self.max_parallel:
            old_max = self.max_parallel
            self.max_parallel = new_max_parallel
            adjusted = True
            self.logger.info(
                f"[リソース制御] max_parallel調整: {old_max} → {self.max_parallel} (実行数: {current_workers})"
            )

        if adjusted:
            self.adjustment_count += 1
            self.last_adjustment_time = time.time()

        return {
            'max_parallel': self.max_parallel,
            'throttle_delay': self.throttle_delay,
            'adjusted': adjusted,
            'memory_percent': memory_percent
        }

    async def apply_throttle(self):
        """スロットル遅延を適用"""
        if self.throttle_delay > 0:
            import asyncio
            await asyncio.sleep(self.throttle_delay)

    def get_status(self):
        """現在の状態を取得"""
        return {
            'max_parallel': self.max_parallel,
            'throttle_delay': self.throttle_delay,
            'adjustment_count': self.adjustment_count,
            'last_adjustment': self.last_adjustment_time
        }


# loguruのカスタムハンドラー：ログをprocessing_statusに送信
def log_to_processing_status(message):
    """loguruのログをprocessing_statusに追加 + ステージ検出"""
    log_record = message.record
    level = log_record['level'].name
    msg = log_record['message']

    # ログレベルに応じてフィルタリング（INFOのみ表示）
    if level in ['INFO', 'WARNING', 'ERROR']:
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_msg = f"[{timestamp}] {msg}"
        processing_status['logs'].append(formatted_msg)

        # ステージ検出（ログメッセージから現在のステージを推測）
        msg_lower = msg.lower()
        if 'stage h' in msg_lower or '構造化' in msg_lower:
            processing_status['current_stage'] = 'Stage H: 構造化'
            processing_status['stage_progress'] = 0.3
        elif 'stage j' in msg_lower or 'チャンク' in msg_lower:
            processing_status['current_stage'] = 'Stage J: チャンク化'
            processing_status['stage_progress'] = 0.5
        elif 'stage k' in msg_lower or 'embedding' in msg_lower or 'embed' in msg_lower:
            processing_status['current_stage'] = 'Stage K: Embedding'
            processing_status['stage_progress'] = 0.7
        elif '成功' in msg or '✅' in msg:
            processing_status['current_stage'] = '完了'
            processing_status['stage_progress'] = 1.0
        elif 'エラー' in msg or '❌' in msg:
            processing_status['current_stage'] = 'エラー'
            processing_status['stage_progress'] = 1.0
        elif 'ダウンロード' in msg_lower or 'download' in msg_lower:
            processing_status['current_stage'] = 'ダウンロード中'
            processing_status['stage_progress'] = 0.1

        # ログは最大件数まで保持
        if len(processing_status['logs']) > MAX_LOG_ENTRIES:
            processing_status['logs'] = processing_status['logs'][-MAX_LOG_ENTRIES:]


# loguruにカスタムハンドラーを追加（スレッド内で個別に追加するためここでは追加しない）
# logger.add(log_to_processing_status, format="{message}")


@app.route('/')
def index():
    """メインページ - 処理画面にリダイレクト"""
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
        'version': '2025-01-11-v2'  # デプロイ確認用
    })


@app.route('/api/process/progress', methods=['GET'])
def get_process_progress():
    """
    処理進捗とシステムリソースを取得（Supabaseから共有状態を取得）
    全ての情報はバックグラウンド処理インスタンスがSupabaseに保存した値を使用
    """
    try:
        # Supabaseからワーカー状況を取得（複数インスタンス対応）
        worker_status = get_worker_status()

        # Supabaseから進捗情報を取得（複数インスタンス共有）
        # CPU/メモリ/スロットル等もバックグラウンド処理インスタンスの値を取得
        progress = get_progress_from_supabase()

        # 現在処理中のドキュメントの進捗を取得
        current_stage = ''
        stage_progress = 0.0
        try:
            # processing中のドキュメントを取得（最新1件）
            client = get_supabase_client()
            processing_doc = client.table('Rawdata_FILE_AND_MAIL').select('processing_stage, processing_progress').eq('processing_status', 'processing').order('created_at', desc=False).limit(1).execute()
            if processing_doc.data and len(processing_doc.data) > 0:
                current_stage = processing_doc.data[0].get('processing_stage', '')
                stage_progress = processing_doc.data[0].get('processing_progress', 0.0)
        except Exception as e:
            logger.error(f"進捗取得エラー: {e}")

        return jsonify({
            'success': True,
            'processing': worker_status['is_processing'],
            'current_index': progress['current_index'],
            'total_count': progress['total_count'],
            'current_file': progress['current_file'],
            'success_count': progress['success_count'],
            'error_count': progress['error_count'],
            'logs': progress['logs'],  # 最新150件
            # ステージ進捗（Supabaseから取得）
            'current_stage': current_stage,
            'stage_progress': stage_progress,
            # バックグラウンド処理インスタンスのリソース情報（Supabaseから取得）
            'system': {
                'cpu_percent': progress['cpu_percent'],
                'memory_percent': progress['memory_percent'],
                'memory_used_gb': progress['memory_used_gb'],
                'memory_total_gb': progress['memory_total_gb']
            },
            'resource_control': {
                'current_workers': worker_status['current_workers'],
                'max_parallel': worker_status['max_parallel'],
                'throttle_delay': progress['throttle_delay'],
                'adjustment_count': progress['adjustment_count']
            },
            'workers': worker_status['workers']  # 処理中のドキュメント一覧
        })
    except Exception as e:
        return safe_error_response(e)


@app.route('/api/workspaces', methods=['GET'])
def get_workspaces():
    """
    ワークスペース一覧を取得
    """
    try:
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient()

        # ワークスペース一覧を取得
        query = db.client.table('Rawdata_FILE_AND_MAIL').select('workspace').execute()

        # ユニークなワークスペースを抽出
        workspaces = set()
        for row in query.data:
            workspace = row.get('workspace')
            if workspace:
                workspaces.add(workspace)

        # ソートしてリスト化
        workspace_list = sorted(list(workspaces))

        return jsonify({
            'success': True,
            'workspaces': workspace_list
        })

    except Exception as e:
        logger.error(f"ワークスペース取得エラー: {e}")
        return safe_error_response(e)


@app.route('/api/process/stats', methods=['GET'])
def get_process_stats():
    """
    処理キューの統計情報を取得

    ステータスの意味:
    - pending: 未処理（まだバッチに選ばれていない）
    - processing: このバッチの処理対象として選択済み（重複防止マーク）
    - completed: 処理完了
    - error: 処理エラー
    """
    try:
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient()

        workspace = request.args.get('workspace', 'all')

        query = db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status, workspace')

        if workspace != 'all':
            query = query.eq('workspace', workspace)

        response = query.execute()

        stats = {
            'pending': 0,
            'processing': 0,  # バッチに選ばれた件数
            'completed': 0,
            'error': 0  # フロントエンド表示用は 'error'
        }

        for doc in response.data:
            status = doc.get('processing_status')
            # nullは無視（pending/processing/completed/failedのみカウント）
            # データベースのステータスは 'failed' のまま、フロントには 'error' として送る
            if status == 'failed':
                stats['error'] = stats.get('error', 0) + 1
            elif status in stats:
                stats[status] = stats.get(status, 0) + 1

        return jsonify({
            'success': True,
            'stats': stats
        })

    except Exception as e:
        logger.error(f"統計取得エラー: {e}")
        return safe_error_response(e)


@app.route('/api/process/start', methods=['POST'])
@require_api_key
def start_processing():
    """
    ドキュメント処理を開始（バックグラウンド実行）
    """
    global processing_status

    # Supabaseでロック状態をチェック（複数インスタンス対応）
    if get_processing_lock():
        return jsonify({
            'success': False,
            'error': '既に処理が実行中です'
        }), 400

    try:
        import threading
        import asyncio

        data = request.get_json()
        workspace = data.get('workspace', 'all')
        limit = data.get('limit', 100)
        preserve_workspace = data.get('preserve_workspace', True)

        # Supabaseにロックを設定
        set_processing_lock(True)

        # loguruハンドラーを追加（process_queued_documents.pyのログをキャッチ）
        handler_id = logger.add(log_to_processing_status, format="{message}")

        # 進捗状況を初期化（処理開始をすぐに表示）
        processing_status['is_processing'] = True
        processing_status['current_index'] = 0
        processing_status['total_count'] = 0
        processing_status['current_file'] = '初期化中...'
        processing_status['success_count'] = 0
        processing_status['error_count'] = 0
        processing_status['current_stage'] = ''
        processing_status['stage_progress'] = 0.0
        processing_status['logs'] = [
            f"[{datetime.now().strftime('%H:%M:%S')}] 処理開始準備中...",
            f"[{datetime.now().strftime('%H:%M:%S')}] ワークスペース: {workspace}, 制限: {limit}件"
        ]
        # リソース制御を初期化
        processing_status['resource_control'] = {
            'throttle_delay': 0.0,
            'adjustment_count': 0
        }

        # Supabaseに初期状態を保存
        update_progress_to_supabase(0, 0, '初期化中...', 0, 0, processing_status['logs'])

        # DocumentProcessorをインポート
        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] モジュール読み込み中...")
        update_progress_to_supabase(0, 0, 'モジュール読み込み中...', 0, 0, processing_status['logs'])
        from process_queued_documents import DocumentProcessor

        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] プロセッサ初期化中...")
        update_progress_to_supabase(0, 0, 'プロセッサ初期化中...', 0, 0, processing_status['logs'])
        processor = DocumentProcessor()

        # pending ドキュメントを取得
        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ドキュメント取得中...")
        update_progress_to_supabase(0, 0, 'ドキュメント取得中...', 0, 0, processing_status['logs'])
        docs = processor.get_pending_documents(workspace, limit)

        if not docs:
            processing_status['is_processing'] = False
            processing_status['current_file'] = ''
            processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] 処理対象のドキュメントがありません")
            set_processing_lock(False)  # ロック解放
            return jsonify({
                'success': True,
                'message': '処理対象のドキュメントがありません',
                'processed': 0
            })

        # ドキュメント数を更新
        processing_status['total_count'] = len(docs)
        processing_status['current_file'] = ''
        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] {len(docs)}件のドキュメントを取得しました")
        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] バックグラウンド処理を開始します...")
        update_progress_to_supabase(0, len(docs), 'バックグラウンド処理開始', 0, 0, processing_status['logs'])

        # バックグラウンド処理関数
        def background_processing():
            global processing_status
            print("[DEBUG] background_processing() 開始")
            logger.info("[DEBUG] background_processing() 開始")

            # リソース監視用のTimer
            update_timer = None

            def periodic_resource_update():
                """5秒ごとにリソース情報を更新（別スレッドで実行）"""
                nonlocal update_timer
                if not processing_status['is_processing']:
                    logger.info("[PERIODIC_UPDATE] 処理終了につきタイマー停止")
                    return

                try:
                    logger.debug("[PERIODIC_UPDATE] リソース情報更新開始")
                    # update_progress_to_supabaseを呼び出し
                    update_progress_to_supabase(
                        processing_status['current_index'],
                        processing_status['total_count'],
                        processing_status['current_file'],
                        processing_status['success_count'],
                        processing_status['error_count'],
                        processing_status['logs']
                    )
                    logger.debug("[PERIODIC_UPDATE] リソース情報更新完了")
                except Exception as e:
                    logger.error(f"[PERIODIC_UPDATE] エラー: {e}", exc_info=True)
                finally:
                    # エラーが発生しても次のタイマーを確実に設定
                    if processing_status['is_processing']:
                        update_timer = threading.Timer(5.0, periodic_resource_update)
                        update_timer.daemon = True
                        update_timer.start()

            # 定期更新を開始（5秒間隔）
            update_timer = threading.Timer(5.0, periodic_resource_update)
            update_timer.daemon = True
            update_timer.start()
            logger.info("[PERIODIC_UPDATE] タイマー開始（5秒間隔）")

            async def process_all():
                print("[DEBUG] process_all() 開始")
                logger.info("[DEBUG] process_all() 開始")
                # アダプティブリソースマネージャーを初期化（グローバル変数として）
                # リソース適応型並列制御
                # - 初期並列数: 1（最小同時実行数）
                # - 最小並列数: 1（リソース逼迫時も1は維持）
                # - 最大並列数: 100（余裕がある場合はここまで増やす）
                # update_progress_to_supabaseからアクセスできるようにグローバル変数として初期化
                global resource_manager
                resource_manager = AdaptiveResourceManager(initial_max_parallel=1, min_parallel=1, max_parallel=100)

                # 並列数制御用のセマフォ（動的に調整）
                # NOTE: セマフォは固定値なので、タスク内で制御ロジックを実装
                # グローバルのactive_tasks（辞書）とは別に、asyncio.Taskのリストを管理
                pending_async_tasks = []
                processed_count = 0

                # 個別ドキュメント処理タスク
                async def process_single_document(doc, index):
                    """個別ドキュメントを処理"""
                    global active_tasks
                    nonlocal processed_count

                    # 停止フラグをチェック
                    if not processing_status['is_processing']:
                        return False

                    file_name = doc.get('file_name', 'unknown')
                    title = doc.get('title', '') or '(タイトル未生成)'
                    doc_id = doc.get('id', str(index))

                    # active_tasksにワーカー情報を追加
                    active_tasks[doc_id] = {
                        'title': title,
                        'started_at': datetime.now(timezone.utc).isoformat()
                    }
                    # Supabaseにワーカー登録
                    register_worker(doc_id, title)

                    try:
                        # 処理前のメモリを記録
                        mem_before = get_cgroup_memory()

                        # 進捗を更新
                        processing_status['current_index'] = index
                        processing_status['current_file'] = title
                        processing_status['logs'].append(
                            f"[{datetime.now().strftime('%H:%M:%S')}] [{index}/{len(docs)}] 処理中: {title}"
                        )

                        # ログは最大件数まで保持
                        if len(processing_status['logs']) > MAX_LOG_ENTRIES:
                            processing_status['logs'] = processing_status['logs'][-MAX_LOG_ENTRIES:]

                        # リソース情報を取得してリソース調整
                        memory_info = get_cgroup_memory()
                        memory_percent = memory_info['percent']
                        worker_status = get_worker_status()
                        current_workers = worker_status['current_workers']

                        # リソース調整（並列数を動的に調整）
                        status = resource_manager.adjust_resources(memory_percent, current_workers)
                        processing_status['resource_control']['throttle_delay'] = status['throttle_delay']
                        processing_status['resource_control']['adjustment_count'] = resource_manager.adjustment_count

                        # 進捗コールバック関数を定義
                        def progress_callback(stage):
                            """各ステージ開始時にSupabaseを更新"""
                            logger.info(f"[PROGRESS] Stage {stage} 開始")
                            processing_status['current_file'] = f"{title} (Stage {stage})"
                            update_progress_to_supabase(
                                processing_status['current_index'],
                                processing_status['total_count'],
                                processing_status['current_file'],
                                processing_status['success_count'],
                                processing_status['error_count'],
                                processing_status['logs']
                            )

                        # ドキュメント処理（進捗コールバックを渡す）
                        success = await processor.process_document(doc, preserve_workspace, progress_callback=progress_callback)

                        # 処理後のメモリを記録（最初の3件のみログ出力）
                        if index <= 3:
                            mem_after = get_cgroup_memory()
                            mem_delta = mem_after['used_gb'] - mem_before['used_gb']
                            logger.info(f"[MEMORY PER DOC] Doc#{index}: before={mem_before['used_gb']:.2f}GB, after={mem_after['used_gb']:.2f}GB, delta={mem_delta:.2f}GB, parallel={len(active_tasks)}")

                        # スロットル遅延を適用
                        await resource_manager.apply_throttle()

                        processed_count += 1

                        if success:
                            processing_status['success_count'] += 1
                            processing_status['logs'].append(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 成功: {title}"
                            )
                        else:
                            processing_status['error_count'] += 1
                            processing_status['logs'].append(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ❌ エラー: {title}"
                            )

                        # Supabaseに結果を保存
                        update_progress_to_supabase(
                            index, len(docs), title,
                            processing_status['success_count'],
                            processing_status['error_count'],
                            processing_status['logs']
                        )

                        return success
                    finally:
                        # active_tasksからワーカー情報を削除
                        if doc_id in active_tasks:
                            del active_tasks[doc_id]
                        # Supabaseからワーカー解除
                        unregister_worker(doc_id)

                # リソース監視はthreading.Timerで定期実行されているため、ここでは不要

                try:
                    # ドキュメントを処理
                    for i, doc in enumerate(docs, 1):
                        logger.info(f"[FOR_LOOP] イテレーション {i}/{len(docs)} 開始, pending_async_tasks={len(pending_async_tasks)}, max_parallel={resource_manager.max_parallel}")

                        # 停止フラグをチェック
                        if not processing_status['is_processing']:
                            processing_status['logs'].append(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 処理が中断されました"
                            )
                            break

                        # 並列数制御：pending_async_tasksが max_parallel 未満になるまで待機
                        # 動的に調整される max_parallel を使用（リソースに応じて1～100で変動）
                        if len(pending_async_tasks) >= resource_manager.max_parallel:
                            logger.info(f"[WHILE_ENTER] pending_async_tasks={len(pending_async_tasks)} >= max_parallel={resource_manager.max_parallel}, 待機開始")
                        while len(pending_async_tasks) >= resource_manager.max_parallel:
                            # 完了したタスクを削除
                            done_tasks = [t for t in pending_async_tasks if t.done()]
                            for t in done_tasks:
                                pending_async_tasks.remove(t)

                            if len(pending_async_tasks) >= resource_manager.max_parallel:
                                # まだ並列数が上限に達している場合は少し待機
                                await asyncio.sleep(0.1)

                        # 新しいタスクを開始
                        task = asyncio.create_task(process_single_document(doc, i))
                        pending_async_tasks.append(task)

                        # イベントループに制御を渡す
                        await asyncio.sleep(0)

                    # すべてのタスクが完了するまで待機
                    if pending_async_tasks:
                        await asyncio.gather(*pending_async_tasks, return_exceptions=True)

                finally:
                    # 処理完了（Supabaseも更新）
                    processing_status['is_processing'] = False
                    set_processing_lock(False)
                    logger.info("[PROCESS] 全ドキュメント処理完了")

            try:
                asyncio.run(process_all())

                # 処理完了
                processing_status['is_processing'] = False
                processing_status['current_file'] = ''
                processing_status['logs'].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] 処理完了: 成功={processing_status['success_count']}, エラー={processing_status['error_count']}"
                )

                # Supabaseに完了状態を保存
                update_progress_to_supabase(
                    processing_status['current_index'],
                    processing_status['total_count'],
                    '',
                    processing_status['success_count'],
                    processing_status['error_count'],
                    processing_status['logs']
                )
            except Exception as e:
                processing_status['is_processing'] = False
                processing_status['logs'].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] ❌ エラー: {str(e)}"
                )
                print(f"[ERROR] バックグラウンド処理エラー: {e}")
            finally:
                # 定期更新タイマーを停止
                processing_status['is_processing'] = False  # タイマーのループ条件をFalseに
                set_processing_lock(False)  # Supabaseも更新
                if update_timer is not None:
                    update_timer.cancel()
                    logger.info("[PERIODIC_UPDATE] タイマーをキャンセルしました")

                # グローバルなresource_managerをリセット
                global resource_manager
                resource_manager = None
                logger.info("[RESOURCE] resource_managerをリセットしました")

                # ハンドラーを削除
                logger.remove(handler_id)
                # Supabaseロック解放
                set_processing_lock(False)

        # 別スレッドで処理を開始
        # daemon=False: Cloud Runがインスタンスを落としても、処理完了まで待機
        thread = threading.Thread(target=background_processing, daemon=False)
        thread.start()

        # すぐにレスポンスを返す
        return jsonify({
            'success': True,
            'message': '処理を開始しました',
            'total_count': len(docs)
        })

    except Exception as e:
        processing_status['is_processing'] = False
        set_processing_lock(False)  # ロック解放
        logger.error(f"処理開始エラー: {e}")
        return safe_error_response(e)


@app.route('/api/process/stop', methods=['POST'])
@require_api_key
def stop_processing():
    """
    処理を停止
    """
    global processing_status

    # Supabaseのロックをチェック（ローカルは別インスタンスの可能性があるので見ない）
    if not get_processing_lock():
        return jsonify({
            'success': False,
            'error': '実行中の処理がありません'
        }), 400

    # 停止フラグを立てる（ローカル＋Supabase両方）
    processing_status['is_processing'] = False
    set_processing_lock(False)  # Supabaseロック解放 + ワーカークリア
    processing_status['logs'].append(
        f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ ユーザーによって停止されました"
    )

    return jsonify({
        'success': True,
        'message': '処理を停止しました'
    })


@app.route('/api/process/reset', methods=['POST'])
@require_api_key
def reset_processing():
    """
    処理フラグを強制リセット（Supabase + ローカル両方）
    """
    global processing_status

    # Supabaseロック解放
    set_processing_lock(False)

    # Supabaseの全状態をリセット
    try:
        client = get_supabase_client()
        client.table('processing_lock').update({
            'current_index': 0,
            'total_count': 0,
            'success_count': 0,
            'error_count': 0,
            'current_file': '',
            'current_workers': 0,
            'max_parallel': 1,
            'throttle_delay': 0.0,
            'adjustment_count': 0,
            'cpu_percent': 0.0,
            'memory_percent': 0.0,
            'memory_used_gb': 0.0,
            'memory_total_gb': 0.0,
            'logs': []
        }).eq('id', 1).execute()
    except Exception as e:
        logger.error(f"Supabaseリセットエラー: {e}")

    # ローカル状態もリセット
    processing_status['is_processing'] = False
    processing_status['current_index'] = 0
    processing_status['total_count'] = 0
    processing_status['current_file'] = ''
    processing_status['success_count'] = 0
    processing_status['error_count'] = 0
    processing_status['current_stage'] = ''
    processing_status['stage_progress'] = 0.0
    processing_status['logs'] = [
        f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 処理フラグを強制リセットしました（Supabase + ローカル）"
    ]
    processing_status['resource_control'] = {
        'throttle_delay': 0.0,
        'adjustment_count': 0
    }

    return jsonify({
        'success': True,
        'message': '処理フラグをリセットしました'
    })


if __name__ == '__main__':
    # 開発環境での実行
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
