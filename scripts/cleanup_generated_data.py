"""
AI生成データを全削除するクリーンアップスクリプト

削除対象:
1. Rawdata_NETSUPER_items の general_name, small_category, keywords (daily_auto_classifier.py の生成データ)
2. Rawdata_NETSUPER_items の embedding (generate_multi_embeddings.py の生成データ)

使い方:
    # general_name, small_category, keywordsのみ削除（embedding は残す）
    python K_kakeibo/cleanup_generated_data.py --general-name-only

    # embeddingのみ削除（general_name, small_category, keywords は残す）
    python K_kakeibo/cleanup_generated_data.py --embedding-only

    # 全て削除
    python K_kakeibo/cleanup_generated_data.py --all

    # ドライラン（確認のみ）
    python K_kakeibo/cleanup_generated_data.py --all --dry-run
"""

import sys
from pathlib import Path
import logging

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.common.database.client import DatabaseClient


def cleanup_general_names(db: DatabaseClient, dry_run: bool = False):
    """
    Rawdata_NETSUPER_items の general_name, small_category, keywords を削除

    Args:
        db: DatabaseClient インスタンス
        dry_run: True の場合は確認のみで削除しない
    """
    logger.info("=" * 80)
    logger.info("general_name, small_category, keywords の削除を開始")
    logger.info("=" * 80)

    # 削除対象のカウント（general_name または small_category が設定されている商品）
    result = db.client.table('Rawdata_NETSUPER_items').select('id').or_(
        'general_name.not.is.null,small_category.not.is.null'
    ).execute()
    count = len(result.data)

    logger.info(f"削除対象: general_name または small_category が設定されている商品 {count:,} 件")

    if dry_run:
        logger.warning("🔍 DRY RUN モード: 実際の削除は行いません")
        return count

    if count == 0:
        logger.info("✅ 削除対象がありません")
        return 0

    # NULL に設定して削除（全商品を対象に、これらのフィールドをNULLにする）
    try:
        # すべての商品のgeneral_name, small_category, keywordsをNULLに設定
        result = db.client.table('Rawdata_NETSUPER_items').update({
            'general_name': None,
            'small_category': None,
            'keywords': None
        }).or_('general_name.not.is.null,small_category.not.is.null').execute()

        logger.info(f"✅ {count:,} 件の general_name, small_category, keywords を削除しました")
        return count
    except Exception as e:
        logger.error(f"❌ 削除エラー: {e}")
        return 0


def cleanup_embeddings(db: DatabaseClient, dry_run: bool = False):
    """
    Rawdata_NETSUPER_items の embedding を削除

    Args:
        db: DatabaseClient インスタンス
        dry_run: True の場合は確認のみで削除しない
    """
    logger.info("=" * 80)
    logger.info("embedding の削除を開始")
    logger.info("=" * 80)

    # 削除対象のカウント
    result = db.client.table('Rawdata_NETSUPER_items').select('id').not_.is_('embedding', 'null').execute()
    count = len(result.data)

    logger.info(f"削除対象: embedding が設定されている商品 {count:,} 件")

    if dry_run:
        logger.warning("🔍 DRY RUN モード: 実際の削除は行いません")
        return count

    if count == 0:
        logger.info("✅ 削除対象がありません")
        return 0

    # NULL に設定して削除
    try:
        db.client.table('Rawdata_NETSUPER_items').update({
            'embedding': None
        }).not_.is_('embedding', 'null').execute()

        logger.info(f"✅ {count:,} 件の embedding を削除しました")
        return count
    except Exception as e:
        logger.error(f"❌ 削除エラー: {e}")
        return 0


def main():
    """メインエントリーポイント"""
    # コマンドライン引数のパース
    dry_run = '--dry-run' in sys.argv
    general_name_only = '--general-name-only' in sys.argv
    embedding_only = '--embedding-only' in sys.argv
    all_data = '--all' in sys.argv

    # 引数チェック
    if not (general_name_only or embedding_only or all_data):
        logger.error("❌ エラー: 削除対象を指定してください")
        logger.info("")
        logger.info("使い方:")
        logger.info("  python K_kakeibo/cleanup_generated_data.py --general-name-only  # general_name, small_category, keywords のみ削除")
        logger.info("  python K_kakeibo/cleanup_generated_data.py --embedding-only     # embedding のみ削除")
        logger.info("  python K_kakeibo/cleanup_generated_data.py --all                # 全て削除")
        logger.info("  python K_kakeibo/cleanup_generated_data.py --all --dry-run      # 確認のみ（削除しない）")
        sys.exit(1)

    # DB接続
    db = DatabaseClient(use_service_role=True)

    logger.info("=" * 80)
    logger.info("AI生成データ クリーンアップ")
    logger.info("=" * 80)

    if dry_run:
        logger.warning("🔍 DRY RUN モード: 実際の削除は行いません")

    total_deleted = 0

    # general_name と keywords の削除
    if general_name_only or all_data:
        count = cleanup_general_names(db, dry_run)
        total_deleted += count

    # embedding の削除
    if embedding_only or all_data:
        count = cleanup_embeddings(db, dry_run)
        total_deleted += count

    # 結果サマリー
    logger.info("")
    logger.info("=" * 80)
    logger.info("クリーンアップ完了")
    logger.info("=" * 80)

    if dry_run:
        logger.warning(f"DRY RUN: {total_deleted:,} 件が削除対象として検出されました（実際には削除されていません）")
    else:
        logger.info(f"✅ 合計 {total_deleted:,} 件のデータを削除しました")

    logger.info("")
    logger.info("次のステップ:")
    if general_name_only or all_data:
        logger.info("  python -m L_product_classification.daily_auto_classifier")
    if embedding_only or all_data:
        logger.info("  python netsuper_search_app/generate_multi_embeddings.py")

    logger.info("=" * 80)


if __name__ == "__main__":
    main()
