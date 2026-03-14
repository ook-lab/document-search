"""
ダイエーネットスーパー スクレイピングモジュール (Playwright版)

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


class DaieiScraperPlaywright:
    """ダイエーネットスーパーのスクレイピングクラス (Playwright版)"""

    def __init__(self):
        self.base_url = "https://netsuper.daiei.co.jp"
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.store_id: Optional[str] = None  # 店舗ID（ログイン後に取得）

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
            login_form = await self.page.query_selector('input[name="login_id"]')
            if login_form:
                # ログインフォームが見つかった = ログインしていない
                return False

            # 店舗IDを含むURL（/0XXX/）ならログイン済み
            current_url = self.page.url
            import re
            if re.search(r'/0\d{3}/', current_url):
                return True

            return False
        except:
            return False

    async def login(self, login_id: str, password: str) -> bool:
        """
        ダイエーネットスーパーにログイン

        Args:
            login_id: ログインID
            password: パスワード

        Returns:
            成功したらTrue
        """
        import asyncio

        try:
            logger.info("🔐 ダイエーネットスーパーにログイン中...")

            # トップページにアクセス
            await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
            await self.page.wait_for_timeout(2000)

            # 既にログイン済みかチェック
            if await self._is_logged_in():
                logger.info("✅ 既にログイン済みです")
                return True

            # ログインフォームを見つける
            logger.info("ステップ1: ログインIDを入力中...")

            # ログインIDを入力
            login_id_input = await self.page.wait_for_selector(
                'input[name="login_id"]',
                timeout=10000,
                state="visible"
            )
            await login_id_input.click()
            await login_id_input.fill(login_id)
            logger.info("✅ ログインID入力完了")

            # パスワードを入力
            logger.info("ステップ2: パスワードを入力中...")
            password_input = await self.page.wait_for_selector(
                'input[name="password"]',
                timeout=5000,
                state="visible"
            )
            await password_input.click()
            await password_input.fill(password)
            logger.info("✅ パスワード入力完了")

            # ログインボタンをクリック
            await asyncio.sleep(0.5)
            login_button = await self.page.wait_for_selector(
                'button.p-mvLogin__submit, button[onclick*="LoginRun"]',
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

                # WAONポイント案内ページが表示される場合があるので、スキップ
                await asyncio.sleep(1)
                shopping_button = await self.page.query_selector('a[href*="submit_apc_shopping"]')
                if shopping_button:
                    logger.info("WAONポイント案内ページをスキップ中...")
                    await shopping_button.click()
                    await asyncio.sleep(2)
                    await self.page.wait_for_load_state("domcontentloaded")
                    logger.info("✅ お買い物ページに遷移")

                return True
            else:
                logger.error("❌ ログイン失敗")
                return False

        except Exception as e:
            logger.error(f"ログインエラー: {e}", exc_info=True)
            return False

    async def select_delivery_slot(self) -> bool:
        """
        配達日時を選択（利用可能な最初の配達便を選択）

        Returns:
            成功したらTrue
        """
        import asyncio

        try:
            logger.info("📦 配達日時を選択中...")

            # 配達便選択ページを待機
            await self.page.wait_for_timeout(2000)

            # 利用可能な配達便のラベルを探す
            # class="acceptable" (○受付中) または class="a-little" (△残りわずか)
            available_labels = await self.page.query_selector_all(
                'label.acceptable, label.a-little'
            )

            if not available_labels:
                logger.error("❌ 利用可能な配達便が見つかりません")
                return False

            logger.info(f"利用可能な配達便: {len(available_labels)}件")

            # 最初の利用可能な配達便を選択
            first_label = available_labels[0]
            label_id = await first_label.get_attribute("id")
            logger.info(f"配達便ラベルを選択: {label_id}")

            # ラベルをクリック（内部のinputが選択される）
            await first_label.click()
            await asyncio.sleep(1)

            # 「お買い物を始める」ボタンをクリック
            # このボタンは配達便選択後に有効になる
            start_shopping_button = await self.page.wait_for_selector(
                'button[type="button"], input[type="button"], a[href*="index.php"]',
                timeout=10000,
                state="visible"
            )

            # ボタンのテキストを確認
            button_text = await start_shopping_button.inner_text()
            logger.info(f"ボタンテキスト: {button_text}")

            await start_shopping_button.click()
            logger.info("お買い物を始めるボタンをクリック")

            await asyncio.sleep(2)
            await self.page.wait_for_load_state("domcontentloaded")

            # 店舗IDをURLから取得
            current_url = self.page.url
            match = re.search(r'/(\d{4})/', current_url)
            if match:
                self.store_id = match.group(1)
                logger.info(f"✅ 店舗ID取得: {self.store_id}")
            else:
                logger.warning("⚠️ 店舗IDが取得できませんでした")

            logger.info(f"現在のURL: {current_url}")
            logger.info("✅ 配達日時選択完了")
            return True

        except Exception as e:
            logger.error(f"配達日時選択エラー: {e}", exc_info=True)
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
            page: ページ番号（1始まり、内部的にpage=0に変換される）

        Returns:
            (商品データのリスト, ページネーション情報)
        """
        try:
            # ダイエーのページ番号は0始まり（page=0が1ページ目）
            # 引数は1始まりで受け取り、内部で0始まりに変換
            actual_page = page - 1

            # URLにページ番号を追加
            if '?' in category_url:
                url = f"{category_url}&page={actual_page}"
            else:
                url = f"{category_url}?page={actual_page}"

            logger.info(f"商品ページ取得中 (page={page}→{actual_page}): {url}")

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
            await self.page.screenshot(path="daiei_product_page.png")
            logger.info("スクリーンショット保存: daiei_product_page.png")

            html_content = await self.page.content()
            with open("daiei_product_page.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("HTML保存: daiei_product_page.html")

            # 商品コンテナを取得
            product_containers = await self.page.query_selector_all('div.item_ct')
            logger.info(f"商品コンテナ数: {len(product_containers)}")

            products = []

            for container in product_containers:
                try:
                    # 商品IDを取得
                    id_anchor = await container.query_selector('a[id]')
                    product_id = await id_anchor.get_attribute('id') if id_anchor else None

                    # 商品名とURLを取得
                    name_elem = await container.query_selector('div.item_name a')
                    product_name = await name_elem.inner_text() if name_elem else None
                    product_url = None
                    if name_elem:
                        href = await name_elem.get_attribute('href')
                        if href:
                            if href.startswith('http'):
                                product_url = href
                            elif href.startswith('/'):
                                product_url = f"https://netsuper.daiei.co.jp{href}"
                            else:
                                product_url = f"https://netsuper.daiei.co.jp/{href}"

                    # 商品画像URLを取得
                    img_elem = await container.query_selector('div.item_img img')
                    img_src = await img_elem.get_attribute('src') if img_elem else None
                    if img_src and not img_src.startswith('http'):
                        img_src = f"https://netsuper.daiei.co.jp{img_src}"

                    # 本体価格を取得
                    price_elem = await container.query_selector('span.item_price')
                    base_price_text = await price_elem.inner_text() if price_elem else None
                    base_price = float(base_price_text) if base_price_text and base_price_text.isdigit() else None

                    # 税込価格を取得
                    tax_price_elem = await container.query_selector('p.item_price2')
                    tax_price_text = await tax_price_elem.inner_text() if tax_price_elem else None
                    tax_price = None
                    if tax_price_text:
                        import re
                        match = re.search(r'([\d,]+\.?\d*)円', tax_price_text)
                        if match:
                            tax_price = float(match.group(1).replace(',', ''))

                    product = {
                        "product_id": product_id,
                        "product_name": product_name,
                        "price": base_price,
                        "price_tax_included": tax_price,
                        "image_url": img_src,
                        "url": product_url,  # URLを追加
                        "in_stock": True,  # ページに表示されている = 在庫あり
                        "is_available": True,
                        "raw_data": {
                            "product_id": product_id,
                            "base_price": base_price,
                            "tax_price": tax_price,
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
                pagination_text_elem = await self.page.query_selector('text="ヒット数："')
                if pagination_text_elem:
                    parent = await pagination_text_elem.evaluate_handle('node => node.parentElement')
                    text = await parent.inner_text()
                    import re
                    # "ヒット数：205件 2/6ページ" のような形式
                    total_match = re.search(r'(\d+)件', text)
                    page_match = re.search(r'(\d+)/(\d+)ページ', text)

                    if total_match and page_match:
                        total_items = int(total_match.group(1))
                        current_page = int(page_match.group(1))
                        total_pages = int(page_match.group(2))

                        pagination_info = {
                            "totalItems": total_items,
                            "currentPage": current_page,
                            "totalPages": total_pages,
                            "itemsPerPage": len(products),
                            "source": "html:pagination_text"
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

    login_id = os.getenv("DAIEI_LOGIN_ID")
    password = os.getenv("DAIEI_PASSWORD")

    if not login_id or not password:
        logger.error("❌ 環境変数 DAIEI_LOGIN_ID と DAIEI_PASSWORD を設定してください")
        return

    scraper = DaieiScraperPlaywright()

    try:
        # ヘッドレスモードをオフにしてブラウザを表示
        await scraper.start(headless=False)

        # ログイン
        success = await scraper.login(login_id, password)
        if not success:
            logger.error("❌ ログイン失敗")
            return

        # 配達日時選択ページのHTMLを保存
        await scraper.page.screenshot(path="daiei_delivery_page.png")
        html_content = await scraper.page.content()
        with open("daiei_delivery_page.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info("📸 配達日時選択ページのスクリーンショット・HTML保存完了")

        # 配達日時選択
        success = await scraper.select_delivery_slot()
        if not success:
            logger.error("❌ 配達日時選択失敗")
            # 失敗時も少し待つ（画面確認のため）
            await scraper.page.wait_for_timeout(5000)
            return

        # 成功後、商品ページのスクリーンショット・HTML保存
        await scraper.page.screenshot(path="daiei_product_top.png")
        html_content = await scraper.page.content()
        with open("daiei_product_top.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info("📸 商品トップページのスクリーンショット・HTML保存完了")

        # テスト: カテゴリーページにアクセス（野菜果物）
        test_category_url = f"https://netsuper.daiei.co.jp/{scraper.store_id}/item/item.php?classL=2&classS=1&ilc_code=1001"
        logger.info(f"📦 テストカテゴリーページにアクセス: {test_category_url}")

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
