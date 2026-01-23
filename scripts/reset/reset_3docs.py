"""
【廃止】3件のドキュメントをリセット

移行先: python scripts/ops.py reset-stages --doc-id <uuid> [--apply]
        （複数件は1件ずつ実行してください）

このスクリプトはハードコードされた doc_id を含むため廃止されました。
"""
import sys
from _legacy_wrapper import show_stub_message

show_stub_message(
    old_script='reset_3docs.py',
    new_command='python scripts/ops.py reset-stages --doc-id <uuid> [--apply]'
)
