"""
楽天西友ネットスーパー 商品データ定期取得スクリプト

使用方法:
    # 初回: ログインしてCookie取得
    python process_rakuten_seiyu.py --auth

    # 商品データ取得
    python process_rakuten_seiyu.py --once              # 1回だけ実行
    python process_rakuten_seiyu.py --continuous        # 継続実行（24時間ごと）
    python process_rakuten_seiyu.py --categories 110001,110003  # 特定カテゴリーのみ

    # ヘッドレスモードなしでログイン（デバッグ用）
    python process_rakuten_seiyu.py --auth --no-headless
"""

import asyncio
import argparse
import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from B_ingestion.rakuten_seiyu.auth_manager import RakutenSeiyuAuthManager
from B_ingestion.rakuten_seiyu.product_ingestion import RakutenSeiyuProductIngestionPipeline

# 環境変数を読み込み
load_dotenv()

# ロガー設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def authenticate(headless: bool = True) -> bool:
    """
    ログインしてCookieを保存

    Args:
        headless: ヘッドレスモードで実行するか

    Returns:
        成功したらTrue
    """
    logger.info("=" * 60)
    logger.info("🔐 楽天西友にログイン中...")
    logger.info("=" * 60)

    rakuten_id = os.getenv("RAKUTEN_ID")
    password = os.getenv("RAKUTEN_PASSWORD")
    zip_code = os.getenv("DELIVERY_ZIP_CODE", "211-0063")

    if not rakuten_id or not password:
        logger.error("❌ 環境変数 RAKUTEN_ID と RAKUTEN_PASSWORD を設定してください")
        logger.error("   .env ファイルを確認してください")
        return False

    try:
        async with RakutenSeiyuAuthManager(headless=headless) as auth:
            # ログイン
            success = await auth.login(
                rakuten_id=rakuten_id,
                password=password
            )

            if not success:
                logger.error("❌ ログイン失敗")
                return False

            # Cookie保存
            await auth.save_cookies("B_ingestion/rakuten_seiyu/rakuten_seiyu_cookies.json")

        logger.info("=" * 60)
        logger.info("✅ 認証完了！Cookie保存しました")
        logger.info("=" * 60)
        return True

    except Exception as e:
        logger.error(f"❌ 認証処理エラー: {e}", exc_info=True)
        return False


async def run_ingestion(
    categories: Optional[str] = None,
    category_config_file: str = "B_ingestion/rakuten_seiyu/categories_config.json",
    headless: bool = True
) -> bool:
    """
    商品データ取得を実行

    Args:
        categories: カンマ区切りのカテゴリーID（指定時は指定カテゴリーのみ）
        category_config_file: カテゴリー設定ファイルのパス
        headless: ヘッドレスモードで実行するか

    Returns:
        成功したらTrue
    """
    logger.info("=" * 60)
    logger.info("🛒 楽天西友商品データ取得開始")
    logger.info("=" * 60)

    # 認証情報を取得
    rakuten_id = os.getenv("RAKUTEN_ID")
    password = os.getenv("RAKUTEN_PASSWORD")

    if not rakuten_id or not password:
        logger.error("❌ 環境変数 RAKUTEN_ID と RAKUTEN_PASSWORD を設定してください")
        return False

    # パイプライン初期化
    pipeline = RakutenSeiyuProductIngestionPipeline(
        rakuten_id=rakuten_id,
        password=password,
        headless=headless
    )

    # ログインしてスクレイパー起動
    login_success = await pipeline.start()
    if not login_success:
        logger.error("❌ スクレイパーの起動またはログインに失敗しました")
        return False

    # カテゴリーを動的に取得（毎回ログイン後に取得）
    all_categories = await pipeline.discover_categories()

    if not all_categories:
        logger.error("❌ カテゴリーが見つかりませんでした")
        await pipeline.close()
        return False

    # カテゴリーIDでフィルタリング（指定がある場合）
    target_categories = []

    if categories:
        # コマンドライン引数でカテゴリーID指定
        category_ids = [c.strip() for c in categories.split(",")]
        target_categories = [
            cat for cat in all_categories
            if cat.get("category_id") in category_ids
        ]

        if not target_categories:
            logger.warning(f"指定されたカテゴリーID {category_ids} が見つかりません")
            logger.info("利用可能なカテゴリー:")
            for cat in all_categories[:10]:  # 最初の10件を表示
                logger.info(f"  ID: {cat.get('category_id')}, 名前: {cat.get('name')}")
            await pipeline.close()
            return False

        logger.info(f"指定カテゴリー: {len(target_categories)}件")
    else:
        # 全カテゴリーを対象（ただし/search/で始まるもののみ）
        target_categories = [
            cat for cat in all_categories
            if cat.get("category_id") and cat.get("category_id").isdigit()
        ]
        logger.info(f"全カテゴリーを処理: {len(target_categories)}件")

    # 各カテゴリーを処理
    total_stats = {
        "total_products": 0,
        "new_products": 0,
        "updated_products": 0,
        "categories_processed": 0
    }

    for category in target_categories:
        try:
            category_url = category["url"]
            category_name = category["name"]
            category_id = category.get("category_id", "不明")

            logger.info("-" * 60)
            logger.info(f"📦 カテゴリー処理中: {category_name} (ID: {category_id})")
            logger.info("-" * 60)

            result = await pipeline.process_category_all_pages(
                category_url=category_url,
                category_name=category_name
            )

            if result["success"]:
                total_stats["total_products"] += result["total_products"]
                total_stats["new_products"] += result["new_products"]
                total_stats["updated_products"] += result["updated_products"]
                total_stats["categories_processed"] += 1

            # カテゴリー間の待機（礼儀正しくアクセス）
            if len(target_categories) > 1:
                import time
                import random
                wait_time = random.uniform(3.0, 5.0)
                logger.info(f"⏳ 次のカテゴリーまで {wait_time:.1f}秒待機...")
                time.sleep(wait_time)

        except Exception as e:
            logger.error(f"❌ カテゴリー処理エラー ({category['name']}): {e}", exc_info=True)
            continue

    # スクレイパーを終了
    await pipeline.close()

    # 最終結果
    logger.info("=" * 60)
    logger.info("✅ 処理完了")
    logger.info(f"   処理カテゴリー数: {total_stats['categories_processed']}")
    logger.info(f"   合計商品数: {total_stats['total_products']}件")
    logger.info(f"   新規: {total_stats['new_products']}件")
    logger.info(f"   更新: {total_stats['updated_products']}件")
    logger.info("=" * 60)

    return True


async def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description='楽天西友ネットスーパー 商品データ取得ツール',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 初回認証
  python process_rakuten_seiyu.py --auth

  # 商品データ取得（1回）
  python process_rakuten_seiyu.py --once

  # 特定カテゴリーのみ取得
  python process_rakuten_seiyu.py --once --categories 110001,110003

  # 継続実行（24時間ごと）
  python process_rakuten_seiyu.py --continuous

  # デバッグモード（ブラウザ表示）
  python process_rakuten_seiyu.py --auth --no-headless
        """
    )

    parser.add_argument(
        '--auth',
        action='store_true',
        help='ログインしてCookie取得'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='1回だけ実行'
    )
    parser.add_argument(
        '--continuous',
        action='store_true',
        help='継続実行（24時間ごと）'
    )
    parser.add_argument(
        '--categories',
        type=str,
        help='カンマ区切りのカテゴリーID（例: 110001,110003）'
    )
    parser.add_argument(
        '--no-headless',
        action='store_true',
        help='ヘッドレスモードなしで実行（デバッグ用）'
    )

    args = parser.parse_args()

    # 引数チェック
    if not any([args.auth, args.once, args.continuous]):
        parser.print_help()
        return

    # 認証処理
    if args.auth:
        headless = not args.no_headless
        success = await authenticate(headless=headless)
        if not success:
            sys.exit(1)
        return

    # 商品データ取得
    if args.once:
        headless = not args.no_headless
        success = await run_ingestion(categories=args.categories, headless=headless)
        if not success:
            sys.exit(1)

    elif args.continuous:
        logger.info("🔄 継続実行モード開始（24時間ごとに実行）")
        logger.info("   Ctrl+C で終了します")
        headless = not args.no_headless

        while True:
            try:
                await run_ingestion(categories=args.categories, headless=headless)

                # 24時間待機
                logger.info("⏳ 次回実行まで24時間待機します...")
                await asyncio.sleep(86400)

            except KeyboardInterrupt:
                logger.info("⚠️  ユーザーによる中断")
                break
            except Exception as e:
                logger.error(f"❌ エラー発生: {e}", exc_info=True)
                logger.info("⏳ 1時間後にリトライします...")
                await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⚠️  プログラムを終了します")
        sys.exit(0)
