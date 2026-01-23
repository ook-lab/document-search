"""
Per-Task Logging Module

並列処理におけるログ出力を分離し、タスクごとに個別ログファイルを作成する。

【設計原則】
1. マスターログ (master.log): システム全体のイベント（起動、終了、リソース監視）
2. タスクログ (logs/tasks/{task_id}.log): 個別タスクの詳細処理ログ

【使用方法】
```python
from shared.logging import TaskLogger

# タスク処理内で使用
with TaskLogger(task_id="doc-12345"):
    logger.info("このログは tasks/doc-12345.log に出力される")
    # ... Stage E-K の処理 ...
```
"""
import sys
import threading
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Dict, Any
from loguru import logger

# プロジェクトルートを取得
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# スレッドローカルストレージ（現在のタスクIDを保持）
_thread_local = threading.local()

# アクティブなタスクハンドラーを管理（メモリリーク防止のため）
_active_handlers: Dict[str, int] = {}
_handlers_lock = threading.Lock()


def _get_current_task_id() -> Optional[str]:
    """現在のスレッドのタスクIDを取得"""
    return getattr(_thread_local, 'task_id', None)


def _task_filter(record: Dict[str, Any]) -> bool:
    """
    タスクログ用フィルター

    スレッドローカルに設定されたtask_idと、ハンドラーのtask_idが一致する場合のみ通す
    """
    handler_task_id = record.get('extra', {}).get('_task_handler_id')
    current_task_id = _get_current_task_id()

    # タスクハンドラー用: task_idが一致する場合のみ
    if handler_task_id:
        return current_task_id == handler_task_id

    # マスターログ用: タスク処理中でない場合、またはマスターフラグがある場合
    is_master = record.get('extra', {}).get('_master', False)
    return is_master or current_task_id is None


def setup_master_logging(
    log_dir: Optional[Path] = None,
    level: str = "INFO",
    rotation: str = "10 MB",
    retention: str = "7 days"
) -> Path:
    """
    マスターログを設定

    システム全体のイベント（起動、終了、エラー、リソース監視）を記録。
    タスク処理の詳細ログは含まれない。

    Args:
        log_dir: ログディレクトリ（デフォルト: logs/）
        level: ログレベル
        rotation: ローテーション設定
        retention: 保持期間

    Returns:
        マスターログファイルのパス
    """
    if log_dir is None:
        log_dir = _PROJECT_ROOT / 'logs'

    log_dir.mkdir(parents=True, exist_ok=True)

    # 日付付きマスターログファイル
    date_str = datetime.now().strftime('%Y%m%d')
    master_log_path = log_dir / f'master_{date_str}.log'

    # 既存のハンドラーをクリア（重複防止）
    logger.remove()

    # コンソール出力（マスターログ用フィルター付き）
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[task_id]}</cyan> | {message}",
        level=level,
        filter=lambda r: _task_filter(r),
        colorize=True
    )

    # マスターログファイル出力
    logger.add(
        master_log_path,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[task_id]} | {message}",
        level=level,
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        filter=lambda r: _task_filter(r) and not r.get('extra', {}).get('_task_handler_id')
    )

    # デフォルトのtask_idを設定（マスターログ用）
    logger.configure(extra={"task_id": "MASTER", "_master": True})

    logger.info(f"マスターログ設定完了: {master_log_path}")
    return master_log_path


class TaskLogger:
    """
    タスク単位のログ分離を実現するコンテキストマネージャー

    使用例:
    ```python
    with TaskLogger(task_id="doc-12345") as tl:
        logger.info("このログはタスク専用ファイルに出力")
        # 処理...

    # スコープを抜けると自動的にハンドラーがクリーンアップされる
    ```

    Features:
    - タスクごとに専用ログファイル (logs/tasks/{task_id}.log)
    - 例外発生時も確実にクリーンアップ
    - スレッドセーフ
    - 処理時間の自動計測
    """

    def __init__(
        self,
        task_id: str,
        log_dir: Optional[Path] = None,
        level: str = "DEBUG",
        include_timestamp: bool = True
    ):
        """
        Args:
            task_id: タスク識別子（ドキュメントIDなど）
            log_dir: ログディレクトリ（デフォルト: logs/tasks/）
            level: ログレベル
            include_timestamp: ファイル名にタイムスタンプを含めるか
        """
        self.task_id = task_id
        self.level = level
        self.include_timestamp = include_timestamp

        if log_dir is None:
            self.log_dir = _PROJECT_ROOT / 'logs' / 'tasks'
        else:
            self.log_dir = Path(log_dir)

        self.log_path: Optional[Path] = None
        self._handler_id: Optional[int] = None
        self._start_time: Optional[datetime] = None
        self._previous_task_id: Optional[str] = None

    def __enter__(self) -> 'TaskLogger':
        """コンテキスト開始: タスク専用ハンドラーを追加"""
        self._start_time = datetime.now()

        # ログディレクトリ作成
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # ログファイルパス生成
        if self.include_timestamp:
            # ミリ秒まで含めて衝突を防止
            timestamp = self._start_time.strftime('%Y%m%d_%H%M%S_%f')[:18]  # YYYYMMDD_HHMMSS_mmm
            # task_idが長い場合は短縮
            short_id = self.task_id[:8] if len(self.task_id) > 8 else self.task_id
            self.log_path = self.log_dir / f'{short_id}_{timestamp}.log'
        else:
            self.log_path = self.log_dir / f'{self.task_id}.log'

        # 前のタスクIDを保存（ネスト対応）
        self._previous_task_id = _get_current_task_id()

        # スレッドローカルにタスクIDを設定
        _thread_local.task_id = self.task_id

        # タスク専用ハンドラーを追加
        self._handler_id = logger.add(
            self.log_path,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
            level=self.level,
            encoding="utf-8",
            filter=lambda r, tid=self.task_id: r.get('extra', {}).get('task_id') == tid
        )

        # アクティブハンドラーとして登録
        with _handlers_lock:
            _active_handlers[self.task_id] = self._handler_id

        # loggerのextraを更新
        logger.configure(extra={"task_id": self.task_id, "_master": False})

        logger.info(f"===== タスク開始: {self.task_id} =====")
        logger.info(f"ログファイル: {self.log_path}")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """コンテキスト終了: ハンドラーをクリーンアップ"""
        end_time = datetime.now()
        duration = (end_time - self._start_time).total_seconds() if self._start_time else 0

        # 例外情報のログ出力
        if exc_type is not None:
            logger.error(f"===== タスク異常終了: {self.task_id} =====")
            logger.error(f"例外タイプ: {exc_type.__name__}")
            logger.error(f"例外メッセージ: {exc_val}")
            import traceback
            logger.error(f"スタックトレース:\n{''.join(traceback.format_tb(exc_tb))}")
        else:
            logger.info(f"===== タスク完了: {self.task_id} =====")

        logger.info(f"処理時間: {duration:.2f}秒")

        # ハンドラーを削除
        if self._handler_id is not None:
            try:
                logger.remove(self._handler_id)
            except ValueError:
                # 既に削除されている場合は無視
                pass

        # アクティブハンドラーから削除
        with _handlers_lock:
            _active_handlers.pop(self.task_id, None)

        # スレッドローカルを復元（ネスト対応）
        _thread_local.task_id = self._previous_task_id

        # loggerのextraを復元
        if self._previous_task_id:
            logger.configure(extra={"task_id": self._previous_task_id, "_master": False})
        else:
            logger.configure(extra={"task_id": "MASTER", "_master": True})

        # 例外は再送出（Falseを返す）
        return False

    @property
    def elapsed_time(self) -> float:
        """経過時間（秒）を取得"""
        if self._start_time is None:
            return 0.0
        return (datetime.now() - self._start_time).total_seconds()


@contextmanager
def task_logging(task_id: str, **kwargs):
    """
    タスクログのコンテキストマネージャー（関数版）

    TaskLoggerのショートカット。

    使用例:
    ```python
    with task_logging("doc-12345"):
        logger.info("タスク処理中...")
    ```
    """
    with TaskLogger(task_id, **kwargs) as tl:
        yield tl


def cleanup_all_handlers():
    """
    全てのアクティブなタスクハンドラーをクリーンアップ

    プロセス終了時やエラーリカバリ時に使用。
    """
    with _handlers_lock:
        for task_id, handler_id in list(_active_handlers.items()):
            try:
                logger.remove(handler_id)
                logger.info(f"ハンドラーをクリーンアップ: {task_id}")
            except ValueError:
                pass
        _active_handlers.clear()

    # スレッドローカルをリセット
    _thread_local.task_id = None


def get_active_task_count() -> int:
    """アクティブなタスクログハンドラーの数を取得"""
    with _handlers_lock:
        return len(_active_handlers)
