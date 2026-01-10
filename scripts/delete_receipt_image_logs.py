"""
レシート画像処理ログを削除

99_lg_image_proc_log テーブルからレシート関連のログを削除
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from shared.common.database.client import DatabaseClient


def delete_image_logs():
    """画像処理ログを削除"""
    db = DatabaseClient(use_service_role=True)

    print("="*80)
    print("レシート画像処理ログ削除")
    print("="*80)

    # 99_lg_image_proc_log を確認
    print("\n99_lg_image_proc_log の件数を確認中...")
    result = db.client.table('99_lg_image_proc_log').select('id', count='exact').execute()
    count = result.count

    print(f"削除対象: {count}件")

    if count > 0:
        response = input(f"\n{count}件のログを削除しますか？ (yes/no): ")
        if response.lower() == 'yes':
            db.client.table('99_lg_image_proc_log').delete().neq('id', '00000000-0000-0000-0000-000000000000').execute()
            print(f"✅ {count}件のログを削除しました")
        else:
            print("❌ 削除をキャンセルしました")
    else:
        print("ℹ️  削除対象なし")

    print("="*80)


if __name__ == "__main__":
    delete_image_logs()
