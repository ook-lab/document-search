"""
ダイエーネットスーパー 商品データ取得メインスクリプト

使い方:
    # 全カテゴリーを処理（デフォルト）
    python process_daiei.py

    # 特定のカテゴリーのみ処理
    python process_daiei.py --category "野菜" --category "果物"

    # ヘッドレスモードをオフにして動作確認
    python process_daiei.py --no-headless
"""

import os
import sys
import json
import asyncio
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "services" / "data-ingestion"))

# .envファイルを読み込む
from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from daiei.product_ingestion import DaieiProductIngestionPipeline

# ロガー設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('daiei_output.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def process_all_categories(
    pipeline: DaieiProductIngestionPipeline,
    target_categories: List[str] = None,
    max_pages_per_category: int = 100
) -> Dict[str, Any]:
    """
    すべてのカテゴリーを処理

    Args:
        pipeline: 商品取得パイプライン
        target_categories: 処理対象のカテゴリー名リスト（Noneなら全カテゴリー）
        max_pages_per_category: カテゴリーあたりの最大ページ数

    Returns:
        処理結果のサマリー
    """
    logger.info("=" * 80)
    logger.info("ダイエーネットスーパー 商品データ取得開始")
    logger.info("=" * 80)

    start_time = datetime.now()

    # ログイン後に取得した店舗IDを使用してカテゴリーURLを構築
    store_id = pipeline.scraper.store_id
    if not store_id:
        logger.error("❌ 店舗IDが取得できていません")
        return {}

    # カテゴリー定義（classL, classS, ilc_code のパラメータで指定）
    # 実際のカテゴリーはトップページから動的に取得するのが理想だが、まずは主要カテゴリーを手動定義
    category_params = [
        {"name": "野菜・果物", "classL": "2", "classS": "1", "ilc_code": "1001"},
        {"name": "肉加工品", "classL": "2", "classS": "2", "ilc_code": "1002"},
        {"name": "魚", "classL": "2", "classS": "3", "ilc_code": "1003"},
        {"name": "惣菜・弁当", "classL": "2", "classS": "4", "ilc_code": "1004"},
        {"name": "パン・乳製品", "classL": "2", "classS": "5", "ilc_code": "1005"},
        {"name": "冷凍食品・アイス", "classL": "2", "classS": "6", "ilc_code": "1006"},
        {"name": "冷蔵食品", "classL": "2", "classS": "7", "ilc_code": "1007"},
        {"name": "調味料・即席食品", "classL": "2", "classS": "8", "ilc_code": "1008"},
        {"name": "米・乾物", "classL": "2", "classS": "9", "ilc_code": "1009"},
        {"name": "菓子", "classL": "2", "classS": "10", "ilc_code": "1010"},
        {"name": "飲料", "classL": "2", "classS": "11", "ilc_code": "1011"},
        {"name": "酒類", "classL": "2", "classS": "12", "ilc_code": "1012"},
        {"name": "医薬品", "classL": "2", "classS": "13", "ilc_code": "1013"},
        {"name": "健康・美容・日用品", "classL": "2", "classS": "14", "ilc_code": "1014"},
        {"name": "住まい・衣料", "classL": "2", "classS": "15", "ilc_code": "1015"},
        {"name": "ベビー・衣料", "classL": "2", "classS": "16", "ilc_code": "1016"},
    ]

    # URLを構築
    categories = []
    for params in category_params:
        url = f"https://netsuper.daiei.co.jp/{store_id}/item/item.php?classL={params['classL']}&classS={params['classS']}&ilc_code={params['ilc_code']}"
        categories.append({
            "name": params["name"],
            "url": url
        })

    # 対象カテゴリーをフィルタリング
    if target_categories:
        categories = [
            cat for cat in categories
            if cat["name"] in target_categories
        ]
        logger.info(f"処理対象カテゴリー: {', '.join(target_categories)}")
    else:
        logger.info(f"全{len(categories)}カテゴリーを処理")

    # 各カテゴリーを処理
    results = []
    total_products = 0
    total_new = 0
    total_updated = 0

    for i, category in enumerate(categories, 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"[{i}/{len(categories)}] カテゴリー: {category['name']}")
        logger.info(f"{'='*80}")

        try:
            result = await pipeline.process_category_all_pages(
                category_url=category["url"],
                category_name=category["name"],
                max_pages=max_pages_per_category
            )

            results.append(result)
            total_products += result["total_products"]
            total_new += result["new_products"]
            total_updated += result["updated_products"]

        except Exception as e:
            logger.error(f"カテゴリー '{category['name']}' の処理でエラー: {e}", exc_info=True)
            continue

    # サマリー出力
    end_time = datetime.now()
    duration = end_time - start_time

    logger.info("\n" + "=" * 80)
    logger.info("処理完了サマリー")
    logger.info("=" * 80)
    logger.info(f"処理時間: {duration}")
    logger.info(f"カテゴリー数: {len(results)}/{len(categories)}")
    logger.info(f"総商品数: {total_products}件")
    logger.info(f"  新規: {total_new}件")
    logger.info(f"  更新: {total_updated}件")

    # カテゴリー別の詳細
    logger.info("\nカテゴリー別詳細:")
    for result in results:
        logger.info(
            f"  {result['category_name']}: "
            f"{result['total_products']}件 "
            f"(新規{result['new_products']}件、更新{result['updated_products']}件、"
            f"{result['pages_processed']}ページ)"
        )

    summary = {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration.total_seconds(),
        "categories_processed": len(results),
        "total_categories": len(categories),
        "total_products": total_products,
        "new_products": total_new,
        "updated_products": total_updated,
        "category_results": results
    }

    return summary


async def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description='ダイエーネットスーパー 商品データ取得'
    )
    parser.add_argument(
        '--category',
        action='append',
        help='処理対象のカテゴリー名（複数指定可）'
    )
    parser.add_argument(
        '--max-pages',
        type=int,
        default=100,
        help='カテゴリーあたりの最大ページ数（デフォルト: 100）'
    )
    parser.add_argument(
        '--no-headless',
        action='store_true',
        help='ブラウザを表示する（デバッグ用）'
    )

    args = parser.parse_args()

    # 環境変数チェック
    login_id = os.getenv("DAIEI_LOGIN_ID")
    password = os.getenv("DAIEI_PASSWORD")

    if not login_id or not password:
        logger.error("❌ 環境変数 DAIEI_LOGIN_ID と DAIEI_PASSWORD を設定してください")
        sys.exit(1)

    # パイプライン初期化
    pipeline = DaieiProductIngestionPipeline(
        login_id=login_id,
        password=password,
        headless=not args.no_headless
    )

    try:
        # スクレイパー起動
        success = await pipeline.start()
        if not success:
            logger.error("❌ スクレイパー起動失敗")
            sys.exit(1)

        # カテゴリー処理
        summary = await process_all_categories(
            pipeline=pipeline,
            target_categories=args.category,
            max_pages_per_category=args.max_pages
        )

        # サマリーをJSONファイルに保存
        output_file = f"daiei_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        logger.info(f"\n✅ 処理結果を {output_file} に保存しました")

    except KeyboardInterrupt:
        logger.info("\n⚠️ ユーザーによって中断されました")

    except Exception as e:
        logger.error(f"❌ エラーが発生しました: {e}", exc_info=True)
        sys.exit(1)

    finally:
        # クリーンアップ
        await pipeline.close()
        logger.info("✅ スクレイパー終了")


if __name__ == "__main__":
    asyncio.run(main())
