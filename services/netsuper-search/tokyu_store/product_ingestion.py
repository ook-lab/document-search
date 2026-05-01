"""
東急ストア ネットスーパー 商品データ取得パイプライン

商品データを取得してSupabaseに保存します。

処理フロー:
1. ログインして配達エリアを選択
2. カテゴリーページの商品データを取得
3. JANコードで既存商品をチェック
4. Supabaseに保存（新規 or 更新）
"""

import os
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any

# プロジェクトルートをパスに追加
_repo = Path(__file__).resolve().parents[3]
_netsuper = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo))
sys.path.insert(0, str(_netsuper))

from dotenv import load_dotenv

load_dotenv(_repo / ".env")

from common.base_product_ingestion import BaseProductIngestionPipeline
from tokyu_store.tokyu_store_scraper_playwright import TokyuStoreScraperPlaywright

# ロガー設定
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class TokyuStoreProductIngestionPipeline(BaseProductIngestionPipeline):
    """東急ストア商品データ取得パイプライン（共通基盤クラス継承）"""

    def __init__(self, login_id: str, password: str, zip_code: str = "158-0094", headless: bool = True):
        """
        Args:
            login_id: 東急ストアログインID（メールアドレス）
            password: パスワード
            zip_code: 配達エリア郵便番号
            headless: ヘッドレスモードで実行するか
        """
        super().__init__(organization_name="東急ストア ネットスーパー", headless=headless)
        self.login_id = login_id
        self.password = password
        self.zip_code = zip_code

        logger.info("TokyuStoreProductIngestionPipeline初期化完了（Service Role使用）")

    async def start(self) -> bool:
        """
        スクレイパーを起動してログイン（東急ストア固有）

        Returns:
            成功したらTrue
        """
        try:
            self.scraper = TokyuStoreScraperPlaywright()
            await self.scraper.start(headless=self.headless)

            # ログイン
            success = await self.scraper.login(self.login_id, self.password)
            if not success:
                logger.error("❌ ログイン失敗")
                await self.scraper.close()
                return False

            # 配達エリア選択
            success = await self.scraper.select_delivery_area(self.zip_code)
            if not success:
                logger.warning("⚠️ 配達エリア選択に失敗しましたが続行します")

            logger.info("✅ スクレイパー起動・ログイン・配達エリア選択完了")
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
        トップページからカテゴリーを動的に取得

        Returns:
            カテゴリー情報のリスト [{"name": "カテゴリー名", "url": "URL"}]
        """
        try:
            logger.info("📂 カテゴリーを取得中...")

            # トップページまたはカテゴリー一覧ページにアクセス
            await self.scraper.page.goto(
                f"{self.scraper.base_url}/shop/default.aspx",
                wait_until="domcontentloaded",
                timeout=60000
            )
            await self.scraper.page.wait_for_timeout(2000)

            # カテゴリーモーダルを開く
            try:
                category_modal_button = await self.scraper.page.query_selector('.category-modal-open, a:has-text("カテゴリ")')
                if category_modal_button:
                    await category_modal_button.click()
                    await self.scraper.page.wait_for_timeout(1000)
                    logger.info("カテゴリーモーダルを開きました")
            except Exception as e:
                logger.warning(f"カテゴリーモーダルを開けませんでした: {e}")

            # カテゴリーリンクを取得（カテゴリーモーダル内の主要カテゴリー）
            category_links = await self.scraper.page.query_selector_all(
                'h3.category-name a, .category-name a'
            )

            categories = []
            for link in category_links:
                try:
                    name = await link.inner_text()
                    href = await link.get_attribute('href')

                    if href and name:
                        # 相対URLを絶対URLに変換
                        if not href.startswith('http'):
                            href = f"{self.scraper.base_url}{href}" if href.startswith('/') else f"{self.scraper.base_url}/{href}"

                        categories.append({
                            "name": name.strip(),
                            "url": href
                        })
                except Exception as e:
                    logger.warning(f"カテゴリーリンク処理エラー: {e}")
                    continue

            logger.info(f"✅ {len(categories)}件のカテゴリーを発見")
            return categories

        except Exception as e:
            logger.error(f"カテゴリー取得エラー: {e}", exc_info=True)
            return []


async def main():
    """テスト実行用のメイン関数"""
    logger.info("東急ストア商品データ取得パイプライン開始")

    login_id = os.getenv("TOKYU_STORE_LOGIN_ID")
    password = os.getenv("TOKYU_STORE_PASSWORD")
    zip_code = os.getenv("DELIVERY_ZIP_CODE", "158-0094")

    if not login_id or not password:
        logger.error("❌ 環境変数 TOKYU_STORE_LOGIN_ID と TOKYU_STORE_PASSWORD を設定してください")
        return

    pipeline = TokyuStoreProductIngestionPipeline(
        login_id=login_id,
        password=password,
        zip_code=zip_code,
        headless=False
    )

    try:
        # スクレイパー起動
        success = await pipeline.start()
        if not success:
            logger.error("❌ スクレイパー起動失敗")
            return

        # カテゴリーを動的に取得
        categories = await pipeline.discover_categories()
        if categories:
            logger.info(f"発見したカテゴリー: {len(categories)}件")
            for cat in categories[:5]:  # 最初の5件を表示
                logger.info(f"  - {cat['name']}: {cat['url']}")

        logger.info("✅ テスト完了")

    finally:
        await pipeline.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
