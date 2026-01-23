"""
【廃止予定】全ステータスのドキュメントのステージE～Kのデータを削除

移行先: python scripts/ops.py reset-stages --workspace <ws> [--apply]
        （--status オプションなしで全ステータスが対象）

このスクリプトは 2025年Q2 に削除予定です。
"""
import sys
from _legacy_wrapper import show_deprecation_warning, reset_stages_wrapper


def main():
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv
    apply = skip_confirm  # --yes がある場合は apply とみなす

    if '--all' in sys.argv:
        show_deprecation_warning(
            old_script='reset_all_stages_e_to_k.py',
            new_command='python scripts/ops.py reset-stages --workspace all [--apply]'
        )
        # 全ワークスペースは危険なので wrapper でも禁止
        print("[ERROR] --all オプションは危険なため無効化されています")
        print("代わりに、ワークスペースを個別に指定してください:")
        print("  python scripts/ops.py reset-stages --workspace <ws> [--apply]")
        return 1

    elif len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        workspace = sys.argv[1]
        show_deprecation_warning(
            old_script='reset_all_stages_e_to_k.py',
            new_command=f'python scripts/ops.py reset-stages --workspace {workspace} [--apply]'
        )
        # status=None で全ステータスが対象
        return reset_stages_wrapper(workspace=workspace, status=None, apply=apply)

    else:
        print("使用方法:")
        print("  python reset_all_stages_e_to_k.py <workspace> [--yes]   # 指定ワークスペース")
        print()
        print("【推奨】新しいコマンド:")
        print("  python scripts/ops.py reset-stages --workspace <ws> [--apply]")
        return 0


if __name__ == '__main__':
    sys.exit(main())
