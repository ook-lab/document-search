"""
東急ストア ネットスーパー 商品データ取得メインスクリプト

使い方:
    # 全カテゴリーを処理（デフォルト）
    python process_tokyu_store.py

    # 特定のカテゴリーのみ処理
    python process_tokyu_store.py --category "野菜" --category "果物"

    # ヘッドレスモードをオフにして動作確認
    python process_tokyu_store.py --no-headless
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

root_dir = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "services" / "netsuper-search"))

# .envファイルを読み込む
from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from tokyu_store.product_ingestion import TokyuStoreProductIngestionPipeline

# ロガー設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tokyu_store_output.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def process_all_categories(
    pipeline: TokyuStoreProductIngestionPipeline,
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
    logger.info("東急ストア ネットスーパー 商品データ取得開始")
    logger.info("=" * 80)

    start_time = datetime.now()

    # カテゴリーを動的に取得
    all_categories = await pipeline.discover_categories()

    if not all_categories:
        logger.error("❌ カテゴリーが見つかりませんでした")
        logger.info("💡 手動でカテゴリーURLを指定してください")

        # 手動カテゴリー定義（実際のサイトから取得したURL）
        manual_categories = [
            {"name": "野菜", "url": f"{pipeline.scraper.base_url}/shop/c/cC10"},
            {"name": "果物", "url": f"{pipeline.scraper.base_url}/shop/c/cC11"},
            {"name": "お魚", "url": f"{pipeline.scraper.base_url}/shop/c/cC20"},
            {"name": "お肉", "url": f"{pipeline.scraper.base_url}/shop/c/cC30"},
            {"name": "惣菜", "url": f"{pipeline.scraper.base_url}/shop/c/cC40"},
            {"name": "牛乳・乳製品・卵", "url": f"{pipeline.scraper.base_url}/shop/c/cC50"},
            {"name": "パン・生菓子・シリアル", "url": f"{pipeline.scraper.base_url}/shop/c/cC51"},
            {"name": "チルド総菜・豆腐・納豆・漬物", "url": f"{pipeline.scraper.base_url}/shop/c/cC52"},
            {"name": "冷凍食品・アイス", "url": f"{pipeline.scraper.base_url}/shop/c/cC53"},
            {"name": "米・餅", "url": f"{pipeline.scraper.base_url}/shop/c/cC54"},
            {"name": "麺類", "url": f"{pipeline.scraper.base_url}/shop/c/cC55"},
            {"name": "乾物・瓶缶詰・粉類", "url": f"{pipeline.scraper.base_url}/shop/c/cC56"},
            {"name": "調味料・中華材料", "url": f"{pipeline.scraper.base_url}/shop/c/cC57"},
            {"name": "お菓子", "url": f"{pipeline.scraper.base_url}/shop/c/cC58"},
            {"name": "水・飲料", "url": f"{pipeline.scraper.base_url}/shop/c/cC59"},
            {"name": "酒類", "url": f"{pipeline.scraper.base_url}/shop/c/cC60"},
        ]
        all_categories = manual_categories
        logger.info(f"📝 手動カテゴリー定義を使用: {len(all_categories)}件")

    # 対象カテゴリーをフィルタリング
    if target_categories:
        categories = [
            cat for cat in all_categories
            if cat["name"] in target_categories
        ]
        logger.info(f"処理対象カテゴリー: {', '.join(target_categories)}")
    else:
        categories = all_categories
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
        description='東急ストア ネットスーパー 商品データ取得'
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
    parser.add_argument(
        '--zip-code',
        type=str,
        default=None,
        help='配達エリアの郵便番号（デフォルト: 環境変数またはコード内の設定）'
    )

    args = parser.parse_args()

    # 環境変数チェック
    login_id = os.getenv("TOKYU_STORE_LOGIN_ID")
    password = os.getenv("TOKYU_STORE_PASSWORD")
    zip_code = args.zip_code or os.getenv("DELIVERY_ZIP_CODE", "158-0094")

    if not login_id or not password:
        logger.error("❌ 環境変数 TOKYU_STORE_LOGIN_ID と TOKYU_STORE_PASSWORD を設定してください")
        logger.error("   .env ファイルに以下を追加してください:")
        logger.error("   TOKYU_STORE_LOGIN_ID=your_email@example.com")
        logger.error("   TOKYU_STORE_PASSWORD=your_password")
        logger.error("   DELIVERY_ZIP_CODE=158-0094")
        sys.exit(1)

    # パイプライン初期化
    pipeline = TokyuStoreProductIngestionPipeline(
        login_id=login_id,
        password=password,
        zip_code=zip_code,
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
        output_file = f"_runtime/data/tokyu_store/tokyu_store_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
