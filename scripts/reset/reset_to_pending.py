"""
【廃止予定】processing 状態のドキュメントを pending に戻す

移行先: python scripts/ops.py reset-status --workspace <ws> [--apply]

このスクリプトは 2025年Q2 に削除予定です。
"""
import sys
from _legacy_wrapper import show_deprecation_warning, reset_status_wrapper


def main():
    workspace = sys.argv[1] if len(sys.argv) > 1 else 'ikuya_classroom'
    apply = '--apply' in sys.argv or '--yes' in sys.argv or '-y' in sys.argv

    show_deprecation_warning(
        old_script='reset_to_pending.py',
        new_command=f'python scripts/ops.py reset-status --workspace {workspace} [--apply]'
    )

    return reset_status_wrapper(workspace=workspace, apply=apply)


if __name__ == '__main__':
    sys.exit(main())
