"""
display_sender と display_subject カラムを削除するマイグレーション実行スクリプト
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
root_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import DatabaseClient
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """マイグレーション実行"""
    db = DatabaseClient(use_service_role=True)

    logger.info("=" * 80)
    logger.info("カラム削除マイグレーション開始")
    logger.info("=" * 80)

    # 削除前のカラム確認
    logger.info("\n[1] 削除前のカラム確認...")
    result_before = db.client.rpc(
        'execute_sql',
        {
            'query': """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'Rawdata_NETSUPER_items'
                  AND column_name IN ('display_sender', 'display_subject')
                ORDER BY column_name;
            """
        }
    ).execute()

    logger.info(f"削除前: {len(result_before.data)} カラムが存在")
    for col in result_before.data:
        logger.info(f"  - {col['column_name']}")

    # カラム削除
    logger.info("\n[2] カラム削除実行...")

    try:
        # display_sender を削除
        db.client.rpc(
            'execute_sql',
            {
                'query': 'ALTER TABLE "Rawdata_NETSUPER_items" DROP COLUMN IF EXISTS display_sender;'
            }
        ).execute()
        logger.info("✓ display_sender カラムを削除しました")

        # display_subject を削除
        db.client.rpc(
            'execute_sql',
            {
                'query': 'ALTER TABLE "Rawdata_NETSUPER_items" DROP COLUMN IF EXISTS display_subject;'
            }
        ).execute()
        logger.info("✓ display_subject カラムを削除しました")

    except Exception as e:
        logger.error(f"❌ カラム削除に失敗: {e}")
        return

    # 削除後のカラム確認
    logger.info("\n[3] 削除後のカラム確認...")
    result_after = db.client.rpc(
        'execute_sql',
        {
            'query': """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'Rawdata_NETSUPER_items'
                  AND column_name IN ('display_sender', 'display_subject')
                ORDER BY column_name;
            """
        }
    ).execute()

    if len(result_after.data) == 0:
        logger.info("✓ 削除成功: display_sender と display_subject は存在しません")
    else:
        logger.warning(f"⚠️  まだ {len(result_after.data)} カラムが残っています:")
        for col in result_after.data:
            logger.warning(f"  - {col['column_name']}")

    # 残存カラムの確認
    logger.info("\n[4] Rawdata_NETSUPER_items の全カラム一覧:")
    result_all = db.client.rpc(
        'execute_sql',
        {
            'query': """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'Rawdata_NETSUPER_items'
                ORDER BY ordinal_position;
            """
        }
    ).execute()

    for col in result_all.data:
        logger.info(f"  - {col['column_name']}: {col['data_type']}")

    logger.info("\n" + "=" * 80)
    logger.info("マイグレーション完了")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
