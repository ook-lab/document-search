"""
【廃止】特定ドキュメントを pending に戻す

移行先: python scripts/ops.py reset-status --doc-id <uuid> [--apply]

このスクリプトはハードコードされた doc_id を含むため廃止されました。
"""
import sys
from _legacy_wrapper import show_stub_message

show_stub_message(
    old_script='reset_doc.py',
    new_command='python scripts/ops.py reset-status --doc-id <uuid> [--apply]'
)
