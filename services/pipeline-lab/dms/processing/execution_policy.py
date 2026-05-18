"""
ExecutionPolicy - 実行可否判断（将来のポリシー拡張用）

呼び出し側が処理対象（pipeline_meta.id 等）を選ぶ前提で、現状は常に許可する。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    """実行可否判断の結果"""
    allowed: bool
    deny_code: Optional[str] = None
    deny_reason: Optional[str] = None

    def __bool__(self):
        return self.allowed


class ExecutionPolicy:
    """実行可否判断（現状は常に許可）。"""

    def __init__(self, db_client=None):
        pass  # DB不要

    def can_execute(
        self,
        doc_id: Optional[str] = None,
        workspace: Optional[str] = None
    ) -> ExecutionResult:
        """常に許可"""
        return ExecutionResult(allowed=True)

    def can_execute_document(self, doc: dict) -> ExecutionResult:
        """常に許可"""
        return ExecutionResult(allowed=True)


# シングルトンインスタンス
_execution_policy_instance: Optional[ExecutionPolicy] = None


def get_execution_policy() -> ExecutionPolicy:
    """ExecutionPolicy のシングルトンインスタンスを取得"""
    global _execution_policy_instance
    if _execution_policy_instance is None:
        _execution_policy_instance = ExecutionPolicy()
    return _execution_policy_instance
