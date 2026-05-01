"""
ダイエーネットスーパー 商品データ取得パイプライン

商品データを取得してSupabaseに保存します。

処理フロー:
1. ログインして配達日時を選択
2. カテゴリーページの商品データを取得
3. JANコードで既存商品をチェック
4. Supabaseに保存（新規 or 更新）
"""

import os
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any

# リポジトリルート + netsuper-search（common / 店舗パッケージ）
_repo = Path(__file__).resolve().parents[3]
_netsuper = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo))
sys.path.insert(0, str(_netsuper))

from dotenv import load_dotenv

load_dotenv(_repo / ".env")

from common.base_product_ingestion import BaseProductIngestionPipeline
from daiei.daiei_scraper_playwright import DaieiScraperPlaywright

# ロガー設定
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class DaieiProductIngestionPipeline(BaseProductIngestionPipeline):
    """ダイエー商品データ取得パイプライン（共通基盤クラス継承）"""

    def __init__(self, login_id: str, password: str, headless: bool = True):
        """
        Args:
            login_id: ダイエーログインID
            password: パスワード
            headless: ヘッドレスモードで実行するか
        """
        super().__init__(organization_name="ダイエーネットスーパー", headless=headless)
        self.login_id = login_id
        self.password = password

        logger.info("DaieiProductIngestionPipeline初期化完了（Service Role使用）")

    async def start(self) -> bool:
        """
        スクレイパーを起動してログイン（ダイエー固有）

        Returns:
            成功したらTrue
        """
        try:
            self.scraper = DaieiScraperPlaywright()
            await self.scraper.start(headless=self.headless)

            # ログイン
            success = await self.scraper.login(self.login_id, self.password)
            if not success:
                logger.error("❌ ログイン失敗")
                await self.scraper.close()
                return False

            # 配達日時選択
            success = await self.scraper.select_delivery_slot()
            if not success:
                logger.error("❌ 配達日時選択失敗")
                await self.scraper.close()
                return False

            logger.info("✅ スクレイパー起動・ログイン・配達日時選択完了")
            return True

        except Exception as e:
            logger.error(f"スクレイパー起動エラー: {e}", exc_info=True)
            return False

    async def close(self):
        """スクレイパーを終了"""
        if self.scraper:
            await self.scraper.close()

    async def discover_categories(self) -> List[Dict[str, str]]:
        """
        カテゴリーを取得（ダイエーは静的リスト）

        Returns:
            カテゴリー情報のリスト [{"name": "カテゴリー名", "url": "URL"}]
        """
        logger.info("🔍 カテゴリーを取得中（ダイエーは静的リスト）...")

        # ダイエーのカテゴリーは動的取得が難しいため、静的リストを使用
        # 今後、スクレイピングで取得できるようになったら実装を変更
        categories = [
            {"name": "野菜・果物", "url": "https://daiei.eorder.ne.jp/category/vegetables"},
            {"name": "精肉", "url": "https://daiei.eorder.ne.jp/category/meat"},
            {"name": "鮮魚", "url": "https://daiei.eorder.ne.jp/category/fish"},
            {"name": "惣菜", "url": "https://daiei.eorder.ne.jp/category/deli"},
            {"name": "冷凍食品", "url": "https://daiei.eorder.ne.jp/category/frozen"},
            {"name": "乳製品・卵", "url": "https://daiei.eorder.ne.jp/category/dairy"},
            {"name": "パン・シリアル", "url": "https://daiei.eorder.ne.jp/category/bread"},
            {"name": "麺類", "url": "https://daiei.eorder.ne.jp/category/noodles"},
            {"name": "缶詰・瓶詰", "url": "https://daiei.eorder.ne.jp/category/canned"},
            {"name": "調味料", "url": "https://daiei.eorder.ne.jp/category/seasoning"},
            {"name": "飲料", "url": "https://daiei.eorder.ne.jp/category/drinks"},
            {"name": "菓子", "url": "https://daiei.eorder.ne.jp/category/snacks"},
            {"name": "日用品", "url": "https://daiei.eorder.ne.jp/category/household"},
        ]

        logger.info(f"✅ {len(categories)}件のカテゴリーを取得")
        return categories


async def main():
    """テスト実行用のメイン関数"""
    logger.info("ダイエー商品データ取得パイプライン開始")

    login_id = os.getenv("DAIEI_LOGIN_ID")
    password = os.getenv("DAIEI_PASSWORD")

    if not login_id or not password:
        logger.error("❌ 環境変数 DAIEI_LOGIN_ID と DAIEI_PASSWORD を設定してください")
        return

    pipeline = DaieiProductIngestionPipeline(
        login_id=login_id,
        password=password,
        headless=False
    )

    try:
        # スクレイパー起動
        success = await pipeline.start()
        if not success:
            logger.error("❌ スクレイパー起動失敗")
            return

        logger.info("✅ テスト完了")

    finally:
        await pipeline.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
