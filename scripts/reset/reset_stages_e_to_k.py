"""
【廃止予定】ステージE～Kのデータを削除し、processing_statusをpendingに戻す

移行先: python scripts/ops.py reset-stages --workspace <ws> [--status completed] [--apply]
        python scripts/ops.py reset-stages --doc-id <uuid> [--apply]

このスクリプトは 2025年Q2 に削除予定です。
"""
import sys
from _legacy_wrapper import show_deprecation_warning, reset_stages_wrapper


def main():
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv
    apply = skip_confirm  # --yes がある場合は apply とみなす

    if '--id' in sys.argv:
        idx = sys.argv.index('--id')
        if idx + 1 < len(sys.argv):
            doc_id = sys.argv[idx + 1]
            show_deprecation_warning(
                old_script='reset_stages_e_to_k.py',
                new_command=f'python scripts/ops.py reset-stages --doc-id {doc_id} [--apply]'
            )
            return reset_stages_wrapper(doc_id=doc_id, status='completed', apply=apply)
        else:
            print("エラー: --id の後にドキュメントIDを指定してください")
            return 1

    elif '--all' in sys.argv:
        show_deprecation_warning(
            old_script='reset_stages_e_to_k.py',
            new_command='python scripts/ops.py reset-stages --workspace all --status completed [--apply]'
        )
        # 全ワークスペースは危険なので wrapper でも禁止
        print("[ERROR] --all オプションは危険なため無効化されています")
        print("代わりに、ワークスペースを個別に指定してください:")
        print("  python scripts/ops.py reset-stages --workspace <ws> --status completed [--apply]")
        return 1

    elif len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        workspace = sys.argv[1]
        show_deprecation_warning(
            old_script='reset_stages_e_to_k.py',
            new_command=f'python scripts/ops.py reset-stages --workspace {workspace} --status completed [--apply]'
        )
        return reset_stages_wrapper(workspace=workspace, status='completed', apply=apply)

    else:
        print("使用方法:")
        print("  python reset_stages_e_to_k.py <workspace> [--yes]   # 指定ワークスペース")
        print("  python reset_stages_e_to_k.py --id <doc_id>         # 特定ドキュメント")
        print()
        print("【推奨】新しいコマンド:")
        print("  python scripts/ops.py reset-stages --workspace <ws> [--apply]")
        print("  python scripts/ops.py reset-stages --doc-id <uuid> [--apply]")
        return 0


if __name__ == '__main__':
    sys.exit(main())
