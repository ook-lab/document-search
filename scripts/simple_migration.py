"""
簡易マイグレーションスクリプト
Supabase Python Client を使用してテーブル確認と作成を行う
"""

import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import DatabaseClient
import logging

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_and_report_migration_status():
    """マイグレーションの状態を確認してレポート"""
    db = DatabaseClient(use_service_role=True)

    logger.info("=" * 80)
    logger.info("マイグレーション状態チェック")
    logger.info("=" * 80)

    # 1. Rawdata_NETSUPER_items のカラムチェック
    logger.info("\n[1] Rawdata_NETSUPER_items テーブルのカラムチェック")
    result = db.client.table('Rawdata_NETSUPER_items').select('*').limit(1).execute()
    if result.data:
        columns = list(result.data[0].keys())
        logger.info(f"現在のカラム数: {len(columns)}")

        required_columns = ['general_name', 'needs_approval', 'classification_confidence']
        missing_columns = [col for col in required_columns if col not in columns]

        if missing_columns:
            logger.warning(f"❌ 不足しているカラム: {missing_columns}")
            logger.info("\n👉 マイグレーションが必要です:")
            logger.info("   Supabase Dashboard → SQL Editor で以下を実行してください:")
            logger.info("   " + "=" * 70)
            logger.info(f"   {root_dir / 'database' / 'migrations' / 'create_product_classification_system.sql'}")
            logger.info("   " + "=" * 70)
            return False
        else:
            logger.info(f"✅ 必要なカラムは全て存在します: {required_columns}")

    # 2. 新規テーブルのチェック
    logger.info("\n[2] 新規テーブルの存在チェック")

    tables_to_check = [
        'MASTER_Product_generalize',
        'MASTER_Product_classify',
        '99_tmp_gemini_clustering',
        '99_lg_gemini_classification_log'
    ]

    all_tables_exist = True
    for table_name in tables_to_check:
        try:
            result = db.client.table(table_name).select('*').limit(0).execute()
            logger.info(f"✅ {table_name} 存在")
        except Exception as e:
            logger.warning(f"❌ {table_name} 存在しない")
            all_tables_exist = False

    if not all_tables_exist:
        logger.warning("\n⚠️  一部のテーブルが存在しません")
        logger.info("Supabase Dashboard でマイグレーションSQLを実行してください")
        return False

    # 3. 商品データの確認
    logger.info("\n[3] 商品データの確認")
    result = db.client.table('Rawdata_NETSUPER_items').select('id', count='exact').execute()
    logger.info(f"全商品数: {result.count} 件")

    if missing_columns:  # general_nameがない場合は未分類数を数えられない
        logger.info("未分類商品数: （カラム不足のため確認不可）")
    else:
        result = db.client.table('Rawdata_NETSUPER_items').select('id', count='exact').is_('general_name', 'null').execute()
        logger.info(f"未分類商品数: {result.count} 件")

    logger.info("\n" + "=" * 80)
    if not missing_columns and all_tables_exist:
        logger.info("✅ マイグレーション完了済み - システム実行可能")
        return True
    else:
        logger.warning("⚠️  マイグレーション未完了 - 手動実行が必要")
        return False


if __name__ == "__main__":
    try:
        migration_ready = check_and_report_migration_status()

        if migration_ready:
            logger.info("\n次のステップ:")
            logger.info("  python3 L_product_classification/gemini_batch_clustering.py")
        else:
            logger.info("\nマイグレーション手順:")
            logger.info("  1. Supabase Dashboard にログイン")
            logger.info("  2. SQL Editor を開く")
            logger.info("  3. database/migrations/create_product_classification_system.sql の内容を貼り付けて実行")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
