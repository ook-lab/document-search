"""
処理関連モジュール

- StateManager: 状態管理（SSOT）
- DocumentProcessor: ドキュメント処理
- AdaptiveResourceManager: リソース動的調整
"""
from .state_manager import StateManager, get_state_manager
from .processor import DocumentProcessor, continuous_processing_loop
from .resource_manager import (
    AdaptiveResourceManager,
    get_cgroup_memory,
    get_cgroup_cpu
)

__all__ = [
    'StateManager',
    'get_state_manager',
    'DocumentProcessor',
    'continuous_processing_loop',
    'AdaptiveResourceManager',
    'get_cgroup_memory',
    'get_cgroup_cpu'
]
