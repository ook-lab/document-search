"""
【廃止予定】Rawdata_NETSUPER_itemsの既存商品にgeneral_nameを一括設定

⚠️ このスクリプトは廃止予定です。
代わりに L_product_classification/daily_auto_classifier.py を使用してください。

理由:
- このスクリプトは general_name と keywords のみ生成
- 新しいスクリプトは general_name, small_category, keywords の3つすべてを Gemini 2.5 Flash で生成

正しい実行手順:
    # 1. 既存データを全削除
    python K_kakeibo/cleanup_generated_data.py --all

    # 2. 全商品を分類（general_name, small_category, keywords を生成）
    python -m L_product_classification.daily_auto_classifier

    # 3. embedding を生成
    python netsuper_search_app/generate_multi_embeddings.py

【旧スクリプトの使い方】（使用非推奨）
    # 全商品を更新
    python K_kakeibo/sync_netsuper_general_names.py

    # 最初の100件のみ（テスト用）
    python K_kakeibo/sync_netsuper_general_names.py --limit=100

    # ドライラン（確認のみ、更新しない）
    python K_kakeibo/sync_netsuper_general_names.py --dry-run
"""

import sys
from pathlib import Path
from typing import Optional
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

from shared.kakeibo.transaction_processor import TransactionProcessor


def sync_general_names(limit: Optional[int] = None, dry_run: bool = False):
    """
    Rawdata_NETSUPER_itemsの商品にgeneral_nameを設定

    Args:
        limit: 処理件数の上限（Noneの場合は全件）
        dry_run: Trueの場合は確認のみで更新しない
    """
    logger.info("=" * 80)
    logger.info("Rawdata_NETSUPER_items general_name 同期開始")
    logger.info("=" * 80)

    if dry_run:
        logger.warning("🔍 DRY RUN モード: 実際の更新は行いません")

    # TransactionProcessorを初期化（商品マッピングを読み込み）
    processor = TransactionProcessor()
    db = processor.db

    # general_nameが未設定の商品のみ取得（処理済みは除外）
    logger.info("未処理の商品データを取得中...")

    # general_nameが未設定の商品のみ処理
    query = db.table('Rawdata_NETSUPER_items').select('id, product_name').is_('general_name', 'null')

    if limit:
        query = query.limit(limit)
        logger.info(f"処理上限: {limit}件")

    result = query.execute()
    products = result.data

    if not products:
        logger.info("✅ general_nameが未設定の商品はありません")
        return

    logger.info(f"対象商品: {len(products)}件")

    # 統計情報
    stats = {
        'total': len(products),
        'matched': 0,
        'not_matched': 0,
        'updated': 0,
        'errors': 0,
        'mappings_saved': 0
    }

    # 各商品にgeneral_nameとkeywordsを設定
    for i, product in enumerate(products, 1):
        product_id = product['id']
        product_name = product.get('product_name', '')

        if not product_name:
            logger.debug(f"[{i}/{len(products)}] ID={product_id}: 商品名が空 - スキップ")
            stats['not_matched'] += 1
            continue

        # general_nameとkeywordsを取得
        result = processor._get_general_name_and_keywords(product_name)

        if result:
            general_name = result.get('general_name')
            keywords = result.get('keywords', [])

            stats['matched'] += 1
            logger.info(f"[{i}/{len(products)}] {product_name}")
            logger.info(f"  → general_name: {general_name}")
            logger.info(f"  → keywords: {keywords}")

            if not dry_run:
                try:
                    # SupabaseのJSONBカラムにはPythonのlistを直接渡す
                    db.table('Rawdata_NETSUPER_items').update({
                        'general_name': general_name,
                        'keywords': keywords  # json.dumps()は不要
                    }).eq('id', product_id).execute()
                    stats['updated'] += 1

                    # AIが生成したマッピングをMASTER_Product_generalizeに蓄積
                    # 既存チェック後、なければ追加
                    existing = db.table('MASTER_Product_generalize').select('id').eq('raw_keyword', product_name.lower()).execute()
                    if not existing.data:
                        db.table('MASTER_Product_generalize').insert({
                            'raw_keyword': product_name.lower(),
                            'general_name': general_name,
                            'confidence_score': 1.0,
                            'source': 'ai_generated',
                            'notes': f'AI抽出: {", ".join(keywords[:3])}'
                        }).execute()
                        stats['mappings_saved'] += 1
                        logger.debug(f"  ✓ マッピング蓄積: {product_name} → {general_name}")

                except Exception as e:
                    logger.error(f"更新エラー (ID={product_id}): {e}")
                    stats['errors'] += 1
        else:
            stats['not_matched'] += 1
            logger.debug(f"[{i}/{len(products)}] {product_name} → マッチなし")

        # 進捗表示（100件ごと）
        if i % 100 == 0:
            logger.info(f"進捗: {i}/{len(products)} ({i*100//len(products)}%)")

    # 結果サマリー
    logger.info("\n" + "=" * 80)
    logger.info("処理完了")
    logger.info("=" * 80)
    logger.info(f"対象商品数:       {stats['total']:,}件")
    logger.info(f"マッチした商品:   {stats['matched']:,}件 ({stats['matched']*100//stats['total'] if stats['total'] > 0 else 0}%)")
    logger.info(f"マッチなし:       {stats['not_matched']:,}件")

    if not dry_run:
        logger.info(f"更新成功:         {stats['updated']:,}件")
        logger.info(f"マッピング蓄積:   {stats['mappings_saved']:,}件")
        if stats['errors'] > 0:
            logger.warning(f"更新エラー:       {stats['errors']:,}件")
    else:
        logger.warning("DRY RUN: 実際の更新は行われていません")

    logger.info("=" * 80)

    # マッチ率が低い場合の警告
    if stats['total'] > 0 and stats['matched'] * 100 // stats['total'] < 30:
        logger.warning("\n⚠️  マッチ率が30%未満です")
        logger.warning("MASTER_Product_generalizeテーブルにより多くの商品を追加することを検討してください")
        logger.warning("または L_product_classification/daily_auto_classifier.py で自動クラスタリングを実行してください")


def main():
    """メインエントリーポイント"""
    # コマンドライン引数のパース
    dry_run = '--dry-run' in sys.argv
    limit = None

    for arg in sys.argv:
        if arg.startswith('--limit='):
            try:
                limit = int(arg.split('=')[1])
            except ValueError:
                logger.error(f"無効なlimit値: {arg}")
                sys.exit(1)

    # 実行
    sync_general_names(limit=limit, dry_run=dry_run)


if __name__ == "__main__":
    main()
