"""
東急ストア ネットスーパー スクレイピングモジュール (Playwright版)

Playwrightを使用してログイン状態を保持したまま商品データを取得します。
"""

import json
import re
import time
import random
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# ロガー設定
logger = logging.getLogger(__name__)


class TokyuStoreScraperPlaywright:
    """東急ストア ネットスーパーのスクレイピングクラス (Playwright版)"""

    def __init__(self):
        self.base_url = "https://ns.tokyu-bell.jp"
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def __aenter__(self):
        """async with構文でのコンテキスト開始"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """async with構文でのコンテキスト終了"""
        await self.close()

    async def start(self, headless: bool = True):
        """
        ブラウザを起動

        Args:
            headless: ヘッドレスモードで起動するか
        """
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=headless)
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self.page = await self.context.new_page()
            logger.info("✅ Playwrightブラウザ起動完了")

        except Exception as e:
            logger.error(f"ブラウザ起動エラー: {e}", exc_info=True)
            raise

    async def close(self):
        """ブラウザを閉じる"""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("✅ ブラウザ終了")

        except Exception as e:
            logger.error(f"ブラウザ終了エラー: {e}", exc_info=True)

    async def _is_logged_in(self) -> bool:
        """ログイン状態をチェック"""
        try:
            # ログインフォームが表示されているかチェック
            login_form = await self.page.query_selector('input[name="LoginID"]')
            if login_form:
                # ログインフォームが見つかった = ログインしていない
                return False

            # ログアウトリンクまたはマイページリンクがあればログイン済み
            logout_link = await self.page.query_selector('a[href*="logout"]')
            mypage_link = await self.page.query_selector('a[href*="mypage"]')

            return bool(logout_link or mypage_link)
        except:
            return False

    async def login(self, login_id: str, password: str) -> bool:
        """
        東急ストア ネットスーパーにログイン

        Args:
            login_id: ログインID（メールアドレス）
            password: パスワード

        Returns:
            成功したらTrue
        """
        import asyncio

        try:
            logger.info("🔐 東急ストア ネットスーパーにログイン中...")

            # 直接ログイン/会員メニューページにアクセス
            logger.info("ステップ1: ログインページに遷移中...")
            await self.page.goto(f"{self.base_url}/shop/customer/menu.aspx", wait_until="domcontentloaded", timeout=30000)
            await self.page.wait_for_timeout(2000)

            # 現在のページを確認
            current_url = self.page.url
            logger.info(f"📍 現在のURL: {current_url}")

            # デバッグ: ログインページのHTML/スクリーンショット保存
            logger.info("📸 ログインページのHTML/スクリーンショット保存中...")
            await self.page.screenshot(path="tokyu_store_login_page.png")
            login_page_html = await self.page.content()
            with open("tokyu_store_login_page.html", "w", encoding="utf-8") as f:
                f.write(login_page_html)
            logger.info("✅ 保存完了: tokyu_store_login_page.png, tokyu_store_login_page.html")

            # メールアドレスを入力
            logger.info("ステップ2: メールアドレスを入力中...")
            login_id_input = await self.page.wait_for_selector(
                'input[name="uid"], input[id="login_uid"]',
                timeout=10000,
                state="visible"
            )
            await login_id_input.click()
            await login_id_input.fill(login_id)
            logger.info("✅ メールアドレス入力完了")

            # パスワードを入力
            logger.info("ステップ3: パスワードを入力中...")
            password_input = await self.page.wait_for_selector(
                'input[name="pwd"], input[id="login_pwd"]',
                timeout=5000,
                state="visible"
            )
            await password_input.click()
            await password_input.fill(password)
            logger.info("✅ パスワード入力完了")

            # ログインボタンをクリック
            await asyncio.sleep(0.5)
            login_button = await self.page.wait_for_selector(
                'input[type="submit"][name="order"]',
                timeout=5000,
                state="visible"
            )
            await login_button.click()
            logger.info("ログインボタンをクリック")

            # ログイン完了を待機
            await asyncio.sleep(3)
            await self.page.wait_for_load_state("domcontentloaded")

            current_url = self.page.url
            logger.info(f"ログイン後のURL: {current_url}")

            # ログイン成功確認
            if await self._is_logged_in():
                logger.info("✅ ログイン成功")
                return True
            else:
                logger.error("❌ ログイン失敗")
                return False

        except Exception as e:
            logger.error(f"ログインエラー: {e}", exc_info=True)
            return False

    async def select_delivery_area(self, zip_code: str = "158-0094") -> bool:
        """
        配達エリア（郵便番号）を選択

        Args:
            zip_code: 郵便番号（デフォルト: 158-0094 世田谷区）

        Returns:
            成功したらTrue
        """
        import asyncio

        try:
            logger.info("📍 配達エリアを選択中...")

            # 郵便番号入力フォームを探す
            zip_input = await self.page.query_selector('input[name*="zip"], input[id*="txtZip"]')

            if zip_input:
                await zip_input.click()
                await zip_input.fill(zip_code.replace("-", ""))
                logger.info(f"✅ 郵便番号入力: {zip_code}")

                # 検索ボタンをクリック
                search_button = await self.page.query_selector('input[type="submit"], button[type="submit"]')
                if search_button:
                    await search_button.click()
                    await asyncio.sleep(2)
                    await self.page.wait_for_load_state("domcontentloaded")
                    logger.info("✅ 配達エリア選択完了")
                    return True
            else:
                # 郵便番号選択が不要な場合
                logger.info("✅ 配達エリア選択は不要です")
                return True

        except Exception as e:
            logger.error(f"配達エリア選択エラー: {e}", exc_info=True)
            return False

    async def fetch_products_page(
        self,
        category_url: str,
        page: int = 1
    ) -> tuple[List[Dict[str, Any]], Optional[dict]]:
        """
        カテゴリーページの商品データを取得

        Args:
            category_url: カテゴリーの完全URL
            page: ページ番号（1始まり）

        Returns:
            (商品データのリスト, ページネーション情報)
        """
        try:
            # URLにページ番号を追加（東急ストアは _p2/ 形式）
            if page == 1:
                url = category_url
            else:
                # 末尾の / を削除してから _p{page}/ を追加
                base_url = category_url.rstrip('/')
                url = f"{base_url}_p{page}/"

            logger.info(f"商品ページ取得中 (page={page}): {url}")

            await self.page.goto(url, wait_until="networkidle", timeout=60000)
            await self.page.wait_for_timeout(3000)

            # 商品データを抽出（HTMLベース）
            products, pagination_info = await self._extract_products_from_html()

            # アクセス間隔制御
            await self.page.wait_for_timeout(random.randint(1000, 2000))

            return products, pagination_info

        except Exception as e:
            logger.error(f"商品ページ取得エラー: {e}", exc_info=True)
            return [], None

    async def _extract_products_from_html(self) -> tuple[List[Dict[str, Any]], Optional[dict]]:
        """
        HTMLから商品データを抽出

        Returns:
            (商品データのリスト, ページネーション情報)
        """
        try:
            logger.info("✅ HTML解析開始")

            # デバッグ用にスクリーンショット・HTML保存
            await self.page.screenshot(path="tokyu_store_product_page.png")
            logger.info("スクリーンショット保存: tokyu_store_product_page.png")

            html_content = await self.page.content()
            with open("tokyu_store_product_page.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("HTML保存: tokyu_store_product_page.html")

            # 商品コンテナを取得（東急ストアの実際のHTML構造）
            product_containers = await self.page.query_selector_all(
                'div.block-pickup-list-p--item-body, li:has(div.block-pickup-list-p--item-body)'
            )
            logger.info(f"商品コンテナ数: {len(product_containers)}")

            products = []

            for container in product_containers:
                try:
                    # 商品名を取得
                    name_elem = await container.query_selector('.block-pickup-list-p--goods-name a')
                    product_name = await name_elem.inner_text() if name_elem else None

                    # 商品画像URLを取得
                    img_elem = await container.query_selector('.block-pickup-list-p--image img')
                    if img_elem:
                        img_src = await img_elem.get_attribute('data-src')  # lazyload用
                        if not img_src:
                            img_src = await img_elem.get_attribute('src')
                        if img_src and not img_src.startswith('http'):
                            img_src = f"{self.base_url}{img_src}"
                    else:
                        img_src = None

                    # 価格を取得（税抜価格）
                    price_elem = await container.query_selector('.block-pickup-list-p--net-price')
                    price_text = await price_elem.inner_text() if price_elem else None
                    price = None
                    if price_text:
                        # 価格から数字を抽出（￥を除去）
                        price_cleaned = price_text.replace('￥', '').replace(',', '').strip()
                        match = re.search(r'(\d+\.?\d*)', price_cleaned)
                        if match:
                            price = float(match.group(1))

                    # 税込価格を取得
                    price_tax_elem = await container.query_selector('.block-pickup-list-p--price.reference-price')
                    price_tax_text = await price_tax_elem.inner_text() if price_tax_elem else None
                    price_tax_included = None
                    if price_tax_text:
                        price_tax_cleaned = price_tax_text.replace('参考税込', '').replace('￥', '').replace(',', '').strip()
                        match = re.search(r'(\d+\.?\d*)', price_tax_cleaned)
                        if match:
                            price_tax_included = float(match.group(1))

                    # 商品IDとURLを取得（リンクから）
                    product_id = None
                    product_url = None
                    link = await container.query_selector('.block-pickup-list-p--goods-name a')
                    if link:
                        href = await link.get_attribute('href')
                        if href:
                            # /shop/g/g01087086/ のような形式からIDを抽出
                            id_match = re.search(r'/g/g(\d+)', href)
                            if id_match:
                                product_id = id_match.group(1)
                            # 完全なURLを構築
                            if href.startswith('http'):
                                product_url = href
                            elif href.startswith('/'):
                                product_url = f"https://www.tokyu-store.co.jp{href}"
                            else:
                                product_url = f"https://www.tokyu-store.co.jp/{href}"

                    if product_name:  # 商品名がある場合のみ追加
                        product = {
                            "product_id": product_id,
                            "product_name": product_name.strip() if product_name else None,
                            "price": price,
                            "price_tax_included": price_tax_included if price_tax_included else price,
                            "image_url": img_src,
                            "url": product_url,  # URLを追加
                            "in_stock": True,  # ページに表示されている = 在庫あり
                            "is_available": True,
                            "raw_data": {
                                "product_id": product_id,
                                "price_text": price_text,
                                "price_tax_text": price_tax_text,
                                "url": product_url  # raw_dataにも追加
                            }
                        }

                        products.append(product)

                except Exception as e:
                    logger.warning(f"商品データ抽出エラー（スキップ）: {e}")
                    continue

            # ページネーション情報を取得
            pagination_info = None
            try:
                # ページング情報を探す（一般的なパターン）
                pager = await self.page.query_selector('div.pager, div.pagination, ul.pagination')
                if pager:
                    page_links = await pager.query_selector_all('a, span')
                    total_pages = len([p for p in page_links if (await p.inner_text()).strip().isdigit()])

                    pagination_info = {
                        "totalItems": len(products) * total_pages,  # 推定
                        "currentPage": 1,  # URLから取得が必要
                        "totalPages": total_pages,
                        "itemsPerPage": len(products),
                        "source": "html:pagination"
                    }
            except Exception as e:
                logger.warning(f"ページネーション情報取得エラー: {e}")

            logger.info(f"✅ 商品抽出完了: {len(products)}件")

            return products, pagination_info

        except Exception as e:
            logger.error(f"商品抽出エラー: {e}", exc_info=True)
            return [], None


async def main():
    """テスト実行用のメイン関数"""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    login_id = os.getenv("TOKYU_STORE_LOGIN_ID")
    password = os.getenv("TOKYU_STORE_PASSWORD")
    zip_code = os.getenv("DELIVERY_ZIP_CODE", "158-0094")

    if not login_id or not password:
        logger.error("❌ 環境変数 TOKYU_STORE_LOGIN_ID と TOKYU_STORE_PASSWORD を設定してください")
        return

    scraper = TokyuStoreScraperPlaywright()

    try:
        # ヘッドレスモードをオフにしてブラウザを表示
        await scraper.start(headless=False)

        # ログイン
        success = await scraper.login(login_id, password)
        if not success:
            logger.error("❌ ログイン失敗")
            return

        # 配達エリア選択
        success = await scraper.select_delivery_area(zip_code)
        if not success:
            logger.warning("⚠️ 配達エリア選択に失敗しましたが続行します")

        # トップページのスクリーンショット・HTML保存
        await scraper.page.screenshot(path="tokyu_store_top.png")
        html_content = await scraper.page.content()
        with open("tokyu_store_top.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info("📸 トップページのスクリーンショット・HTML保存完了")

        # テスト: カテゴリーページにアクセス
        # 実際のカテゴリーURLを確認してから設定
        test_category_url = f"{scraper.base_url}/shop/default.aspx"
        logger.info(f"📦 テストページにアクセス: {test_category_url}")

        products, pagination = await scraper.fetch_products_page(test_category_url, page=1)
        logger.info(f"✅ 商品取得完了: {len(products)}件")
        if pagination:
            logger.info(f"ページネーション情報: {pagination}")

        logger.info("✅ テスト完了")

        # 画面を確認できるように5秒待つ
        await scraper.page.wait_for_timeout(5000)

    finally:
        await scraper.close()


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())
