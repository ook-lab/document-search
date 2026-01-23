"""
【廃止予定】捏造された日付を持つドキュメントを特定し、再処理用にpendingに戻す

このスクリプトの --check 機能は引き続き利用可能です（読み取り専用）。
--reset 機能は ops.py 経由に移行予定です。

移行先:
  確認: python scripts/reset/reset_fabricated_dates.py --check --workspace <ws>
  リセット: python scripts/ops.py reset-status --workspace <ws> [--apply]

このスクリプトは 2025年Q2 に削除予定です。
"""
import argparse
import re
import sys
from typing import List, Dict, Any

from _legacy_wrapper import show_deprecation_warning, reset_status_wrapper


def get_documents_with_date_suffix(db, workspace: str = 'all', limit: int = 1000) -> List[Dict[str, Any]]:
    """日付サフィックス付きのドキュメントを取得（読み取り専用）"""
    query = db.client.table('Rawdata_FILE_AND_MAIL')\
        .select('id, file_name, title, workspace, processing_status, metadata')\
        .eq('processing_status', 'completed')\
        .not_.is_('title', 'null')

    if workspace != 'all':
        query = query.eq('workspace', workspace)

    result = query.limit(limit).execute()

    if not result.data:
        return []

    # 日付サフィックスパターン
    date_pattern = re.compile(r'_\((\d{4}|\d{4}_\d{2}|\d{4}_\d{2}_\d{2}|YYYY|YYYY_MM|YYYY_MM_DD)\)$')

    docs_with_date = []
    for doc in result.data:
        title = doc.get('title', '')
        if date_pattern.search(title):
            docs_with_date.append(doc)

    return docs_with_date


def categorize_by_date_format(documents: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """日付形式でドキュメントを分類"""
    categories = {
        'YYYY_MM_DD': [],
        'YYYY_MM': [],
        'YYYY': [],
        'placeholder': []
    }

    for doc in documents:
        title = doc.get('title', '')

        if title.endswith('_(YYYY)'):
            categories['placeholder'].append(doc)
        elif re.search(r'_\(\d{4}_\d{2}_\d{2}\)$', title):
            categories['YYYY_MM_DD'].append(doc)
        elif re.search(r'_\(\d{4}_\d{2}\)$', title):
            categories['YYYY_MM'].append(doc)
        elif re.search(r'_\(\d{4}\)$', title):
            categories['YYYY'].append(doc)

    return categories


def check_documents(workspace: str = 'all', limit: int = 1000):
    """日付サフィックス付きドキュメントの統計を表示（読み取り専用）"""
    from shared.common.database.client import DatabaseClient
    db = DatabaseClient()

    print(f"\n日付サフィックス付きドキュメントを確認中 (workspace: {workspace})...\n")

    docs = get_documents_with_date_suffix(db, workspace, limit)

    if not docs:
        print(f"日付サフィックス付きのドキュメントが見つかりませんでした")
        return

    print(f"合計: {len(docs)}件\n")

    categories = categorize_by_date_format(docs)

    print("=== 日付形式別の統計 ===")
    print(f"完全な日付 _(YYYY_MM_DD): {len(categories['YYYY_MM_DD'])}件")
    print(f"年月のみ _(YYYY_MM): {len(categories['YYYY_MM'])}件")
    print(f"年のみ _(YYYY): {len(categories['YYYY'])}件")
    print(f"プレースホルダー _(YYYY): {len(categories['placeholder'])}件")
    print()

    print("=== 完全な日付のサンプル (最初の10件) ===")
    for doc in categories['YYYY_MM_DD'][:10]:
        title = doc.get('title', '(なし)')
        ws = doc.get('workspace', '(なし)')
        print(f"  - {title} (workspace: {ws})")

    if len(categories['YYYY_MM_DD']) > 10:
        print(f"  ... 他 {len(categories['YYYY_MM_DD']) - 10}件")
    print()

    print("=== 再処理の推奨 ===")
    print(f"対象となるドキュメント: {len(docs) - len(categories['placeholder'])}件")
    print("（プレースホルダー _(YYYY) は除外）")
    print()
    print("再処理するには:")
    print(f"  python scripts/ops.py reset-status --workspace {workspace}")
    print(f"  python scripts/ops.py reset-status --workspace {workspace} --apply")


def reset_to_pending(workspace: str = 'all', limit: int = 100, dry_run: bool = True):
    """日付サフィックス付きドキュメントをpendingに戻す（wrapper経由）"""
    show_deprecation_warning(
        old_script='reset_fabricated_dates.py --reset',
        new_command=f'python scripts/ops.py reset-status --workspace {workspace} [--apply]'
    )

    if workspace == 'all':
        print("[ERROR] workspace=all は危険なため無効化されています")
        print("代わりに、ワークスペースを個別に指定してください:")
        print("  python scripts/ops.py reset-status --workspace <ws> [--apply]")
        return 1

    return reset_status_wrapper(workspace=workspace, apply=not dry_run)


def main():
    parser = argparse.ArgumentParser(
        description='捏造された日付を持つドキュメントを特定し、再処理用にpendingに戻す'
    )
    parser.add_argument('--check', action='store_true', help='統計を表示（読み取り専用）')
    parser.add_argument('--reset', action='store_true', help='pendingに戻す（wrapper経由）')
    parser.add_argument('--workspace', type=str, default='all', help='対象ワークスペース')
    parser.add_argument('--limit', type=int, default=1000, help='最大件数')
    parser.add_argument('--no-dry-run', action='store_true', help='実際に実行')

    args = parser.parse_args()

    if args.check:
        check_documents(args.workspace, args.limit)
        return 0
    elif args.reset:
        return reset_to_pending(args.workspace, args.limit, dry_run=not args.no_dry_run)
    else:
        print("エラー: --check または --reset のいずれかを指定してください")
        print()
        print("使い方:")
        print("  python reset_fabricated_dates.py --check --workspace <ws>")
        print("  python reset_fabricated_dates.py --reset --workspace <ws> [--no-dry-run]")
        print()
        print("【推奨】新しいコマンド:")
        print("  python scripts/ops.py reset-status --workspace <ws> [--apply]")
        return 1


if __name__ == '__main__':
    sys.exit(main())
