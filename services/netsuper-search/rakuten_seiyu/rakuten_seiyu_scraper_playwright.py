"""
楽天西友ネットスーパー スクレイピングモジュール (Playwright版)

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


class RakutenSeiyuScraperPlaywright:
    """楽天西友ネットスーパーのスクレイピングクラス (Playwright版)"""

    def __init__(self):
        self.base_url = "https://netsuper.rakuten.co.jp/seiyu"
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
                viewport={'width': 1280, 'height': 720},
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
            page_content = await self.page.content()
            current_url = self.page.url

            # URLが楽天西友のホームページであれば、ログイン成功とみなす
            if "netsuper.rakuten.co.jp/seiyu" in current_url and "id.rakuten.co.jp" not in current_url:
                # さらに、ログインページ特有の要素がないことを確認
                if "ログイン" not in page_content or "shopping-cart" in page_content or "minicart" in page_content:
                    return True

            # 従来の方法もチェック
            return "ログアウト" in page_content or "マイページ" in page_content
        except:
            return False

    async def login(self, rakuten_id: str, password: str) -> bool:
        """
        楽天西友にログイン (auth_manager.pyから移植)

        Args:
            rakuten_id: 楽天ID
            password: パスワード

        Returns:
            成功したらTrue
        """
        import asyncio

        try:
            logger.info("🔐 楽天西友にログイン中...")
            await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

            # ログインボタンを探してクリック
            login_selectors = [
                'a:has-text("ログイン")',
                'button:has-text("ログイン")',
                '[data-test="login-button"]',
                '.login-button',
                '#login-link'
            ]

            login_clicked = False
            for selector in login_selectors:
                try:
                    login_button = await self.page.wait_for_selector(
                        selector,
                        timeout=5000,
                        state="visible"
                    )
                    if login_button:
                        await login_button.click()
                        login_clicked = True
                        logger.info(f"ログインボタンをクリック: {selector}")
                        break
                except Exception:
                    continue

            if not login_clicked:
                if await self._is_logged_in():
                    logger.info("✅ 既にログイン済みです")
                    return True
                else:
                    logger.error("ログイン画面に遷移できませんでした")
                    return False

            await self.page.wait_for_load_state("domcontentloaded")

            # ステップ1: 楽天ID入力
            logger.info("ステップ1: 楽天IDを入力中...")
            username_selectors = [
                'input[name="username"]',
                '#user_id',
                'input[autocomplete="username"]',
                'input[name="u"]',
                '#loginInner_u',
                'input[type="email"]',
                'input[placeholder*="楽天会員ID"]',
                'input[placeholder*="ユーザID"]',
                'input[placeholder*="メールアドレス"]'
            ]

            username_filled = False
            for selector in username_selectors:
                try:
                    username_input = await self.page.wait_for_selector(
                        selector,
                        timeout=5000,
                        state="visible"
                    )
                    if username_input:
                        await username_input.click()
                        await username_input.fill(rakuten_id)
                        logger.info(f"✅ 楽天ID入力完了: {selector}")
                        username_filled = True
                        break
                except Exception:
                    continue

            if not username_filled:
                logger.error("楽天ID入力欄が見つかりませんでした")
                return False

            # 「次へ」ボタンをクリック
            next_button_selectors = [
                '#cta001',
                'div[role="button"]:has-text("次へ")',
                'button:has-text("次へ")',
                '[id*="cta"]',
                'button[type="submit"]',
                'input[type="submit"]',
                '.submit-button'
            ]

            next_clicked = False
            for selector in next_button_selectors:
                try:
                    next_button = await self.page.wait_for_selector(
                        selector,
                        timeout=5000,
                        state="visible"
                    )
                    if next_button:
                        await next_button.click()
                        logger.info(f"「次へ」ボタンをクリック: {selector}")
                        next_clicked = True
                        await asyncio.sleep(0.5)
                        await self.page.wait_for_load_state("domcontentloaded")
                        break
                except Exception:
                    continue

            # ステップ2: パスワード入力
            logger.info("ステップ2: パスワードを入力中...")
            password_selectors = [
                '#password_current',
                'input[name="password"]',
                'input[autocomplete="current-password"]'
            ]

            password_filled = False
            for selector in password_selectors:
                try:
                    password_input = await self.page.wait_for_selector(
                        selector,
                        timeout=3000,
                        state="visible"
                    )
                    if password_input:
                        await password_input.click()
                        await password_input.fill(password)
                        logger.info(f"✅ パスワード入力完了: {selector}")
                        password_filled = True
                        break
                except Exception:
                    continue

            if not password_filled:
                logger.error("パスワード入力欄が見つかりませんでした")
                return False

            # ログインボタンをクリック
            await asyncio.sleep(0.5)
            login_button_selectors = [
                '#cta011',
                '#cta001',
                'div[role="button"]:has-text("次へ")',
                '[id*="cta"]'
            ]

            login_clicked = False
            for selector in login_button_selectors:
                try:
                    login_button = await self.page.wait_for_selector(
                        selector,
                        timeout=5000,
                        state="visible"
                    )
                    if login_button:
                        await login_button.click()
                        logger.info(f"ログインボタンをクリック: {selector}")
                        login_clicked = True
                        break
                except Exception:
                    continue

            if not login_clicked:
                logger.error("ログインボタンが見つかりませんでした")
                return False

            # ログイン完了を待機
            await asyncio.sleep(2)
            await self.page.wait_for_load_state("domcontentloaded")

            # デバッグ用：ログイン後のページを保存
            await self.page.screenshot(path="after_login.png")
            logger.info("スクリーンショット保存: after_login.png")

            current_url = self.page.url
            logger.info(f"ログイン後のURL: {current_url}")

            page_content = await self.page.content()
            with open("after_login.html", "w", encoding="utf-8") as f:
                f.write(page_content)
            logger.info("HTML保存: after_login.html")

            # ログイン成功確認
            is_logged_in = await self._is_logged_in()

            # デバッグ情報
            has_logout = "ログアウト" in page_content
            has_mypage = "マイページ" in page_content
            logger.info(f"ログイン確認: ログアウト={has_logout}, マイページ={has_mypage}")

            if is_logged_in:
                logger.info("✅ ログイン成功")
                return True
            else:
                logger.error("❌ ログイン失敗")
                return False

        except Exception as e:
            logger.error(f"ログインエラー: {e}", exc_info=True)
            return False

    async def fetch_products_page(
        self,
        category_url: str,
        page: int = 1
    ) -> tuple[List[Dict[str, Any]], Optional[dict]]:
        """
        カテゴリーページの商品データを取得

        Args:
            category_url: カテゴリーの完全URL（例: "https://netsuper.rakuten.co.jp/seiyu/search/110001/"）
            page: ページ番号

        Returns:
            (商品データのリスト, ページネーション情報)、失敗時は(空リスト, None)
        """
        try:
            # URLにページ番号を追加（既にクエリパラメータがある場合は&で追加）
            if '?' in category_url:
                url = f"{category_url}&page={page}"
            else:
                url = f"{category_url}?page={page}"
            logger.info(f"商品ページ取得中: {url}")

            await self.page.goto(url, wait_until="networkidle", timeout=30000)
            await self.page.wait_for_timeout(3000)

            # デバッグ用：商品ページのHTMLを保存
            if page == 1:
                await self.page.screenshot(path="product_page.png")
                logger.info("商品ページのスクリーンショット保存: product_page.png")

                html_debug = await self.page.content()
                with open("product_page.html", "w", encoding="utf-8") as f:
                    f.write(html_debug)
                logger.info("商品ページのHTML保存: product_page.html")

            logger.info(f"ステータスコード: OK (Playwright)")

            # 商品データとページネーション情報を抽出
            products, pagination_info = await self.extract_products_from_page()

            # アクセス間隔制御
            await self.page.wait_for_timeout(random.randint(1000, 2000))

            return products, pagination_info

        except Exception as e:
            logger.error(f"商品ページ取得エラー: {e}", exc_info=True)
            return [], None

    async def extract_products_from_page(self) -> tuple[List[Dict[str, Any]], Optional[dict]]:
        """
        PlaywrightページからHTMLを解析して商品データを抽出

        Returns:
            (商品データのリスト, ページネーション情報)
        """
        try:
            # HTMLから直接商品データを抽出
            extraction_result = await self.page.evaluate("""
                () => {
                    const result = {
                        products: [],
                        pagination: null
                    };

                    // 総商品数を取得（total-hit属性から）
                    const itemListContainer = document.querySelector('[id="item-list"]');
                    if (itemListContainer) {
                        const totalHit = itemListContainer.getAttribute('total-hit');
                        if (totalHit) {
                            const totalItems = parseInt(totalHit);
                            // 1ページあたり48件として計算
                            const itemsPerPage = 48;
                            const totalPages = Math.ceil(totalItems / itemsPerPage);

                            result.pagination = {
                                totalItems: totalItems,
                                itemsPerPage: itemsPerPage,
                                totalPages: totalPages,
                                source: 'html:total-hit'
                            };
                        }
                    }

                    // 商品要素を取得（data-ratid属性を持つ.product-item要素）
                    const productElements = document.querySelectorAll('.product-item[data-ratid][data-ratunit="item"]');

                    productElements.forEach((element) => {
                        try {
                            // 商品ID（data-ratid）
                            const itemId = element.getAttribute('data-ratid');

                            // メーカー/産地
                            const makerElement = element.querySelector('.product-item-info-maker');
                            const manufacturer = makerElement ? makerElement.textContent.trim() : null;

                            // 商品名
                            const nameElement = element.querySelector('.product-item-info-name');
                            const productName = nameElement ? nameElement.textContent.trim() : null;

                            // 数量
                            const amountElement = element.querySelector('.product-item-info-amount');
                            const amount = amountElement ? amountElement.textContent.trim() : null;

                            // 価格（税抜）
                            const priceElement = element.querySelector('.product-item-info-price');
                            let price = null;
                            if (priceElement) {
                                const priceText = priceElement.childNodes[0].textContent.trim();
                                price = parseInt(priceText.replace(/[^0-9]/g, ''));
                            }

                            // 価格（税込）
                            const taxPriceElement = element.querySelector('.product-item-info-tax');
                            let priceTaxIncluded = null;
                            if (taxPriceElement) {
                                const taxPriceText = taxPriceElement.textContent.trim();
                                const match = taxPriceText.match(/(\d+)円/);
                                if (match) {
                                    priceTaxIncluded = parseInt(match[1]);
                                }
                            }

                            // 画像URL
                            const imgElement = element.querySelector('.product-item-img img');
                            let imageUrl = null;
                            if (imgElement) {
                                imageUrl = imgElement.getAttribute('data-src') || imgElement.getAttribute('src');
                                if (imageUrl && imageUrl.startsWith('//')) {
                                    imageUrl = 'https:' + imageUrl;
                                }
                            }

                            // 商品ページURL
                            const linkElement = element.querySelector('a[href*="/item/"]');
                            const productUrl = linkElement ? linkElement.getAttribute('href') : null;

                            const product = {
                                itemId: itemId,
                                productName: productName,
                                manufacturer: manufacturer,
                                amount: amount,
                                price: price,
                                priceTaxIncluded: priceTaxIncluded,
                                imageUrl: imageUrl,
                                productUrl: productUrl
                            };

                            result.products.push(product);
                        } catch (err) {
                            console.error('Error parsing product element:', err);
                        }
                    });

                    return result;
                }
            """)

            products_data = extraction_result.get('products', [])
            pagination_info = extraction_result.get('pagination')

            logger.info(f"✅ HTML解析結果:")
            logger.info(f"  - 商品数: {len(products_data)}件")

            if pagination_info:
                logger.info(f"  - 総商品数: {pagination_info.get('totalItems')}件")
                logger.info(f"  - 総ページ数: {pagination_info.get('totalPages')}ページ")
                logger.info(f"  - 1ページあたり: {pagination_info.get('itemsPerPage')}件")
            else:
                logger.warning("  - ⚠️ ページネーション情報が見つかりませんでした")

            if not products_data:
                logger.warning("商品データが見つかりませんでした")
                return [], pagination_info

            # 商品データを整形
            products = []
            for item in products_data:
                try:
                    # 商品URLを完全なURLに変換
                    product_url = item.get('productUrl')
                    if product_url:
                        if product_url.startswith('http'):
                            pass  # 既に完全なURL
                        elif product_url.startswith('/'):
                            product_url = f"https://netsuper.rakuten.co.jp{product_url}"
                        else:
                            product_url = f"https://netsuper.rakuten.co.jp/{product_url}"

                    product = {
                        "product_name": f"{item.get('manufacturer', '')} {item.get('productName', '')} {item.get('amount', '')}".strip(),
                        "price": item.get('price'),
                        "price_tax_included": item.get('priceTaxIncluded'),
                        "jan_code": item.get('itemId'),  # 商品IDをJANコードとして使用
                        "image_url": item.get('imageUrl'),
                        "url": product_url,  # URLを追加
                        "manufacturer": item.get('manufacturer'),
                        "category": None,  # HTMLからは取得できないため
                        "in_stock": True,  # リストにあるものは在庫ありと仮定
                        "raw_data": item
                    }
                    products.append(product)
                except Exception as e:
                    logger.error(f"商品データ整形エラー: {e}")
                    continue

            logger.info(f"✅ {len(products)}件の商品を抽出完了")
            return products, pagination_info

        except Exception as e:
            logger.error(f"商品抽出エラー: {e}", exc_info=True)
            return [], None

    def _find_products_in_nuxt(self, nuxt_data: dict) -> Optional[list]:
        """
        NUXT データ内から商品リストを探す

        Args:
            nuxt_data: window.__NUXT__ のJSONデータ

        Returns:
            商品リスト、見つからない場合はNone
        """
        possible_paths = [
            ["data", 0, "itemList"],
            ["data", 0, "products"],
            ["data", "items"],
            ["data", "products"],
            ["state", "items"],
            ["state", "products"],
        ]

        for path in possible_paths:
            try:
                current = nuxt_data
                for key in path:
                    current = current[key]

                if isinstance(current, list) and len(current) > 0:
                    logger.info(f"商品データ発見: {'.'.join(map(str, path))}")
                    return current
            except (KeyError, IndexError, TypeError):
                continue

        logger.debug(f"NUXT データ構造: {json.dumps(nuxt_data, indent=2, ensure_ascii=False)[:1000]}")
        return None

    def _parse_product_item(self, item: dict) -> Optional[Dict[str, Any]]:
        """
        商品アイテムをパースして統一フォーマットに変換

        Args:
            item: 商品データ（辞書）

        Returns:
            整形された商品データ
        """
        try:
            product_name = item.get("name") or item.get("productName") or item.get("title")
            if not product_name:
                logger.warning("商品名が見つかりません")
                return None

            # 価格
            price = None
            price_data = item.get("price") or item.get("priceInfo") or {}
            if isinstance(price_data, dict):
                price = price_data.get("value") or price_data.get("price")
            elif isinstance(price_data, (int, float)):
                price = price_data

            # JANコード
            jan_code = item.get("janCode") or item.get("jan") or item.get("barcode")

            # 画像URL
            image_url = item.get("imageUrl") or item.get("image") or item.get("thumbnailUrl")
            if image_url:
                image_url = self._fix_image_url(image_url)

            # メーカー
            manufacturer = item.get("manufacturer") or item.get("brand") or item.get("maker")

            # カテゴリー
            category = item.get("category") or item.get("categoryName")

            # 在庫状況
            in_stock = item.get("inStock", True)
            is_available = item.get("isAvailable", True)

            # 商品URL（複数のフィールド名をチェック）
            product_url = item.get("url") or item.get("productUrl") or item.get("itemUrl") or item.get("link")

            # URLがない場合、商品IDから構築を試みる
            if not product_url:
                item_id = item.get("itemId") or item.get("id") or item.get("productId")
                if item_id:
                    product_url = f"https://netsuper.rakuten.co.jp/seiyu/product/{item_id}/"

            product = {
                "product_name": product_name,
                "price": price,
                "jan_code": jan_code,
                "image_url": image_url,
                "manufacturer": manufacturer,
                "category": category,
                "in_stock": in_stock,
                "is_available": is_available,
                "url": product_url,  # URLを追加
                "raw_data": item
            }

            return product

        except Exception as e:
            logger.error(f"商品パースエラー: {e}", exc_info=True)
            return None

    def _fix_image_url(self, url: str) -> str:
        """
        画像URLを修正（プロトコル補完など）

        Args:
            url: 元のURL

        Returns:
            修正されたURL
        """
        if url.startswith("//"):
            return f"https:{url}"
        elif not url.startswith("http"):
            return f"{self.base_url}{url}"
        return url

    async def scrape_category_all_pages(
        self,
        category_url: str,
        category_name: str,
        max_pages: int = 100
    ) -> List[Dict[str, Any]]:
        """
        カテゴリーの全ページから商品を取得

        Args:
            category_url: カテゴリーの完全URL
            category_name: カテゴリー名
            max_pages: 最大ページ数（安全装置）

        Returns:
            全商品のリスト
        """
        all_products = []
        page = 1
        total_pages = None

        logger.info(f"カテゴリー '{category_name}' の全ページスクレイピング開始")

        while page <= max_pages:
            logger.info(f"ページ {page} を処理中..." + (f"（全{total_pages}ページ）" if total_pages else ""))

            products, pagination_info = await self.fetch_products_page(category_url, page)

            # 初回のみページネーション情報を取得
            if page == 1 and pagination_info:
                total_pages = pagination_info.get('totalPages')
                total_items = pagination_info.get('totalItems')
                items_per_page = pagination_info.get('itemsPerPage')

                if total_pages:
                    logger.info(f"📄 総ページ数: {total_pages}ページ")
                if total_items:
                    logger.info(f"📦 総商品数: {total_items}件")
                if items_per_page:
                    logger.info(f"📝 1ページあたり: {items_per_page}件")

                # 総ページ数がわかっている場合はmax_pagesを更新
                if total_pages and total_pages < max_pages:
                    max_pages = total_pages
                    logger.info(f"✅ 最大ページ数を {total_pages} に設定")

            if not products:
                logger.info(f"ページ {page} に商品なし、終了します")
                break

            all_products.extend(products)
            logger.info(f"ページ {page}: {len(products)}件取得（累計: {len(all_products)}件）")

            # ページネーション情報から総ページ数がわかっている場合、それを超えたら終了
            if total_pages and page >= total_pages:
                logger.info(f"✅ 全{total_pages}ページの処理完了")
                break

            page += 1

            # ページ間待機
            await self.page.wait_for_timeout(random.randint(1000, 2000))

        logger.info(f"✅ カテゴリー '{category_name}' 完了: 合計 {len(all_products)}件")
        return all_products


async def main():
    """テスト実行用のメイン関数"""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    rakuten_id = os.getenv("RAKUTEN_ID")
    password = os.getenv("RAKUTEN_PASSWORD")

    if not rakuten_id or not password:
        logger.error("❌ 環境変数 RAKUTEN_ID と RAKUTEN_PASSWORD を設定してください")
        return

    async with RakutenSeiyuScraperPlaywright() as scraper:
        # ログイン
        success = await scraper.login(rakuten_id, password)
        if not success:
            logger.error("❌ ログイン失敗")
            return

        # テスト: 1ページ取得
        html = await scraper.fetch_products_page(category_id="110001", page=1)
        if html:
            products = scraper.extract_products_from_html(html)
            logger.info(f"取得した商品数: {len(products)}")

            if products:
                logger.info(f"最初の商品: {json.dumps(products[0], indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
