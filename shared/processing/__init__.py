"""
処理関連モジュール

- ExecutionPolicy: 実行可否判断のSSOT（必ずここを通る）
- StateManager: 状態管理（SSOT）
- DocumentProcessor: ドキュメント処理
- AdaptiveResourceManager: リソース動的調整

【設計原則】
- 常駐禁止: continuous_processing_loop は削除済み
- バッチ1回実行: Cloud Run Jobs / ローカル両用
"""
from .execution_policy import ExecutionPolicy, ExecutionResult, get_execution_policy
from .state_manager import StateManager, get_state_manager
from .processor import DocumentProcessor
from .resource_manager import (
    AdaptiveResourceManager,
    get_cgroup_memory,
    get_cgroup_cpu
)

__all__ = [
    'ExecutionPolicy',
    'ExecutionResult',
    'get_execution_policy',
    'StateManager',
    'get_state_manager',
    'DocumentProcessor',
    'AdaptiveResourceManager',
    'get_cgroup_memory',
    'get_cgroup_cpu'
]
