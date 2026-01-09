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

from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from loguru import logger
import psutil
import time

app = Flask(__name__)
CORS(app)

# 処理進捗の管理
processing_status = {
    'is_processing': False,
    'current_index': 0,
    'total_count': 0,
    'current_file': '',
    'success_count': 0,
    'failed_count': 0,
    'logs': [],
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
            with open('/sys/fs/cgroup/memory/memory.stat', 'r') as f:
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
        # Cloud Run: cgroup v1 の cpuacct.usage から取得（ナノ秒単位）
        with open('/sys/fs/cgroup/cpuacct/cpuacct.usage', 'r') as f:
            usage_nsec = int(f.read().strip())

        current_time = time.time()

        # 初回呼び出しの場合は初期化のみ
        if _last_cpu_stats['usage_usec'] == 0:
            _last_cpu_stats = {'usage_usec': usage_nsec, 'timestamp': current_time}
            return 0.0

        # 前回との差分から使用率を計算
        time_delta = current_time - _last_cpu_stats['timestamp']
        usage_delta = usage_nsec - _last_cpu_stats['usage_usec']

        if time_delta > 0:
            # usage_deltaはナノ秒、time_deltaは秒
            # CPU使用率 = (使用時間の増加 / 経過時間)
            cpu_percent = (usage_delta / (time_delta * 1_000_000_000)) * 100
            cpu_percent = max(0.0, min(cpu_percent, 400.0))  # 0-400%の範囲
        else:
            cpu_percent = 0.0

        # 次回のために保存
        _last_cpu_stats = {'usage_usec': usage_nsec, 'timestamp': current_time}

        return round(cpu_percent, 1)
    except Exception as e:
        # cpuacct.usage読み取り失敗時は0を返す
        return 0.0


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

    def adjust_resources(self, memory_percent):
        """リソース使用率に基づいて並列数とスロットルを調整

        Args:
            memory_percent: 現在のメモリ使用率（%）

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
        if memory_percent < self.memory_low and self.throttle_delay == 0:
            # 余裕あり かつ 減速なし → 並列数を増やす
            if self.max_parallel < self.max_parallel_limit:
                self.max_parallel = min(self.max_parallel + self.parallel_step, self.max_parallel_limit)
                adjusted = True
                self.logger.info(
                    f"[リソース制御] ⬆️ メモリ余裕 ({memory_percent:.1f}%) → 並列数増加: {original_parallel} → {self.max_parallel}"
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
    """loguruのログをprocessing_statusに追加"""
    log_record = message.record
    level = log_record['level'].name
    msg = log_record['message']

    # ログレベルに応じてフィルタリング（INFOのみ表示）
    if level in ['INFO', 'WARNING', 'ERROR']:
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_msg = f"[{timestamp}] {msg}"
        processing_status['logs'].append(formatted_msg)

        # ログは最大300件まで保持
        if len(processing_status['logs']) > 300:
            processing_status['logs'] = processing_status['logs'][-300:]


# loguruにカスタムハンドラーを追加（スレッド内で個別に追加するためここでは追加しない）
# logger.add(log_to_processing_status, format="{message}")


@app.route('/')
def index():
    """メインページ - 処理画面にリダイレクト"""
    return render_template('processing.html')


@app.route('/processing')
def processing():
    """ドキュメント処理システムのメインページ"""
    return render_template('processing.html')


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
    処理進捗とシステムリソースを取得
    """
    try:
        # cgroupからリソース情報を取得（Cloud Run対応）
        cpu_percent = get_cgroup_cpu()
        memory_info = get_cgroup_memory()

        return jsonify({
            'success': True,
            'processing': processing_status['is_processing'],
            'current_index': processing_status['current_index'],
            'total_count': processing_status['total_count'],
            'current_file': processing_status['current_file'],
            'success_count': processing_status['success_count'],
            'failed_count': processing_status['failed_count'],
            'logs': processing_status['logs'][-150:],  # 最新150件
            'system': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory_info['percent'],
                'memory_used_gb': memory_info['used_gb'],
                'memory_total_gb': memory_info['total_gb']
            },
            'resource_control': processing_status['resource_control']
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
        from A_common.database.client import DatabaseClient
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
    """
    try:
        from A_common.database.client import DatabaseClient
        db = DatabaseClient()

        workspace = request.args.get('workspace', 'all')

        query = db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status, workspace')

        if workspace != 'all':
            query = query.eq('workspace', workspace)

        response = query.execute()

        stats = {
            'pending': 0,
            'processing': 0,
            'completed': 0,
            'failed': 0,
            'null': 0
        }

        for doc in response.data:
            status = doc.get('processing_status')
            if status is None:
                stats['null'] += 1
            else:
                stats[status] = stats.get(status, 0) + 1

        stats['total'] = len(response.data)

        processed = stats['completed'] + stats['failed']
        if processed > 0:
            stats['success_rate'] = round(stats['completed'] / processed * 100, 1)
        else:
            stats['success_rate'] = 0.0

        return jsonify({
            'success': True,
            'stats': stats
        })

    except Exception as e:
        print(f"[ERROR] 統計取得エラー: {e}")
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

    # 既に処理中の場合はエラー
    if processing_status['is_processing']:
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

        # 進捗状況を初期化（処理開始をすぐに表示）
        processing_status['is_processing'] = True
        processing_status['current_index'] = 0
        processing_status['total_count'] = 0
        processing_status['current_file'] = '初期化中...'
        processing_status['success_count'] = 0
        processing_status['failed_count'] = 0
        processing_status['logs'] = [
            f"[{datetime.now().strftime('%H:%M:%S')}] 処理開始準備中...",
            f"[{datetime.now().strftime('%H:%M:%S')}] ワークスペース: {workspace}, 制限: {limit}件"
        ]

        # DocumentProcessorをインポート
        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] モジュール読み込み中...")
        from process_queued_documents import DocumentProcessor

        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] プロセッサ初期化中...")
        processor = DocumentProcessor()

        # pending ドキュメントを取得
        processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ドキュメント取得中...")
        docs = processor.get_pending_documents(workspace, limit)

        if not docs:
            processing_status['is_processing'] = False
            processing_status['current_file'] = ''
            processing_status['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] 処理対象のドキュメントがありません")
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

        # バックグラウンド処理関数
        def background_processing():
            global processing_status

            # スレッド内でloguruハンドラーを追加（スレッドセーフ）
            from loguru import logger as thread_logger
            handler_id = thread_logger.add(log_to_processing_status, format="{message}")

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

                # リソース監視タスク
                async def monitor_resources():
                    """リソース監視タスク：メモリ使用率をチェックして並列数を調整"""
                    while processing_status['is_processing']:
                        try:
                            memory_info = get_cgroup_memory()
                            memory_percent = memory_info['percent']

                            # リソース調整
                            status = resource_manager.adjust_resources(memory_percent)

                            # リソース制御情報を更新
                            processing_status['resource_control']['max_parallel'] = status['max_parallel']
                            processing_status['resource_control']['throttle_delay'] = status['throttle_delay']
                            processing_status['resource_control']['adjustment_count'] = resource_manager.adjustment_count

                            # 調整ログを表示（調整があった場合のみ）
                            if status['adjusted']:
                                processing_status['logs'].append(
                                    f"[{datetime.now().strftime('%H:%M:%S')}] 🔧 並列数={status['max_parallel']}, "
                                    f"遅延={status['throttle_delay']:.1f}s, メモリ={memory_percent:.1f}%"
                                )
                        except Exception as e:
                            thread_logger.error(f"リソース監視エラー: {e}")

                        await asyncio.sleep(2)  # 2秒ごとに監視

                # 個別ドキュメント処理タスク
                async def process_single_document(doc, index):
                    """個別ドキュメントを処理"""
                    nonlocal processed_count

                    # 停止フラグをチェック
                    if not processing_status['is_processing']:
                        return False

                    file_name = doc.get('file_name', 'unknown')
                    title = doc.get('title', '') or '(タイトル未生成)'

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

                    return success

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
                        while len(active_tasks) >= resource_manager.max_parallel:
                            # イベント駆動：どれか1つでも完了するまで待つ
                            done, pending = await asyncio.wait(
                                active_tasks,
                                return_when=asyncio.FIRST_COMPLETED
                            )
                            # 完了したタスクを削除
                            for t in done:
                                active_tasks.remove(t)

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
            except Exception as e:
                processing_status['is_processing'] = False
                processing_status['logs'].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] ❌ エラー: {str(e)}"
                )
                print(f"[ERROR] バックグラウンド処理エラー: {e}")
            finally:
                # ハンドラーを削除
                thread_logger.remove(handler_id)

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

    if not processing_status['is_processing']:
        return jsonify({
            'success': False,
            'error': '実行中の処理がありません'
        }), 400

    # 停止フラグを立てる
    processing_status['is_processing'] = False
    processing_status['logs'].append(
        f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ ユーザーによって停止されました"
    )

    return jsonify({
        'success': True,
        'message': '処理を停止しました'
    })


if __name__ == '__main__':
    # 開発環境での実行
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
