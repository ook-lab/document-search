"""
楽天西友ネットスーパー 商品データ取得パイプライン

商品データを取得してSupabaseに保存します。

処理フロー:
1. Cookieを使用して商品ページにアクセス
2. 商品データを抽出
3. JANコードで既存商品をチェック
4. Supabaseに保存（新規 or 更新）
"""

import os
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

# .envファイルを読み込む
from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from B_ingestion.common.base_product_ingestion import BaseProductIngestionPipeline
from B_ingestion.rakuten_seiyu.rakuten_seiyu_scraper_playwright import RakutenSeiyuScraperPlaywright

# ロガー設定
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class RakutenSeiyuProductIngestionPipeline(BaseProductIngestionPipeline):
    """楽天西友商品データ取得パイプライン（共通基盤クラス継承）"""

    def __init__(self, rakuten_id: str, password: str, headless: bool = True):
        """
        Args:
            rakuten_id: 楽天ID
            password: パスワード
            headless: ヘッドレスモードで実行するか
        """
        super().__init__(organization_name="楽天西友ネットスーパー", headless=headless)
        self.rakuten_id = rakuten_id
        self.password = password

        logger.info("RakutenSeiyuProductIngestionPipeline初期化完了（Service Role使用）")

    async def start(self) -> bool:
        """
        スクレイパーを起動してログイン（楽天西友固有）

        Returns:
            成功したらTrue
        """
        try:
            self.scraper = RakutenSeiyuScraperPlaywright()
            await self.scraper.start(headless=self.headless)

            # ログイン
            success = await self.scraper.login(self.rakuten_id, self.password)
            if not success:
                logger.error("❌ ログイン失敗")
                await self.scraper.close()
                return False

            logger.info("✅ スクレイパー起動・ログイン完了")
            return True

        except Exception as e:
            logger.error(f"スクレイパー起動エラー: {e}", exc_info=True)
            return False

    async def close(self):
        """スクレイパーを終了"""
        if self.scraper:
            await self.scraper.close()

    async def discover_categories(self) -> List[Dict[str, Any]]:
        """
        実際のカテゴリーを取得（毎回動的に取得）

        Returns:
            カテゴリー情報のリスト
        """
        logger.info("🔍 カテゴリーを探索中...")

        page = self.scraper.page

        # トップページにアクセス
        await page.goto(self.scraper.base_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # カテゴリーリンクを探す
        category_selectors = [
            'a[href*="/search/"]',
            '[class*="category"] a',
        ]

        all_categories = []

        for selector in category_selectors:
            try:
                links = await page.query_selector_all(selector)

                for link in links:
                    try:
                        href = await link.get_attribute('href')
                        text = await link.inner_text()

                        if not href or href.startswith('javascript:') or not text:
                            continue

                        # カテゴリーIDを抽出
                        category_id = None
                        if '/search/' in href:
                            parts = href.split('/search/')
                            if len(parts) > 1:
                                category_id = parts[1].split('?')[0].split('/')[0]

                        # 完全なURLを構築
                        full_url = href if href.startswith('http') else f"https://netsuper.rakuten.co.jp{href}"

                        category_info = {
                            'name': text.strip(),
                            'url': full_url,
                            'category_id': category_id
                        }

                        # 重複チェック
                        if not any(cat['url'] == full_url for cat in all_categories):
                            all_categories.append(category_info)

                    except Exception:
                        continue

            except Exception:
                continue

        logger.info(f"✅ {len(all_categories)}件のカテゴリーを発見")
        return all_categories


async def main():
    """テスト実行用のメイン関数"""
    logger.info("楽天西友商品データ取得パイプライン開始")

    rakuten_id = os.getenv("RAKUTEN_ID")
    password = os.getenv("RAKUTEN_PASSWORD")

    if not rakuten_id or not password:
        logger.error("❌ 環境変数 RAKUTEN_ID と RAKUTEN_PASSWORD を設定してください")
        return

    pipeline = RakutenSeiyuProductIngestionPipeline(
        rakuten_id=rakuten_id,
        password=password,
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
