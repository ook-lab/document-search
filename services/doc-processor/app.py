"""
Flask Web Application - Document Processing System
ドキュメント処理システムのWebインターフェース（処理専用）
"""
import os
import sys
from pathlib import Path

# プロジェクトルートをPythonパスに追加（ローカル実行時用）
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from loguru import logger
import psutil
import time

app = Flask(__name__)
CORS(app)

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
            # 5分以上更新がなければ期限切れとみなす
            if lock.get('is_processing') and lock.get('updated_at'):
                from datetime import datetime, timezone
                updated_at = datetime.fromisoformat(lock['updated_at'].replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                if (now - updated_at).total_seconds() > 300:  # 5分
                    logger.warning(f"処理ロックが期限切れ（{(now - updated_at).total_seconds():.0f}秒経過）。自動リセット。")
                    set_processing_lock(False)
                    return False
            return lock.get('is_processing', False)
        return False
    except Exception as e:
        logger.error(f"処理ロック取得エラー: {e}")
        return False


def set_processing_lock(is_processing: bool):
    """Supabaseに処理ロック状態を設定"""
    try:
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
        client.table('processing_lock').update(data).eq('id', 1).execute()
        logger.info(f"処理ロック設定: {is_processing}")
        return True
    except Exception as e:
        logger.error(f"処理ロック設定エラー: {e}")
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
    """ワーカーを登録（処理開始時）"""
    try:
        client = get_supabase_client()
        worker_id = f"{_instance_id}-{doc_id[:8]}"
        client.table('processing_workers').upsert({
            'instance_id': worker_id,
            'doc_id': doc_id,
            'doc_title': doc_title,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }).execute()
        # current_workersを更新
        update_worker_count()
        return True
    except Exception as e:
        logger.error(f"ワーカー登録エラー: {e}")
        return False


def unregister_worker(doc_id: str) -> bool:
    """ワーカーを解除（処理終了時）"""
    try:
        client = get_supabase_client()
        worker_id = f"{_instance_id}-{doc_id[:8]}"
        client.table('processing_workers').delete().eq('instance_id', worker_id).execute()
        # current_workersを更新
        update_worker_count()
        logger.info(f"ワーカー解除: {worker_id}")
        return True
    except Exception as e:
        logger.error(f"ワーカー解除エラー: {e}")
        return False


def clear_all_workers() -> bool:
    """全ワーカーをクリア（処理終了時）"""
    try:
        client = get_supabase_client()
        # ステップ1: 全レコードのinstance_idを取得
        result = client.table('processing_workers').select('instance_id').execute()
        instance_ids = [row['instance_id'] for row in result.data] if result.data else []

        # ステップ2: instance_idが存在する場合のみ削除
        if instance_ids:
            client.table('processing_workers').delete().in_('instance_id', instance_ids).execute()

        # ステップ3: current_workersを0に更新
        client.table('processing_lock').update({
            'current_workers': 0
        }).eq('id', 1).execute()

        logger.info(f"全ワーカーをクリアしました（削除件数: {len(instance_ids)}件）")
        return True
    except Exception as e:
        logger.error(f"全ワーカークリアエラー: {e}")
        return False




def reset_stuck_documents() -> int:
    """
    processing状態でスタックしているドキュメントをpendingにリセット

    スタック判定:
    - processing_status='processing'
    - かつ processing_workers テーブルに存在しない

    Returns:
        リセットした件数
    """
    try:
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)

        # processing状態のドキュメントを取得
        result = db.client.table('Rawdata_FILE_AND_MAIL').select('id').eq('processing_status', 'processing').execute()
        processing_ids = [row['id'] for row in result.data] if result.data else []

        if not processing_ids:
            return 0

        # processing_workersから実際に処理中のdoc_idを取得
        workers_result = db.client.table('processing_workers').select('doc_id').execute()
        active_doc_ids = [row['doc_id'] for row in workers_result.data] if workers_result.data else []

        # スタックしているドキュメント = processingだがワーカーに存在しない
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
    """現在のワーカー数をカウントしてSupabaseに保存"""
    try:
        client = get_supabase_client()
        result = client.table('processing_workers').select('instance_id').execute()
        count = len(result.data) if result.data else 0
        client.table('processing_lock').update({
            'current_workers': count
        }).eq('id', 1).execute()
        return count
    except Exception as e:
        logger.error(f"ワーカー数更新エラー: {e}")
        return 0


def update_progress_to_supabase(current_index: int, total_count: int, current_file: str,
                                 success_count: int, failed_count: int, logs: list):
    """進捗情報をSupabaseに保存（複数インスタンス共有用）"""
    try:
        client = get_supabase_client()
        # 最新150件のログのみ保存
        latest_logs = logs[-150:] if len(logs) > 150 else logs

        # システムリソース情報も含める（Cloud Run環境対応）
        cpu_percent = get_cgroup_cpu()
        memory_info = get_cgroup_memory()

        # スロットル情報を取得
        throttle_delay = processing_status.get('resource_control', {}).get('throttle_delay', 0.0)

        client.table('processing_lock').update({
            'current_index': current_index,
            'total_count': total_count,
            'current_file': current_file,
            'success_count': success_count,
            'failed_count': failed_count,
            'logs': latest_logs,
            'cpu_percent': cpu_percent,
            'memory_percent': memory_info['percent'],
            'memory_used_gb': memory_info['used_gb'],
            'memory_total_gb': memory_info['total_gb'],
            'throttle_delay': throttle_delay,
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
                'failed_count': data.get('failed_count', 0),
                'logs': data.get('logs', [])
            }
        return {
            'current_index': 0,
            'total_count': 0,
            'current_file': '',
            'success_count': 0,
            'failed_count': 0,
            'logs': []
        }
    except Exception as e:
        logger.error(f"進捗取得エラー: {e}")
        return {
            'current_index': 0,
            'total_count': 0,
            'current_file': '',
            'success_count': 0,
            'failed_count': 0,
            'logs': []
        }


def get_worker_status() -> dict:
    """現在のワーカー状況を取得"""
    try:
        client = get_supabase_client()
        # processing_lockからmax_parallelとcurrent_workersを取得
        lock_result = client.table('processing_lock').select('*').eq('id', 1).execute()
        # processing_workersから詳細を取得
        workers_result = client.table('processing_workers').select('*').execute()

        lock_data = lock_result.data[0] if lock_result.data else {}
        workers = workers_result.data if workers_result.data else []

        return {
            'max_parallel': lock_data.get('max_parallel', 10),
            'current_workers': len(workers),
            'is_processing': lock_data.get('is_processing', False),
            'workers': workers
        }
    except Exception as e:
        logger.error(f"ワーカー状況取得エラー: {e}")
        return {'max_parallel': 3, 'current_workers': 0, 'is_processing': False, 'workers': []}


def adjust_max_parallel(memory_percent: float) -> int:
    """メモリ使用率に基づいてmax_parallelを調整

    重要: 実行数が上限に達している場合のみ並列数を増やす
    （実際にフル稼働している状態で余裕があることを確認してから増やす）
    """
    try:
        client = get_supabase_client()
        status = get_worker_status()
        current_max = status['max_parallel']
        current_workers = status['current_workers']

        new_max = current_max

        # メモリに余裕があり、ワーカーが上限に達している場合のみ増加
        # current_workers >= current_max - 1 で、ほぼフル稼働していることを確認
        if memory_percent < 60 and current_workers >= current_max - 1:
            new_max = min(current_max + 1, 20)  # 最大20
        # メモリが逼迫している場合は減少
        elif memory_percent > 85:
            new_max = max(current_max - 1, 1)  # 最小1

        if new_max != current_max:
            client.table('processing_lock').update({
                'max_parallel': new_max
            }).eq('id', 1).execute()
            logger.info(f"max_parallel調整: {current_max} → {new_max} (メモリ: {memory_percent:.1f}%)")

        return new_max
    except Exception as e:
        logger.error(f"max_parallel調整エラー: {e}")
        return 3


def can_start_new_worker() -> bool:
    """新しいワーカーを開始できるか確認"""
    status = get_worker_status()
    return status['current_workers'] < status['max_parallel']


# ========== 処理進捗の管理（ローカルキャッシュ、表示用） ==========
processing_status = {
    'is_processing': False,
    'current_index': 0,
    'total_count': 0,
    'current_file': '',
    'success_count': 0,
    'failed_count': 0,
    'logs': [],
    # ステージ進捗（ドキュメント内の進捗）
    'current_stage': '',
    'stage_progress': 0.0,  # 0.0 - 1.0
    # アダプティブリソース制御情報
    'resource_control': {
        'max_parallel': 3,
        'current_parallel': 0,
        'throttle_delay': 0.0,
        'adjustment_count': 0
    }
}

# CPU使用率計算用の前回の値
_last_cpu_stats = {'usage_usec': 0, 'timestamp': time.time()}


def get_cgroup_memory():
    """cgroupからメモリ使用率を取得（Cloud Run対応）"""
    try:
        # Cloud Run: cgroup v1 のメモリ情報を取得
        with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
            current = int(f.read().strip())
        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
            max_mem = int(f.read().strip())

            # 非常に大きい値の場合は無制限なので、システム全体のメモリを使用
            # (通常、9223372036854771712 などの値)
            if max_mem > 1e15:
                max_mem = psutil.virtual_memory().total

        # キャッシュメモリを除外して正確な使用量を計算
        # Kubernetesの標準: working_set = usage - total_inactive_file
        inactive_file = 0
        total_cache = 0
        try:
            # cgroup v2 と v1 の両方に対応
            import os
            memory_stat_path = '/sys/fs/cgroup/memory.stat'  # v2
            if not os.path.exists(memory_stat_path):
                memory_stat_path = '/sys/fs/cgroup/memory/memory.stat'  # v1

            with open(memory_stat_path, 'r') as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) == 2:
                        key, value = parts
                        if key == 'total_inactive_file':
                            inactive_file = int(value)
                        elif key == 'total_cache' or key == 'cache':
                            total_cache = int(value)

            # デバッグログで両方の計算方法を確認
            if inactive_file > 0 or total_cache > 0:
                logger.info(f"[MEMORY DEBUG] usage={current/(1024**3):.2f}GB, inactive_file={inactive_file/(1024**3):.2f}GB, total_cache={total_cache/(1024**3):.2f}GB")
                logger.info(f"[MEMORY DEBUG] working_set(inactive除外)={(current-inactive_file)/(1024**3):.2f}GB, working_set(全cache除外)={(current-total_cache)/(1024**3):.2f}GB")
        except Exception as e:
            logger.debug(f"[MEMORY DEBUG] memory.stat読み取り失敗: {e}")

        # Kubernetes標準の計算式を使用（total_inactive_fileを除外）
        # これは「すぐに解放可能なキャッシュ」を除いた実使用量
        cache_memory = inactive_file

        # キャッシュを除いた実際の使用量
        actual_used = current - cache_memory
        percent = (actual_used / max_mem) * 100
        used_gb = actual_used / (1024 ** 3)
        total_gb = max_mem / (1024 ** 3)

        return {
            'percent': round(percent, 1),
            'used_gb': round(used_gb, 2),
            'total_gb': round(total_gb, 2)
        }
    except Exception as e:
        # cgroupが使えない場合はpsutilにフォールバック
        logger.debug(f"cgroup memory読み取り失敗、psutilを使用: {e}")
        memory = psutil.virtual_memory()
        return {
            'percent': round(memory.percent, 1),
            'used_gb': round(memory.used / (1024 ** 3), 2),
            'total_gb': round(memory.total / (1024 ** 3), 2)
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
    - 並列数を20-30まで増やせる可能性
    - 処理時間短縮でコストメリット
    """

    def __init__(self, initial_max_parallel=3, min_parallel=1, max_parallel=5, history_size=3):
        """初期化

        Args:
            initial_max_parallel: 初期並列数（デフォルト: 3）
            min_parallel: 最小並列数（デフォルト: 1）
            max_parallel: 最大並列数（デフォルト: 5）
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

        # フェーズ1: 並列数増加（余裕がある場合）
        # 重要: 実行数が上限に達している場合のみ増やす（実際にフル稼働している状態で余裕があることを確認）
        if memory_percent < self.memory_low and self.throttle_delay == 0:
            # 実行数が上限に達している かつ 余裕あり かつ 減速なし → 並列数を増やす
            if current_workers >= self.max_parallel - 1 and self.max_parallel < self.max_parallel_limit:
                self.max_parallel = min(self.max_parallel + self.parallel_step, self.max_parallel_limit)
                adjusted = True
                self.logger.info(
                    f"[リソース制御] ⬆️ メモリ余裕 ({memory_percent:.1f}%) + フル稼働中 ({current_workers}/{original_parallel}) → 並列数増加: {original_parallel} → {self.max_parallel}"
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
        elif '失敗' in msg or '❌' in msg:
            processing_status['current_stage'] = '失敗'
            processing_status['stage_progress'] = 1.0
        elif 'ダウンロード' in msg_lower or 'download' in msg_lower:
            processing_status['current_stage'] = 'ダウンロード中'
            processing_status['stage_progress'] = 0.1

        # ログは最大300件まで保持
        if len(processing_status['logs']) > 300:
            processing_status['logs'] = processing_status['logs'][-300:]


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
        'message': 'Document Processing System is running'
    })


@app.route('/api/process/progress', methods=['GET'])
def get_process_progress():
    """
    処理進捗とシステムリソースを取得（Supabaseから共有状態を取得）
    """
    try:
        # cgroupからリソース情報を取得（Cloud Run対応）
        cpu_percent = get_cgroup_cpu()
        memory_info = get_cgroup_memory()

        # Supabaseからワーカー状況を取得（複数インスタンス対応）
        worker_status = get_worker_status()

        # Supabaseから進捗情報を取得（複数インスタンス共有）
        progress = get_progress_from_supabase()

        # 現在処理中のドキュメントの進捗を取得
        current_stage = ''
        stage_progress = 0.0
        try:
            # processing中のドキュメントを取得（最新1件）
            processing_doc = db.client.table('Rawdata_FILE_AND_MAIL').select('processing_stage, processing_progress').eq('processing_status', 'processing').order('created_at', desc=False).limit(1).execute()
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
            'failed_count': progress['failed_count'],
            'logs': progress['logs'],  # 最新150件
            # ステージ進捗（Supabaseから取得）
            'current_stage': current_stage,
            'stage_progress': stage_progress,
            'system': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory_info['percent'],
                'memory_used_gb': memory_info['used_gb'],
                'memory_total_gb': memory_info['total_gb']
            },
            'resource_control': {
                'current_parallel': worker_status['current_workers'],
                'max_parallel': worker_status['max_parallel'],
                'throttle_delay': 0.0,
                'adjustment_count': 0
            },
            'workers': worker_status['workers']  # 処理中のドキュメント一覧
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


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
        print(f"[ERROR] ワークスペース取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process/stats', methods=['GET'])
def get_process_stats():
    """
    処理キューの統計情報を取得

    ステータスの意味:
    - pending: 未処理（まだバッチに選ばれていない）
    - processing: このバッチの処理対象として選択済み（重複防止マーク）
    - completed: 処理完了
    - failed: 処理失敗
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
            'failed': 0
        }

        for doc in response.data:
            status = doc.get('processing_status')
            # nullは無視（pending/processing/completed/failedのみカウント）
            if status in stats:
                stats[status] = stats.get(status, 0) + 1

        return jsonify({
            'success': True,
            'stats': stats
        })

    except Exception as e:
        logger.error(f"統計取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process/start', methods=['POST'])
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
        processing_status['failed_count'] = 0
        processing_status['current_stage'] = ''
        processing_status['stage_progress'] = 0.0
        processing_status['logs'] = [
            f"[{datetime.now().strftime('%H:%M:%S')}] 処理開始準備中...",
            f"[{datetime.now().strftime('%H:%M:%S')}] ワークスペース: {workspace}, 制限: {limit}件"
        ]

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

            async def process_all():
                # アダプティブリソースマネージャーを初期化
                # 現在: 16GBで max_parallel=10
                # TODO: 32GBの場合は max_parallel=20-30 に増やすことでコストメリットが期待できる
                # [MEMORY PER DOC] ログで1ドキュメントあたりのメモリ消費を確認してから調整
                resource_manager = AdaptiveResourceManager(initial_max_parallel=3, min_parallel=1, max_parallel=10)

                # 並列数制御用のセマフォ（動的に調整）
                # NOTE: セマフォは固定値なので、タスク内で制御ロジックを実装
                active_tasks = []
                processed_count = 0

                # リソース監視 + ログ同期タスク
                async def monitor_resources():
                    """リソース監視タスク：メモリ使用率をチェックして並列数を調整 + ログを定期同期"""
                    while processing_status['is_processing']:
                        try:
                            memory_info = get_cgroup_memory()
                            memory_percent = memory_info['percent']

                            # ワーカー数を取得（並列数調整に必要）
                            worker_status = get_worker_status()
                            current_workers = worker_status['current_workers']

                            # Supabaseのmax_parallelを調整（複数インスタンス共有）
                            new_max = adjust_max_parallel(memory_percent)

                            # ローカルのリソース調整も実行（current_workersを渡す）
                            status = resource_manager.adjust_resources(memory_percent, current_workers)
                            # Supabaseの値で上書き
                            resource_manager.max_parallel_limit = new_max

                            # リソース制御情報を更新
                            processing_status['resource_control']['max_parallel'] = new_max
                            processing_status['resource_control']['current_parallel'] = current_workers
                            processing_status['resource_control']['throttle_delay'] = status['throttle_delay']
                            processing_status['resource_control']['adjustment_count'] = resource_manager.adjustment_count

                            # ログをSupabaseに定期同期（2秒ごと）
                            update_progress_to_supabase(
                                processing_status['current_index'],
                                processing_status['total_count'],
                                processing_status['current_file'],
                                processing_status['success_count'],
                                processing_status['failed_count'],
                                processing_status['logs']
                            )

                        except Exception as e:
                            logger.error(f"リソース監視エラー: {e}")

                        await asyncio.sleep(2)  # 2秒ごとに監視 + ログ同期

                # 個別ドキュメント処理タスク
                async def process_single_document(doc, index):
                    """個別ドキュメントを処理"""
                    nonlocal processed_count

                    # 停止フラグをチェック
                    if not processing_status['is_processing']:
                        return False

                    file_name = doc.get('file_name', 'unknown')
                    title = doc.get('title', '') or '(タイトル未生成)'
                    doc_id = doc.get('id', str(index))

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

                        # ログは最大300件まで保持
                        if len(processing_status['logs']) > 300:
                            processing_status['logs'] = processing_status['logs'][-300:]

                        # Supabaseに進捗を保存
                        update_progress_to_supabase(
                            index, len(docs), title,
                            processing_status['success_count'],
                            processing_status['failed_count'],
                            processing_status['logs']
                        )

                        # ドキュメント処理
                        success = await processor.process_document(doc, preserve_workspace)

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
                            processing_status['failed_count'] += 1
                            processing_status['logs'].append(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ❌ 失敗: {title}"
                            )

                        # Supabaseに結果を保存
                        update_progress_to_supabase(
                            index, len(docs), title,
                            processing_status['success_count'],
                            processing_status['failed_count'],
                            processing_status['logs']
                        )

                        return success
                    finally:
                        # Supabaseからワーカー解除
                        unregister_worker(doc_id)

                # リソース監視タスクを開始
                monitor_task = asyncio.create_task(monitor_resources())

                try:
                    # ドキュメントを処理
                    for i, doc in enumerate(docs, 1):
                        # 停止フラグをチェック
                        if not processing_status['is_processing']:
                            processing_status['logs'].append(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 処理が中断されました"
                            )
                            break

                        # 並列数制御：active_tasksが max_parallel 未満になるまで待機
                        # Supabaseで共有されたmax_parallel_limitを使用
                        while len(active_tasks) >= resource_manager.max_parallel_limit:
                            # 完了したタスクを削除
                            done_tasks = [t for t in active_tasks if t.done()]
                            for t in done_tasks:
                                active_tasks.remove(t)

                            if len(active_tasks) >= resource_manager.max_parallel_limit:
                                # まだ並列数が上限に達している場合は少し待機
                                await asyncio.sleep(0.1)

                        # 新しいタスクを開始
                        task = asyncio.create_task(process_single_document(doc, i))
                        active_tasks.append(task)

                        # 現在の並列数を更新
                        processing_status['resource_control']['current_parallel'] = len(active_tasks)

                    # すべてのタスクが完了するまで待機
                    if active_tasks:
                        await asyncio.gather(*active_tasks, return_exceptions=True)

                finally:
                    # リソース監視タスクを停止
                    processing_status['is_processing'] = False  # 監視ループを終了
                    await monitor_task

            try:
                asyncio.run(process_all())

                # 処理完了
                processing_status['is_processing'] = False
                processing_status['current_file'] = ''
                processing_status['logs'].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] 処理完了: 成功={processing_status['success_count']}, 失敗={processing_status['failed_count']}"
                )

                # Supabaseに完了状態を保存
                update_progress_to_supabase(
                    processing_status['current_index'],
                    processing_status['total_count'],
                    '',
                    processing_status['success_count'],
                    processing_status['failed_count'],
                    processing_status['logs']
                )
            except Exception as e:
                processing_status['is_processing'] = False
                processing_status['logs'].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] ❌ エラー: {str(e)}"
                )
                print(f"[ERROR] バックグラウンド処理エラー: {e}")
            finally:
                # ハンドラーを削除
                logger.remove(handler_id)
                # Supabaseロック解放
                set_processing_lock(False)

        # 別スレッドで処理を開始
        thread = threading.Thread(target=background_processing, daemon=True)
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
        print(f"[ERROR] 処理開始エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process/stop', methods=['POST'])
def stop_processing():
    """
    処理を停止
    """
    global processing_status

    # ローカルまたはSupabaseのロックをチェック
    if not processing_status['is_processing'] and not get_processing_lock():
        return jsonify({
            'success': False,
            'error': '実行中の処理がありません'
        }), 400

    # 停止フラグを立てる
    processing_status['is_processing'] = False
    set_processing_lock(False)  # Supabaseロック解放
    processing_status['logs'].append(
        f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ ユーザーによって停止されました"
    )

    return jsonify({
        'success': True,
        'message': '処理を停止しました'
    })


@app.route('/api/process/reset', methods=['POST'])
def reset_processing():
    """
    処理フラグを強制リセット（Supabase + ローカル両方）
    """
    global processing_status

    # Supabaseロック解放
    set_processing_lock(False)

    # ローカル状態もリセット
    processing_status['is_processing'] = False
    processing_status['current_index'] = 0
    processing_status['total_count'] = 0
    processing_status['current_file'] = ''
    processing_status['success_count'] = 0
    processing_status['failed_count'] = 0
    processing_status['current_stage'] = ''
    processing_status['stage_progress'] = 0.0
    processing_status['logs'] = [
        f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 処理フラグを強制リセットしました（Supabase + ローカル）"
    ]
    processing_status['resource_control'] = {
        'current_parallel': 0,
        'max_parallel': 3,
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
