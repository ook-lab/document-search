"""
東急ストアネットスーパー スケジュール管理対応版

カテゴリーごとの実行スケジュールを管理し、
サーバー負荷を最小限に抑える待機時間を実装します。
"""

import os
import sys
import asyncio
import random
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from common.category_manager_db import CategoryManagerDB
from tokyu_store.product_ingestion import TokyuStoreProductIngestionPipeline

# ロガー設定
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class PoliteTokyuStorePipeline:
    """サーバー負荷に配慮したスケジュール管理パイプライン"""

    def __init__(
        self,
        login_id: str,
        password: str,
        zip_code: str = "158-0094",
        headless: bool = True,
        dry_run: bool = False
    ):
        """
        Args:
            login_id: 東急ストアログインID
            password: パスワード
            zip_code: 配達エリア郵便番号
            headless: ヘッドレスモード
            dry_run: Dry Run モード（設定ファイルの初期化のみ）
        """
        self.pipeline = TokyuStoreProductIngestionPipeline(
            login_id=login_id,
            password=password,
            zip_code=zip_code,
            headless=headless
        )
        self.manager = CategoryManagerDB()
        self.dry_run = dry_run
        self.store_name = "tokyu_store"

    async def polite_wait_between_pages(self):
        """ページ遷移間の待機（4秒〜8秒のランダム）"""
        wait_time = random.uniform(4.0, 8.0)
        logger.info(f"⏳ ページ遷移待機: {wait_time:.1f}秒")
        await asyncio.sleep(wait_time)

    async def polite_wait_between_categories(self):
        """カテゴリー切替時の待機（15秒〜30秒のランダム）"""
        wait_time = random.uniform(15.0, 30.0)
        logger.info(f"⏳ カテゴリー切替待機: {wait_time:.1f}秒")
        await asyncio.sleep(wait_time)

    async def initialize_categories(self):
        """カテゴリーを初期化（初回実行時）"""
        logger.info("📋 カテゴリーを初期化します...")

        # スクレイパー起動
        success = await self.pipeline.start()
        if not success:
            logger.error("❌ スクレイパー起動失敗")
            return False

        try:
            # カテゴリーを動的に取得
            categories = await self.pipeline.discover_categories()

            if not categories:
                logger.warning("カテゴリーが見つかりませんでした")
                return False

            # CategoryManagerに登録
            category_list = [
                {"name": cat["name"], "url": cat["url"]}
                for cat in categories
            ]

            # デフォルト設定で初期化
            # 開始日: 明日、インターバル: 7日
            tomorrow = datetime.now().strftime("%Y-%m-%d")
            self.manager.initialize_store_categories(
                self.store_name,
                category_list,
                default_interval_days=7,
                default_start_date=tomorrow
            )

            logger.info(f"✅ {len(categories)}件のカテゴリーを初期化しました")
            logger.info("管理画面で設定を調整してください:")
            logger.info("  streamlit run B_ingestion/netsuper_category_manager_ui.py")

            return True

        finally:
            await self.pipeline.close()

    async def run_scheduled_categories(self, manual_categories: List[str] = None):
        """スケジュールに基づいてカテゴリーを処理

        Args:
            manual_categories: 手動実行時に指定されたカテゴリー名のリスト（Noneの場合はスケジュールに従う）
        """
        if manual_categories:
            logger.info("="*80)
            logger.info("東急ストアネットスーパー 手動実行開始")
            logger.info(f"対象カテゴリー: {', '.join(manual_categories)}")
            logger.info("="*80)
        else:
            logger.info("="*80)
            logger.info("東急ストアネットスーパー スケジュール実行開始")
            logger.info("="*80)

        # スクレイパー起動
        success = await self.pipeline.start()
        if not success:
            logger.error("❌ スクレイパー起動失敗")
            return

        try:
            # カテゴリーを動的に取得して更新
            logger.info("🔄 カテゴリーを最新化中...")
            discovered_categories = await self.pipeline.discover_categories()

            if discovered_categories:
                logger.info(f"✅ {len(discovered_categories)}件のカテゴリーを取得")

                # 既存の設定を取得
                existing_categories = self.manager.get_all_categories(self.store_name)
                existing_names = {cat["name"]: cat for cat in existing_categories} if existing_categories else {}

                # 新規カテゴリーを追加
                for cat in discovered_categories:
                    if cat["name"] not in existing_names:
                        logger.info(f"  📝 新規カテゴリー追加: {cat['name']}")
                        self.manager.update_category(
                            self.store_name,
                            cat["name"],
                            {
                                "url": cat["url"],
                                "enabled": True,
                                "interval_days": 7,
                                "start_date": datetime.now().strftime("%Y-%m-%d")
                            }
                        )
                    else:
                        # URLが変更されている場合は更新
                        if existing_names[cat["name"]].get("url") != cat["url"]:
                            logger.info(f"  🔄 URL更新: {cat['name']}")
                            self.manager.update_category(
                                self.store_name,
                                cat["name"],
                                {"url": cat["url"]}
                            )

            # 設定からカテゴリーを取得
            categories = self.manager.get_all_categories(self.store_name)

            if not categories:
                logger.warning("カテゴリーが設定されていません。初回実行してください:")
                logger.warning("  python -m B_ingestion.tokyu_store.process_with_schedule --init")
                return

            # 実行すべきカテゴリーをフィルタリング
            today = datetime.now()
            runnable_categories = []

            if manual_categories:
                # 手動実行時: 指定されたカテゴリーのみ
                for cat in categories:
                    if cat["name"] in manual_categories:
                        runnable_categories.append(cat)
            else:
                # スケジュール実行時: 今日実行すべきカテゴリー
                for cat in categories:
                    if self.manager.should_run_category(self.store_name, cat["name"], today):
                        runnable_categories.append(cat)

            logger.info(f"📊 総カテゴリー数: {len(categories)}件")
            logger.info(f"✅ 本日実行対象: {len(runnable_categories)}件")

            if not runnable_categories:
                logger.info("本日実行するカテゴリーはありません")
                return

            # カテゴリーごとに処理
            for idx, cat in enumerate(runnable_categories, 1):
                logger.info("")
                logger.info("="*80)
                logger.info(f"📦 カテゴリー {idx}/{len(runnable_categories)}: {cat['name']}")
                logger.info(f"   URL: {cat['url']}")
                logger.info("="*80)

                try:
                    # カテゴリーの商品データを取得してSupabaseに保存
                    result = await self.pipeline.process_category_all_pages(
                        category_url=cat['url'],
                        category_name=cat['name']
                    )

                    if result:
                        logger.info(f"✅ カテゴリー {cat['name']} の処理完了")
                        logger.info(f"   商品数: {result.get('total_products', 0)}件")
                        logger.info(f"   新規: {result.get('new_products', 0)}件, 更新: {result.get('updated_products', 0)}件")
                    else:
                        logger.warning(f"⚠️ カテゴリー {cat['name']} の処理に問題がありました")

                except Exception as e:
                    logger.error(f"❌ カテゴリー {cat['name']} 処理エラー: {e}", exc_info=True)

                finally:
                    # 成功・失敗に関わらず実行済みとしてマーク
                    self.manager.mark_as_run(self.store_name, cat["name"], today)

                # カテゴリー間の待機
                if idx < len(runnable_categories):
                    await self.polite_wait_between_categories()

            logger.info("")
            logger.info("="*80)
            logger.info("✅ すべてのカテゴリー処理完了")
            logger.info("="*80)

        finally:
            await self.pipeline.close()


async def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(description="東急ストアスクレイピング（スケジュール管理対応）")
    parser.add_argument("--init", action="store_true", help="カテゴリーを初期化（初回実行時のみ）")
    parser.add_argument("--manual", action="store_true", help="手動実行モード（環境変数MANUAL_CATEGORIESからカテゴリーを取得）")
    parser.add_argument("--headless", action="store_true", default=True, help="ヘッドレスモード")
    parser.add_argument("--zip-code", default="158-0094", help="配達エリア郵便番号")
    args = parser.parse_args()

    login_id = os.getenv("TOKYU_STORE_LOGIN_ID")
    password = os.getenv("TOKYU_STORE_PASSWORD")

    if not login_id or not password:
        logger.error("❌ 環境変数 TOKYU_STORE_LOGIN_ID と TOKYU_STORE_PASSWORD を設定してください")
        return

    pipeline = PoliteTokyuStorePipeline(
        login_id=login_id,
        password=password,
        zip_code=args.zip_code,
        headless=args.headless
    )

    if args.init:
        # 初期化モード
        await pipeline.initialize_categories()
    elif args.manual:
        # 手動実行モード
        manual_categories_str = os.getenv("MANUAL_CATEGORIES", "")
        if not manual_categories_str:
            logger.error("❌ 環境変数 MANUAL_CATEGORIES が設定されていません")
            return

        manual_categories = [cat.strip() for cat in manual_categories_str.split(",") if cat.strip()]
        if not manual_categories:
            logger.error("❌ カテゴリーが指定されていません")
            return

        await pipeline.run_scheduled_categories(manual_categories=manual_categories)

        # 商品データ取得後、自動的にembedding生成を実行
        await generate_embeddings_if_needed()
    else:
        # 通常実行モード（スケジュール）
        await pipeline.run_scheduled_categories()

        # 商品データ取得後、自動的にembedding生成を実行
        await generate_embeddings_if_needed()


async def generate_embeddings_if_needed():
    """商品データの分類・embedding生成（未生成のものがあれば実行）"""
    try:
        logger.info("")
        logger.info("="*80)
        logger.info("🔄 商品分類・Embedding生成チェック開始")
        logger.info("="*80)

        # ステップ1: Gemini 2.5 Flash で general_name, small_category, keywords を生成
        logger.info("ステップ1: Gemini 2.5 Flash で商品分類生成")
        classification_path = Path(__file__).parent.parent.parent / "L_product_classification"
        sys.path.insert(0, str(classification_path))

        from daily_auto_classifier import DailyAutoClassifier

        classifier = DailyAutoClassifier()
        result = await classifier.process_unclassified_products()
        logger.info(f"✅ 分類完了: {result.get('classified_count', 0)}件")

        # ステップ2: Embedding生成
        logger.info("ステップ2: Embedding生成")
        netsuper_app_path = Path(__file__).parent.parent.parent / "netsuper_search_app"
        sys.path.insert(0, str(netsuper_app_path))

        from generate_multi_embeddings import MultiEmbeddingGenerator

        # embedding生成を実行
        generator = MultiEmbeddingGenerator()
        generator.process_products(delay=0.1)

        logger.info("✅ 商品分類・Embedding生成処理完了")

    except Exception as e:
        logger.error(f"⚠️ 商品分類・Embedding生成エラー（スキップして続行）: {e}")


if __name__ == "__main__":
    asyncio.run(main())
