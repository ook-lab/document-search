"""
レガシースクリプト用 wrapper 共通モジュール

【設計原則】
- 旧スクリプト（reset_*.py）は全てこのモジュールを通じて ops.py を呼ぶ
- DB 直更新は禁止（SSOT 違反）
- 設計者が旧スクリプトを見ても誤解しない

【廃止予定】
- 2025年Q2: 全 wrapper を stub 化（案内メッセージのみ）
- 2025年Q3: 完全削除
"""
import subprocess
import sys
from pathlib import Path


# ops.py へのパス
OPS_PY = Path(__file__).resolve().parent.parent / 'ops.py'


def show_deprecation_warning(old_script: str, new_command: str):
    """廃止警告を表示"""
    print("\n" + "="*70)
    print(f"【警告】このスクリプト ({old_script}) は非推奨です")
    print("="*70)
    print(f"\n移行先コマンド:")
    print(f"  {new_command}")
    print("\nこのスクリプトは将来削除されます（2025年Q2予定）")
    print("="*70 + "\n")


def run_ops_command(args: list, dry_run: bool = True) -> int:
    """ops.py を実行

    Args:
        args: ops.py に渡す引数リスト（例: ['reset-status', '--workspace', 'foo']）
        dry_run: True なら --apply を付けない（デフォルト）

    Returns:
        終了コード
    """
    cmd = [sys.executable, str(OPS_PY)] + args
    if not dry_run and '--apply' not in args:
        cmd.append('--apply')

    print(f"[実行] {' '.join(cmd)}\n")
    return subprocess.call(cmd)


def reset_status_wrapper(workspace: str = None, doc_id: str = None, apply: bool = False) -> int:
    """processing→pending リセットの wrapper

    移行先: python ops.py reset-status --workspace <ws> [--apply]
    """
    args = ['reset-status']

    if doc_id:
        args.extend(['--doc-id', doc_id])
    elif workspace:
        args.extend(['--workspace', workspace])
    else:
        print("[ERROR] workspace または doc_id を指定してください")
        return 1

    return run_ops_command(args, dry_run=not apply)


def reset_stages_wrapper(workspace: str = None, doc_id: str = None,
                         status: str = 'completed', apply: bool = False) -> int:
    """ステージE-Kクリアの wrapper

    移行先: python ops.py reset-stages --workspace <ws> [--status <status>] [--apply]
    """
    args = ['reset-stages']

    if doc_id:
        args.extend(['--doc-id', doc_id])
    elif workspace:
        args.extend(['--workspace', workspace])
    else:
        print("[ERROR] workspace または doc_id を指定してください")
        return 1

    if status and status != 'completed':
        args.extend(['--status', status])

    return run_ops_command(args, dry_run=not apply)


def show_stub_message(old_script: str, new_command: str):
    """完全廃止時のスタブメッセージを表示"""
    print("\n" + "="*70)
    print(f"【エラー】このスクリプト ({old_script}) は廃止されました")
    print("="*70)
    print(f"\n代わりに以下を使用してください:")
    print(f"  {new_command}")
    print("\n詳細: docs/OPERATIONS.md を参照")
    print("="*70 + "\n")
    sys.exit(410)  # 410 Gone
