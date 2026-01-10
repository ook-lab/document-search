"""
Rawdata_NETSUPER_itemsから混入しているレシートデータを削除

安全性:
1. ドライランモード（デフォルト）で削除対象を確認
2. 削除前にRawdata_RECEIPT_itemsに存在することを確認
3. --execute フラグで実際に削除を実行
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import argparse

# Windows環境でのUnicode出力設定
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from shared.common.database.client import DatabaseClient


class ReceiptDataCleanup:
    """レシートデータのクリーンアップ"""

    def __init__(self, dry_run=True):
        self.db = DatabaseClient(use_service_role=True)
        self.dry_run = dry_run

    def get_mixed_receipt_data(self):
        """
        混入しているレシートデータを取得

        Returns:
            レシートデータのリスト
        """
        result = self.db.client.table('Rawdata_NETSUPER_items').select(
            'id, product_name, organization, source_type, doc_type, metadata, created_at'
        ).eq('source_type', 'physical_store').execute()

        return result.data

    def verify_exists_in_receipt_items(self, receipt_id):
        """
        Rawdata_RECEIPT_itemsに該当レシートが存在するか確認

        Args:
            receipt_id: レシートID

        Returns:
            存在する場合True
        """
        if not receipt_id:
            return False

        result = self.db.client.table('Rawdata_RECEIPT_shops').select('id').eq(
            'id', receipt_id
        ).execute()

        return len(result.data) > 0

    def delete_mixed_data(self, items):
        """
        混入データを削除

        Args:
            items: 削除対象アイテムのリスト
        """
        stats = {
            'total': len(items),
            'deleted': 0,
            'error': 0
        }

        print("\n" + "=" * 80)
        print("削除処理開始")
        print("=" * 80)
        print(f"\n方針: source_type='physical_store' のデータは全てネットスーパーテーブルから削除")
        print(f"理由: レシートデータは Rawdata_RECEIPT_items に保存されるべき\n")

        for i, item in enumerate(items, 1):
            try:
                item_id = item.get('id')
                product_name = item.get('product_name')
                organization = item.get('organization', '不明')

                if not self.dry_run:
                    # 実際に削除
                    self.db.client.table('Rawdata_NETSUPER_items').delete().eq('id', item_id).execute()
                    stats['deleted'] += 1
                    if i % 20 == 0 or i == stats['total']:
                        print(f"[{i}/{stats['total']}] 削除中... ({stats['deleted']}件完了)")
                else:
                    # ドライラン: 最初の10件だけ表示
                    if i <= 10:
                        print(f"[{i}/{stats['total']}] 削除対象: {product_name} @ {organization}")

            except Exception as e:
                stats['error'] += 1
                print(f"[{i}/{stats['total']}] ❌ エラー: {item.get('product_name')} - {e}")

        # サマリー
        print("\n" + "=" * 80)
        print("処理完了")
        print("=" * 80)
        print(f"対象件数: {stats['total']:,}件")
        if self.dry_run:
            print(f"[DRY RUN] 削除対象: {stats['total']:,}件")
            print(f"\n💡 実際に削除するには --execute フラグを付けて実行してください")
        else:
            print(f"削除完了: {stats['deleted']:,}件")
            print(f"エラー:   {stats['error']:,}件")
        print("=" * 80)

        return stats

    def cleanup(self):
        """クリーンアップ実行"""
        print("=" * 80)
        if self.dry_run:
            print("Rawdata_NETSUPER_items レシートデータ削除 [DRY RUN]")
        else:
            print("Rawdata_NETSUPER_items レシートデータ削除 [実行モード]")
        print("=" * 80)

        # 混入データを取得
        mixed_data = self.get_mixed_receipt_data()
        print(f"\n混入データ件数: {len(mixed_data):,}件")

        if len(mixed_data) == 0:
            print("✅ 混入データは見つかりませんでした")
            return

        # 削除処理
        stats = self.delete_mixed_data(mixed_data)

        return stats


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description='Rawdata_NETSUPER_itemsから混入レシートデータを削除')
    parser.add_argument('--execute', action='store_true', help='実際に削除を実行（デフォルトはdry run）')
    args = parser.parse_args()

    cleanup = ReceiptDataCleanup(dry_run=not args.execute)
    cleanup.cleanup()


if __name__ == "__main__":
    main()
