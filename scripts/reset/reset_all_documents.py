"""
【廃止】全ドキュメントをリセット

このスクリプトは事故リスクが高いため廃止されました。

代替手段:
  1. 特定ワークスペースのみ:
     python scripts/ops.py reset-stages --workspace <ws> [--apply]

  2. 全ワークスペースが本当に必要な場合:
     ops.py を直接実行するのではなく、ワークスペースごとに実行してください。
     これは意図的な設計です（事故防止）。

  3. 本当に全件が必要な場合は、DBを直接操作してください（要バックアップ）。
"""
import sys
from _legacy_wrapper import show_stub_message

show_stub_message(
    old_script='reset_all_documents.py',
    new_command='python scripts/ops.py reset-stages --workspace <ws> [--apply]'
)
