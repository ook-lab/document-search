"""
捏造された日付を持つドキュメントを特定し、再処理用にpendingに戻すスクリプト

使い方:
    # 日付サフィックス付きのドキュメントを確認
    python reset_fabricated_dates.py --check --workspace=ema_classroom

    # 特定のワークスペースを再処理用にリセット
    python reset_fabricated_dates.py --reset --workspace=ema_classroom --limit=50

    # 全ワークスペースを確認
    python reset_fabricated_dates.py --check --workspace=all
"""

import argparse
import re
from typing import List, Dict, Any
from shared.common.database.client import DatabaseClient


class FabricatedDateResetter:
    """捏造日付リセッター"""

    def __init__(self):
        self.db = DatabaseClient()

    def get_documents_with_date_suffix(
        self,
        workspace: str = 'all',
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        日付サフィックス付きのドキュメントを取得

        パターン:
        - _(YYYY_MM_DD)
        - _(YYYY_MM)
        - _(YYYY)
        - _(2025_12_04) など
        """
        query = self.db.client.table('Rawdata_FILE_AND_MAIL')\
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

    def categorize_by_date_format(self, documents: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """日付形式でドキュメントを分類"""
        categories = {
            'YYYY_MM_DD': [],  # 完全な日付
            'YYYY_MM': [],     # 年月のみ
            'YYYY': [],        # 年のみ
            'placeholder': []  # _(YYYY) プレースホルダー
        }

        for doc in documents:
            title = doc.get('title', '')

            # 日付サフィックスを抽出
            if title.endswith('_(YYYY)'):
                categories['placeholder'].append(doc)
            elif re.search(r'_\(\d{4}_\d{2}_\d{2}\)$', title):
                categories['YYYY_MM_DD'].append(doc)
            elif re.search(r'_\(\d{4}_\d{2}\)$', title):
                categories['YYYY_MM'].append(doc)
            elif re.search(r'_\(\d{4}\)$', title):
                categories['YYYY'].append(doc)

        return categories

    def check_documents(self, workspace: str = 'all', limit: int = 1000):
        """日付サフィックス付きドキュメントの統計を表示"""
        print(f"\n日付サフィックス付きドキュメントを確認中 (workspace: {workspace})...\n")

        docs = self.get_documents_with_date_suffix(workspace, limit)

        if not docs:
            print(f"日付サフィックス付きのドキュメントが見つかりませんでした")
            return

        print(f"合計: {len(docs)}件\n")

        # 日付形式で分類
        categories = self.categorize_by_date_format(docs)

        print("=== 日付形式別の統計 ===")
        print(f"完全な日付 _(YYYY_MM_DD): {len(categories['YYYY_MM_DD'])}件")
        print(f"年月のみ _(YYYY_MM): {len(categories['YYYY_MM'])}件")
        print(f"年のみ _(YYYY): {len(categories['YYYY'])}件")
        print(f"プレースホルダー _(YYYY): {len(categories['placeholder'])}件")
        print()

        # サンプル表示
        print("=== 完全な日付のサンプル (最初の10件) ===")
        for doc in categories['YYYY_MM_DD'][:10]:
            title = doc.get('title', '(なし)')
            workspace = doc.get('workspace', '(なし)')
            print(f"  - {title} (workspace: {workspace})")

        if len(categories['YYYY_MM_DD']) > 10:
            print(f"  ... 他 {len(categories['YYYY_MM_DD']) - 10}件")
        print()

        print("=== 年月のみのサンプル (最初の10件) ===")
        for doc in categories['YYYY_MM'][:10]:
            title = doc.get('title', '(なし)')
            workspace = doc.get('workspace', '(なし)')
            print(f"  - {title} (workspace: {workspace})")

        if len(categories['YYYY_MM']) > 10:
            print(f"  ... 他 {len(categories['YYYY_MM']) - 10}件")
        print()

        print("=== 再処理の推奨 ===")
        print(f"完全な日付 _(YYYY_MM_DD) の {len(categories['YYYY_MM_DD'])}件:")
        print("  → 文書内に明示的な発行日があるか確認が必要")
        print("  → sended_date と一致しているか確認が必要")
        print()
        print(f"年月のみ _(YYYY_MM) の {len(categories['YYYY_MM'])}件:")
        print("  → 文書内に年月の記載があるか確認が必要")
        print()
        print(f"プレースホルダー _(YYYY) の {len(categories['placeholder'])}件:")
        print("  → 正常（日付不明）")
        print()

    def reset_to_pending(
        self,
        workspace: str = 'all',
        limit: int = 100,
        dry_run: bool = True
    ):
        """
        日付サフィックス付きドキュメントをpendingに戻す

        Args:
            workspace: 対象ワークスペース
            limit: 処理する最大件数
            dry_run: Trueの場合、実際には更新せずに表示のみ
        """
        docs = self.get_documents_with_date_suffix(workspace, limit)

        if not docs:
            print(f"対象ドキュメントが見つかりませんでした")
            return

        # プレースホルダー_(YYYY)は除外（これは正常）
        docs_to_reset = [doc for doc in docs if not doc.get('title', '').endswith('_(YYYY)')]

        print(f"\n再処理対象: {len(docs_to_reset)}件")

        if dry_run:
            print("\n[DRY RUN] 以下のドキュメントがpendingに戻されます:")
            for i, doc in enumerate(docs_to_reset[:20], 1):
                title = doc.get('title', '(なし)')
                workspace = doc.get('workspace', '(なし)')
                print(f"{i}. {title} (workspace: {workspace})")

            if len(docs_to_reset) > 20:
                print(f"... 他 {len(docs_to_reset) - 20}件")

            print("\n実際に実行するには --no-dry-run オプションを追加してください")
            return

        # 実際に更新
        print("\n[実行中] ドキュメントをpendingに戻しています...")

        for doc in docs_to_reset:
            doc_id = doc['id']
            try:
                self.db.client.table('Rawdata_FILE_AND_MAIL')\
                    .update({'processing_status': 'pending'})\
                    .eq('id', doc_id)\
                    .execute()
                print(f"✓ {doc.get('title', '(なし)')}")
            except Exception as e:
                print(f"✗ {doc.get('title', '(なし)')}: {e}")

        print(f"\n[完了] {len(docs_to_reset)}件をpendingに戻しました")


def main():
    parser = argparse.ArgumentParser(
        description='捏造された日付を持つドキュメントを特定し、再処理用にpendingに戻す'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='日付サフィックス付きドキュメントの統計を表示'
    )
    parser.add_argument(
        '--reset',
        action='store_true',
        help='日付サフィックス付きドキュメントをpendingに戻す'
    )
    parser.add_argument(
        '--workspace',
        type=str,
        default='all',
        help='対象ワークスペース（デフォルト: all）'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=1000,
        help='処理する最大件数（デフォルト: 1000）'
    )
    parser.add_argument(
        '--no-dry-run',
        action='store_true',
        help='実際に更新を実行（デフォルトはdry run）'
    )

    args = parser.parse_args()

    resetter = FabricatedDateResetter()

    if args.check:
        resetter.check_documents(args.workspace, args.limit)
    elif args.reset:
        resetter.reset_to_pending(
            args.workspace,
            args.limit,
            dry_run=not args.no_dry_run
        )
    else:
        print("エラー: --check または --reset のいずれかを指定してください")
        print("使い方: python reset_fabricated_dates.py --help")


if __name__ == '__main__':
    main()
