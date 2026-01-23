"""
【廃止】単一ドキュメントを処理

移行先: python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute

このスクリプトはハードコードされた doc_id を含むため廃止されました。
処理実行は process_queued_documents.py のみを使用してください（入口の統一）。
"""
import sys

print("\n" + "=" * 70)
print("【エラー】このスクリプト (process_single_doc.py) は廃止されました")
print("=" * 70)
print("\n代わりに以下を使用してください:")
print("  python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute")
print("\n例:")
print("  python scripts/processing/process_queued_documents.py --doc-id 2a16467c-435b-44ab-80f8-d9f8c1670495 --execute")
print("\n詳細: docs/OPERATIONS.md を参照")
print("=" * 70 + "\n")
sys.exit(410)  # 410 Gone
