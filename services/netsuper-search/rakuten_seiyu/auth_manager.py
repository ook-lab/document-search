"""
楽天西友ネットスーパー 認証マネージャー

Playwrightを使用して楽天IDでログインし、配送先を設定した後、
セッションCookieをファイルに保存します。
"""

import json
import logging
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# ロガー設定
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class RakutenSeiyuAuthManager:
    """楽天西友ネットスーパーの認証を管理するクラス"""

    def __init__(self, headless: bool = True):
        """
        Args:
            headless: ヘッドレスモードで実行するか（デフォルト: True）
        """
        self.headless = headless
        self.base_url = "https://netsuper.rakuten.co.jp/seiyu"
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None

    async def __aenter__(self):
        """コンテキストマネージャーのエントリー"""
        await self._launch_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """コンテキストマネージャーの終了"""
        await self.close()

    async def _launch_browser(self):
        """ブラウザを起動"""
        logger.info("ブラウザを起動中...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        self.page = await self.context.new_page()
        logger.info("ブラウザ起動完了")

    async def login(self, rakuten_id: str, password: str) -> bool:
        """
        楽天IDでログイン

        Args:
            rakuten_id: 楽天ID（メールアドレス）
            password: パスワード

        Returns:
            成功したらTrue
        """
        try:
            logger.info("楽天西友トップページにアクセス中...")
            await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)

            # デバッグ用：ページのスクリーンショットとHTMLを保存
            await self.page.screenshot(path="debug_step1_top_page.png")
            logger.info("スクリーンショット保存: debug_step1_top_page.png")

            html_content = await self.page.content()
            with open("debug_step1_top_page.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("HTML保存: debug_step1_top_page.html")

            # ログインボタンを探してクリック
            logger.info("ログインボタンを探しています...")
            logger.info(f"現在のURL: {self.page.url}")

            # 複数のセレクタパターンを試行
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
                logger.warning("ログインボタンが見つかりませんでした。既にログイン済みの可能性があります。")
                # 既にログイン済みかチェック
                if await self._is_logged_in():
                    logger.info("既にログイン済みです")
                    return True
                else:
                    logger.error("ログイン画面に遷移できませんでした")
                    return False

            # 楽天ID入力画面が表示されるまで待機
            logger.info("楽天ID入力画面を待機中...")
            await self.page.wait_for_load_state("domcontentloaded")

            # デバッグ用：ログイン画面のスクリーンショットとHTMLを保存
            await self.page.screenshot(path="debug_step1_5_login_form.png")
            logger.info("スクリーンショット保存: debug_step1_5_login_form.png")

            html_content = await self.page.content()
            with open("debug_step1_5_login_form.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("HTML保存: debug_step1_5_login_form.html")
            logger.info(f"ログインフォームURL: {self.page.url}")

            # ステップ1: 楽天ID（ユーザー名）を入力
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
                        # クリックしてフォーカスを当てる
                        await username_input.click()
                        # すぐに入力
                        await username_input.fill(rakuten_id)
                        logger.info(f"楽天ID入力完了: {selector}")
                        username_filled = True
                        break
                except Exception as e:
                    logger.debug(f"楽天ID入力欄検索失敗: {selector} - {e}")
                    continue

            if not username_filled:
                logger.error("楽天ID入力欄が見つかりませんでした")
                return False

            # 「次へ」ボタンをクリック（楽天の2段階認証）
            logger.info("「次へ」ボタンを探しています...")
            next_button_selectors = [
                '#cta001',
                'div[role="button"]:has-text("次へ")',
                'button:has-text("次へ")',
                '[id*="cta"]',
                'button[type="submit"]',
                'input[type="submit"]',
                '.submit-button'
            ]

            import asyncio
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
                        await asyncio.sleep(4)  # ページ遷移を待つ（延長）
                        await self.page.wait_for_load_state("domcontentloaded")
                        break
                except Exception as e:
                    logger.debug(f"「次へ」ボタン検索失敗: {selector} - {e}")
                    continue

            # ステップ2: パスワードを入力
            logger.info("ステップ2: パスワードを入力中...")

            # デバッグ用：パスワード画面のスクリーンショットとHTMLを保存
            await self.page.screenshot(path="debug_step1_7_password_form.png")
            logger.info("スクリーンショット保存: debug_step1_7_password_form.png")

            html_content = await self.page.content()
            with open("debug_step1_7_password_form.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("HTML保存: debug_step1_7_password_form.html")
            logger.info(f"パスワードフォームURL: {self.page.url}")

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
                        # クリックしてフォーカスを当ててすぐに入力
                        await password_input.click()
                        await password_input.fill(password)
                        logger.info(f"パスワード入力完了: {selector}")
                        password_filled = True
                        break
                except Exception as e:
                    logger.debug(f"パスワード入力欄検索失敗: {selector} - {e}")
                    continue

            if not password_filled:
                logger.error("パスワード入力欄が見つかりませんでした")
                return False

            # ログインボタンをクリック
            logger.info("ログインボタンを探しています...")

            # ボタンが有効になるまで少し待つ
            await asyncio.sleep(1)

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
                except Exception as e:
                    logger.debug(f"ログインボタン検索失敗: {selector} - {e}")
                    continue

            if not login_clicked:
                logger.error("ログインボタンが見つかりませんでした")
                return False

            # ログイン完了を待機（十分な時間を確保）
            await asyncio.sleep(3)  # 3秒待機
            await self.page.wait_for_load_state("domcontentloaded")
            logger.info("ログイン処理完了")

            # デバッグ用：ログイン後のスクリーンショットとHTMLを保存
            await self.page.screenshot(path="debug_step2_after_login.png")
            logger.info("スクリーンショット保存: debug_step2_after_login.png")

            html_content = await self.page.content()
            with open("debug_step2_after_login.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info("HTML保存: debug_step2_after_login.html")
            logger.info(f"ログイン後のURL: {self.page.url}")

            # ログイン成功確認
            is_logged_in = await self._is_logged_in()
            logger.info(f"ログイン状態チェック結果: {is_logged_in}")

            if is_logged_in:
                logger.info("✅ ログイン成功")
                return True
            else:
                logger.error("❌ ログイン失敗")
                # エラーメッセージがあれば表示
                try:
                    error_msg = await self.page.query_selector('text=/エラー|ログインに失敗|正しく/')
                    if error_msg:
                        error_text = await error_msg.text_content()
                        logger.error(f"エラーメッセージ: {error_text}")
                except:
                    pass
                return False

        except Exception as e:
            logger.error(f"ログイン処理エラー: {e}", exc_info=True)
            return False

    async def _is_logged_in(self) -> bool:
        """ログイン状態を確認"""
        try:
            # ログイン状態を示す要素をチェック
            # （実際のサイトの構造に応じて調整が必要）
            current_url = self.page.url
            logger.debug(f"ログイン確認中 - URL: {current_url}")

            # ログアウトボタンやマイページリンクがあればログイン済み
            logout_exists = await self.page.query_selector('a:has-text("ログアウト")') is not None
            logger.debug(f"ログアウトボタン存在: {logout_exists}")

            mypage_exists = await self.page.query_selector('a:has-text("マイページ")') is not None
            logger.debug(f"マイページリンク存在: {mypage_exists}")

            # 楽天西友特有の要素をチェック
            cart_exists = await self.page.query_selector('a:has-text("カート")') is not None
            logger.debug(f"カートボタン存在: {cart_exists}")

            # URLチェック
            url_check = "mypage" in current_url.lower() or "member" in current_url.lower()
            logger.debug(f"URL判定: {url_check}")

            result = logout_exists or mypage_exists or cart_exists or url_check
            logger.debug(f"最終判定: {result}")

            return result
        except Exception as e:
            logger.error(f"ログイン確認エラー: {e}")
            return False

    async def set_delivery_area(self, zip_code: str) -> bool:
        """
        配送先エリアを設定

        Args:
            zip_code: 郵便番号（例: "211-0063"）

        Returns:
            成功したらTrue
        """
        try:
            logger.info(f"配送先エリアを設定中: {zip_code}")

            # 配送先設定ページに移動
            # （実際のサイト構造に応じて調整が必要）

            # 郵便番号入力フォームを探す
            zip_code_selectors = [
                'input[name="zip_code"]',
                'input[name="zipcode"]',
                'input[placeholder*="郵便番号"]',
                '#zip-code-input'
            ]

            zip_code_found = False
            for selector in zip_code_selectors:
                try:
                    zip_input = await self.page.wait_for_selector(
                        selector,
                        timeout=5000,
                        state="visible"
                    )
                    if zip_input:
                        await zip_input.fill(zip_code.replace("-", ""))
                        logger.info(f"郵便番号入力完了: {selector}")
                        zip_code_found = True
                        break
                except Exception:
                    continue

            if not zip_code_found:
                logger.warning("郵便番号入力欄が見つかりませんでした")
                # エリア設定が不要な場合もあるのでWarningのみ
                return True

            # 確定ボタンをクリック
            confirm_selectors = [
                'button:has-text("確定")',
                'button:has-text("この住所に配送")',
                'button[type="submit"]'
            ]

            for selector in confirm_selectors:
                try:
                    confirm_button = await self.page.wait_for_selector(
                        selector,
                        timeout=5000
                    )
                    if confirm_button:
                        await confirm_button.click()
                        logger.info("配送先確定ボタンをクリック")
                        break
                except Exception:
                    continue

            await self.page.wait_for_load_state("domcontentloaded")
            logger.info("✅ 配送先エリア設定完了")
            return True

        except Exception as e:
            logger.error(f"配送先エリア設定エラー: {e}", exc_info=True)
            return False

    async def save_cookies(self, file_path: str = "rakuten_seiyu_cookies.json") -> bool:
        """
        セッションCookieをファイルに保存

        Args:
            file_path: 保存先ファイルパス

        Returns:
            成功したらTrue
        """
        try:
            logger.info(f"Cookieを保存中: {file_path}")
            cookies = await self.context.cookies()

            # ファイルに保存
            output_path = Path(file_path)
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2, ensure_ascii=False)

            logger.info(f"✅ Cookie保存完了: {len(cookies)}個のCookieを保存")
            return True

        except Exception as e:
            logger.error(f"Cookie保存エラー: {e}", exc_info=True)
            return False

    async def close(self):
        """ブラウザを閉じる"""
        try:
            if self.browser:
                await self.browser.close()
                logger.info("ブラウザを閉じました")
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.error(f"ブラウザクローズエラー: {e}", exc_info=True)


async def main():
    """テスト実行用のメイン関数"""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    rakuten_id = os.getenv("RAKUTEN_ID")
    password = os.getenv("RAKUTEN_PASSWORD")
    zip_code = os.getenv("DELIVERY_ZIP_CODE", "211-0063")

    if not rakuten_id or not password:
        logger.error("環境変数 RAKUTEN_ID と RAKUTEN_PASSWORD を設定してください")
        return

    async with RakutenSeiyuAuthManager(headless=False) as auth:
        # ログイン
        if await auth.login(rakuten_id, password):
            # 配送先設定
            await auth.set_delivery_area(zip_code)
            # Cookie保存
            await auth.save_cookies("rakuten_seiyu_cookies.json")
            logger.info("✅ 認証処理完了")
        else:
            logger.error("❌ 認証処理失敗")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
