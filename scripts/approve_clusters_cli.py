"""
コマンドラインでクラスタを承認するスクリプト
"""

import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from shared.common.database.client import DatabaseClient
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def display_clusters():
    """承認待ちクラスタを表示"""
    db = DatabaseClient(use_service_role=True)

    logger.info("=" * 80)
    logger.info("承認待ちクラスタ一覧")
    logger.info("=" * 80)

    # 承認待ちクラスタを取得
    result = db.client.table('99_tmp_gemini_clustering').select(
        '*'
    ).eq('approval_status', 'pending').execute()

    clusters = result.data

    if not clusters:
        logger.info("承認待ちのクラスタはありません")
        return []

    logger.info(f"\n全 {len(clusters)} クラスタ\n")

    for i, cluster in enumerate(clusters, 1):
        logger.info(f"[{i}] 一般名詞: {cluster['general_name']}")
        logger.info(f"    カテゴリ: {cluster.get('category_name', '未設定')}")
        logger.info(f"    商品数: {len(cluster['product_ids'])}")
        logger.info(f"    信頼度: {cluster['confidence_avg']:.1%}")
        logger.info(f"    商品例: {', '.join(cluster['product_names'][:2])}")
        logger.info("")

    return clusters


def approve_all_clusters():
    """全クラスタを一括承認"""
    db = DatabaseClient(use_service_role=True)

    # カテゴリマスタから「食材」を取得
    categories_result = db.client.table('60_ms_categories').select(
        'id, name'
    ).eq('name', '食材').execute()

    if not categories_result.data:
        logger.error("❌ 「食材」カテゴリが見つかりません")
        logger.info("カテゴリを手動で作成するか、マイグレーションを確認してください")
        return

    category_id = categories_result.data[0]['id']
    logger.info(f"✅ カテゴリID取得: {category_id} (食材)")

    # 承認待ちクラスタを取得
    clusters_result = db.client.table('99_tmp_gemini_clustering').select(
        '*'
    ).eq('approval_status', 'pending').execute()

    clusters = clusters_result.data

    if not clusters:
        logger.info("承認待ちのクラスタはありません")
        return

    logger.info(f"\n{len(clusters)}件のクラスタを承認処理中...\n")

    approved_count = 0

    for cluster in clusters:
        cluster_id = cluster["id"]
        general_name = cluster["general_name"]
        product_ids = cluster["product_ids"]
        product_names = cluster["product_names"]
        confidence = cluster["confidence_avg"]

        try:
            # Tier 1: 各商品名 → general_name のマッピング
            for product_name in set(product_names):  # 重複排除
                db.client.table('MASTER_Product_generalize').upsert({
                    "raw_keyword": product_name,
                    "general_name": general_name,
                    "confidence_score": confidence,
                    "source": "gemini_batch"
                }, on_conflict="raw_keyword,general_name").execute()

            # Tier 2: general_name + context → category_id
            db.client.table('MASTER_Product_classify').upsert({
                "general_name": general_name,
                "source_type": "online_shop",
                "workspace": "shopping",
                "doc_type": "online shop",
                "organization": None,  # 全組織共通
                "category_id": category_id,
                "approval_status": "approved",
                "confidence_score": confidence
            }, on_conflict="general_name,source_type,workspace,doc_type,organization").execute()

            # Rawdata_NETSUPER_itemsを更新
            for product_id in product_ids:
                db.client.table('Rawdata_NETSUPER_items').update({
                    "general_name": general_name,
                    "category_id": category_id,
                    "needs_approval": False,
                    "classification_confidence": confidence
                }).eq('id', product_id).execute()

            # クラスタのステータスを更新
            db.client.table('99_tmp_gemini_clustering').update({
                "approval_status": "approved"
            }).eq('id', cluster_id).execute()

            approved_count += 1
            logger.info(f"✅ [{approved_count}/{len(clusters)}] {general_name} (商品数: {len(product_ids)})")

        except Exception as e:
            logger.error(f"❌ {general_name} の承認に失敗: {e}")
            continue

    logger.info(f"\n{'='*80}")
    logger.info(f"承認完了: {approved_count}/{len(clusters)} クラスタ")
    logger.info(f"{'='*80}")

    # 結果確認
    result = db.client.table('Rawdata_NETSUPER_items').select(
        'id', count='exact'
    ).eq('needs_approval', False).execute()

    logger.info(f"\n分類済み商品数: {result.count} 件")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='クラスタ承認スクリプト')
    parser.add_argument('--approve-all', action='store_true', help='全クラスタを一括承認')
    parser.add_argument('--list', action='store_true', help='クラスタ一覧を表示')

    args = parser.parse_args()

    if args.list:
        display_clusters()
    elif args.approve_all:
        approve_all_clusters()
    else:
        # デフォルト: 一覧表示後、承認確認
        clusters = display_clusters()

        if clusters:
            print("\n全てのクラスタを承認しますか？ (y/n): ", end='')
            response = input().strip().lower()

            if response == 'y':
                approve_all_clusters()
            else:
                logger.info("承認をキャンセルしました")
