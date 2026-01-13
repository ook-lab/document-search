"""
状態管理モジュール（SSOT: Single Source of Truth）

DBの processing_lock テーブルを正とし、全プロセスがここ経由で状態を参照・更新
app.py と processor.py 間の状態同期問題を解消
"""
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List
from loguru import logger

# 定数
LOCK_TIMEOUT_SECONDS = 300  # ロックの有効期限（5分）
MAX_LOG_ENTRIES = 300       # 保持するログの最大件数
MAX_LOG_ENTRIES_DB = 100    # DBに保存するログの最大件数


class StateManager:
    """
    処理状態を一元管理するシングルトンクラス (SSOT)

    設計原則:
    - DBを正（SSOT）とし、メモリはキャッシュとして使用
    - 全ての状態変更はDBに即時反映
    - 複数インスタンス間の状態共有をサポート
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        from shared.common.database.client import DatabaseClient
        self._db = DatabaseClient(use_service_role=True)
        self._state_lock = threading.Lock()

        # メモリ上の状態キャッシュ（高速アクセス用）
        self._cache = {
            'is_processing': False,
            'current_index': 0,
            'total_count': 0,
            'current_file': '',
            'success_count': 0,
            'error_count': 0,
            'logs': [],
            'current_stage': '',
            'stage_progress': 0.0,
            'resource_control': {
                'throttle_delay': 0.0,
                'adjustment_count': 0,
                'max_parallel': 1,
                'current_workers': 0
            }
        }
        self._stop_requested = False
        self._initialized = True
        logger.info("StateManager initialized (SSOT mode)")

    @property
    def client(self):
        """Supabaseクライアントを取得"""
        return self._db.client

    # ========== 状態取得 ==========

    def get_status(self) -> Dict[str, Any]:
        """
        現在の状態を取得（メモリキャッシュから）

        高頻度アクセス用。DBとの同期はsync_from_db()で行う
        """
        with self._state_lock:
            return {
                'is_processing': self._cache['is_processing'],
                'stop_requested': self._stop_requested,
                'current_index': self._cache['current_index'],
                'total_count': self._cache['total_count'],
                'current_file': self._cache['current_file'],
                'success_count': self._cache['success_count'],
                'error_count': self._cache['error_count'],
                'logs': self._cache['logs'].copy(),
                'current_stage': self._cache['current_stage'],
                'stage_progress': self._cache['stage_progress'],
                'resource_control': self._cache['resource_control'].copy()
            }

    def get_status_from_db(self) -> Dict[str, Any]:
        """
        DBから最新の状態を取得（マルチインスタンス対応）
        """
        try:
            result = self.client.table('processing_lock').select('*').eq('id', 1).execute()
            if result.data:
                data = result.data[0]
                return {
                    'is_processing': data.get('is_processing', False),
                    'current_index': data.get('current_index', 0),
                    'total_count': data.get('total_count', 0),
                    'current_file': data.get('current_file', ''),
                    'success_count': data.get('success_count', 0),
                    'error_count': data.get('error_count', 0),
                    'logs': data.get('logs', []),
                    'cpu_percent': data.get('cpu_percent', 0.0),
                    'memory_percent': data.get('memory_percent', 0.0),
                    'memory_used_gb': data.get('memory_used_gb', 0.0),
                    'memory_total_gb': data.get('memory_total_gb', 0.0),
                    'throttle_delay': data.get('throttle_delay', 0.0),
                    'max_parallel': data.get('max_parallel', 1),
                    'current_workers': data.get('current_workers', 0),
                    'adjustment_count': data.get('adjustment_count', 0)
                }
        except Exception as e:
            logger.error(f"Failed to get status from DB: {e}")

        return self.get_status()

    # ========== ログ管理 ==========

    def add_log(self, message: str, level: str = 'INFO'):
        """
        ログを追加（メモリ + DB同期）
        """
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_msg = f"[{timestamp}] {message}"

        with self._state_lock:
            self._cache['logs'].append(formatted_msg)
            if len(self._cache['logs']) > MAX_LOG_ENTRIES:
                self._cache['logs'] = self._cache['logs'][-MAX_LOG_ENTRIES:]

        # ログレベルに応じて出力
        if level == 'ERROR':
            logger.error(message)
        elif level == 'WARNING':
            logger.warning(message)
        else:
            logger.info(message)

    # ========== 処理制御 ==========

    def check_lock(self) -> bool:
        """
        DBのロック状態を確認

        Returns:
            True: 処理中（ロックあり）
            False: アイドル（ロックなし）
        """
        try:
            result = self.client.table('processing_lock').select('is_processing, updated_at').eq('id', 1).execute()
            if result.data:
                lock = result.data[0]
                if lock.get('is_processing') and lock.get('updated_at'):
                    # タイムアウトチェック
                    updated_at = datetime.fromisoformat(lock['updated_at'].replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    elapsed = (now - updated_at).total_seconds()

                    if elapsed > LOCK_TIMEOUT_SECONDS:
                        logger.warning(f"Lock timed out ({elapsed:.0f}s). Auto-releasing.")
                        self._set_lock(False)
                        return False
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to check lock: {e}")
            return False

    def start_processing(self, total_count: int) -> bool:
        """
        処理を開始（ロック取得）

        Args:
            total_count: 処理予定のドキュメント数

        Returns:
            True: 開始成功
            False: 既に処理中
        """
        if self.check_lock():
            self.add_log("既に他のプロセスが実行中です", 'WARNING')
            return False

        # メモリ状態を初期化
        with self._state_lock:
            self._cache['is_processing'] = True
            self._cache['total_count'] = total_count
            self._cache['current_index'] = 0
            self._cache['current_file'] = '初期化中...'
            self._cache['success_count'] = 0
            self._cache['error_count'] = 0
            self._cache['logs'] = []
            self._cache['current_stage'] = ''
            self._cache['stage_progress'] = 0.0
            self._stop_requested = False
            # リソース制御を初期値にリセット
            self._cache['resource_control'] = {
                'throttle_delay': 0.0,
                'adjustment_count': 0,
                'max_parallel': 1,  # 初期値は1
                'current_workers': 0
            }

        # DBロック設定
        success = self._set_lock(True)
        if success:
            self.add_log(f"処理開始: {total_count}件")
            self._reset_stuck_documents()
        return success

    def stop_processing(self):
        """処理停止をリクエスト"""
        self._stop_requested = True
        self.add_log("停止リクエストを受け付けました。現在の処理完了後に停止します。", 'WARNING')

    def finish_processing(self):
        """処理を終了（ロック解除）"""
        with self._state_lock:
            self._cache['is_processing'] = False
            self._cache['current_file'] = ''
            self._stop_requested = False

        self._set_lock(False)
        self._reset_stuck_documents()
        self.add_log(
            f"処理完了: 成功={self._cache['success_count']}, "
            f"エラー={self._cache['error_count']}"
        )

    def is_stop_requested(self) -> bool:
        """停止がリクエストされているか"""
        return self._stop_requested

    # ========== 進捗更新 ==========

    def update_progress(
        self,
        index: int = None,
        filename: str = None,
        stage: str = None,
        stage_progress: float = None,
        success_inc: int = 0,
        error_inc: int = 0
    ):
        """進捗を更新（メモリのみ、DBへはsync_to_dbで同期）"""
        with self._state_lock:
            if index is not None:
                self._cache['current_index'] = index
            if filename is not None:
                self._cache['current_file'] = filename
            if stage is not None:
                self._cache['current_stage'] = stage
            if stage_progress is not None:
                self._cache['stage_progress'] = stage_progress
            self._cache['success_count'] += success_inc
            self._cache['error_count'] += error_inc

    def update_resource_control(
        self,
        throttle_delay: float = None,
        max_parallel: int = None,
        current_workers: int = None,
        adjustment_count: int = None
    ):
        """リソース制御情報を更新"""
        with self._state_lock:
            if throttle_delay is not None:
                self._cache['resource_control']['throttle_delay'] = throttle_delay
            if max_parallel is not None:
                self._cache['resource_control']['max_parallel'] = max_parallel
            if current_workers is not None:
                self._cache['resource_control']['current_workers'] = current_workers
            if adjustment_count is not None:
                self._cache['resource_control']['adjustment_count'] = adjustment_count

    # ========== DB同期 ==========

    def sync_to_db(self, cpu_percent: float = 0.0, memory_info: dict = None):
        """
        現在の状態をDBに同期（定期実行用）

        Args:
            cpu_percent: CPU使用率
            memory_info: メモリ情報 {'percent', 'used_gb', 'total_gb'}
        """
        try:
            with self._state_lock:
                data = {
                    'current_index': self._cache['current_index'],
                    'total_count': self._cache['total_count'],
                    'current_file': self._cache['current_file'],
                    'success_count': self._cache['success_count'],
                    'error_count': self._cache['error_count'],
                    'logs': self._cache['logs'][-MAX_LOG_ENTRIES_DB:],
                    'throttle_delay': self._cache['resource_control']['throttle_delay'],
                    'max_parallel': self._cache['resource_control']['max_parallel'],
                    'current_workers': self._cache['resource_control']['current_workers'],
                    'adjustment_count': self._cache['resource_control']['adjustment_count'],
                    'cpu_percent': cpu_percent,
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }

                if memory_info:
                    data['memory_percent'] = memory_info.get('percent', 0.0)
                    data['memory_used_gb'] = memory_info.get('used_gb', 0.0)
                    data['memory_total_gb'] = memory_info.get('total_gb', 0.0)

            self.client.table('processing_lock').update(data).eq('id', 1).execute()
            logger.debug("State synced to DB")
        except Exception as e:
            logger.error(f"Failed to sync to DB: {e}")

    def sync_from_db(self):
        """DBから状態を同期（マルチインスタンス対応）"""
        try:
            db_status = self.get_status_from_db()
            with self._state_lock:
                self._cache['is_processing'] = db_status.get('is_processing', False)
                self._cache['current_index'] = db_status.get('current_index', 0)
                self._cache['total_count'] = db_status.get('total_count', 0)
                self._cache['current_file'] = db_status.get('current_file', '')
                self._cache['success_count'] = db_status.get('success_count', 0)
                self._cache['error_count'] = db_status.get('error_count', 0)
                self._cache['logs'] = db_status.get('logs', [])
                self._cache['resource_control'] = {
                    'throttle_delay': db_status.get('throttle_delay', 0.0),
                    'max_parallel': db_status.get('max_parallel', 1),
                    'current_workers': db_status.get('current_workers', 0),
                    'adjustment_count': db_status.get('adjustment_count', 0)
                }
        except Exception as e:
            logger.error(f"Failed to sync from DB: {e}")

    # ========== リセット ==========

    def reset(self):
        """状態を完全リセット（緊急用）"""
        with self._state_lock:
            self._cache = {
                'is_processing': False,
                'current_index': 0,
                'total_count': 0,
                'current_file': '',
                'success_count': 0,
                'error_count': 0,
                'logs': [],
                'current_stage': '',
                'stage_progress': 0.0,
                'resource_control': {
                    'throttle_delay': 0.0,
                    'adjustment_count': 0,
                    'max_parallel': 1,
                    'current_workers': 0
                }
            }
            self._stop_requested = False

        # DB完全リセット
        try:
            self.client.table('processing_lock').update({
                'is_processing': False,
                'current_index': 0,
                'total_count': 0,
                'current_file': '',
                'success_count': 0,
                'error_count': 0,
                'logs': [],
                'current_workers': 0,
                'max_parallel': 1,
                'throttle_delay': 0.0,
                'adjustment_count': 0,
                'cpu_percent': 0.0,
                'memory_percent': 0.0,
                'memory_used_gb': 0.0,
                'memory_total_gb': 0.0,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }).eq('id', 1).execute()
            logger.info("State fully reset (memory + DB)")
        except Exception as e:
            logger.error(f"Failed to reset DB state: {e}")

        self._reset_stuck_documents()

    # ========== 内部メソッド ==========

    def _set_lock(self, is_processing: bool) -> bool:
        """DBのロック状態を設定"""
        try:
            data = {
                'is_processing': is_processing,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            if is_processing:
                data['started_at'] = datetime.now(timezone.utc).isoformat()
                # 処理開始時はリソース制御を初期値にリセット
                data['max_parallel'] = 1
                data['current_workers'] = 0
                data['throttle_delay'] = 0.0

            self.client.table('processing_lock').update(data).eq('id', 1).execute()
            logger.info(f"Lock set: {is_processing}")
            return True
        except Exception as e:
            logger.error(f"Failed to set lock: {e}")
            return False

    def _reset_stuck_documents(self):
        """processing状態でスタックしているドキュメントをpendingにリセット"""
        try:
            result = self.client.table('Rawdata_FILE_AND_MAIL').select('id').eq('processing_status', 'processing').execute()
            if result.data:
                stuck_ids = [row['id'] for row in result.data]
                if stuck_ids:
                    self.client.table('Rawdata_FILE_AND_MAIL').update({
                        'processing_status': 'pending'
                    }).in_('id', stuck_ids).execute()
                    logger.info(f"Reset {len(stuck_ids)} stuck documents to pending")
        except Exception as e:
            logger.error(f"Failed to reset stuck documents: {e}")


def get_state_manager() -> StateManager:
    """StateManagerのシングルトンインスタンスを取得"""
    return StateManager()
