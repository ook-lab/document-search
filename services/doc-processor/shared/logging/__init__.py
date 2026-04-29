"""
ロギングユーティリティ

Per-Task Logging機能を提供

【使用方法】
1. エントリーポイントでマスターログを設定:
   from shared.logging import setup_master_logging
   setup_master_logging()

2. タスク処理内でTaskLoggerを使用:
   from shared.logging import TaskLogger
   with TaskLogger(task_id="doc-12345"):
       logger.info("このログは tasks/doc-12345_*.log に出力")

【ログ出力先】
- マスターログ: logs/master_YYYYMMDD.log（システム全体のイベント）
- タスクログ: logs/tasks/{task_id}_{timestamp}.log（個別タスクの詳細）
"""
from .task_logger import (
    TaskLogger,
    setup_master_logging,
    task_logging,
    cleanup_all_handlers,
    get_active_task_count,
)

__all__ = [
    'TaskLogger',
    'setup_master_logging',
    'task_logging',
    'cleanup_all_handlers',
    'get_active_task_count',
]
