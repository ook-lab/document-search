"""
早稲田アカデミーOnlineブラウザ自動化

Playwrightを使用してログイン・PDF取得を自動化
"""
import os
import asyncio
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from loguru import logger
from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout


class WasedaAcademyBrowser:
    """早稲田アカデミーOnlineのブラウザ自動化"""

    def __init__(
        self,
        login_id: Optional[str] = None,
        password: Optional[str] = None,
        headless: bool = True
    ):
        """
        Args:
            login_id: ログインID（Noneの場合は環境変数から取得）
            password: パスワード（Noneの場合は環境変数から取得）
            headless: ヘッドレスモードで実行するか
        """
        self.login_id = login_id or os.getenv('WASEDA_LOGIN_ID')
        self.password = password or os.getenv('WASEDA_PASSWORD')
        self.headless = headless
        self.base_url = "https://online.waseda-ac.co.jp"

        if not self.login_id or not self.password:
            raise ValueError(
                "ログイン情報が設定されていません。"
                "環境変数 WASEDA_LOGIN_ID と WASEDA_PASSWORD を設定してください。"
            )

    async def login(self, page: Page) -> bool:
        """
        早稲田アカデミーOnlineにログイン

        Args:
            page: Playwrightのページオブジェクト

        Returns:
            ログイン成功時True
        """
        try:
            logger.info("ログインページにアクセス中...")
            await page.goto(f"{self.base_url}/login", wait_until="networkidle")

            # ログインフォームが表示されるまで待機
            await page.wait_for_selector('input[name="email"], input[name="username"], input[type="email"]', timeout=10000)

            # ログインID入力（複数のセレクタを試行）
            login_selectors = [
                'input[name="email"]',
                'input[name="username"]',
                'input[type="email"]',
                'input[placeholder*="メール"]',
                'input[placeholder*="ID"]'
            ]

            login_input_found = False
            for selector in login_selectors:
                try:
                    login_input = page.locator(selector).first
                    if await login_input.count() > 0:
                        await login_input.fill(self.login_id)
                        logger.info(f"ログインID入力完了（selector: {selector}）")
                        login_input_found = True
                        break
                except Exception:
                    continue

            if not login_input_found:
                logger.error("ログインID入力フィールドが見つかりません")
                return False

            # パスワード入力
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]'
            ]

            password_input_found = False
            for selector in password_selectors:
                try:
                    password_input = page.locator(selector).first
                    if await password_input.count() > 0:
                        await password_input.fill(self.password)
                        logger.info(f"パスワード入力完了（selector: {selector}）")
                        password_input_found = True
                        break
                except Exception:
                    continue

            if not password_input_found:
                logger.error("パスワード入力フィールドが見つかりません")
                return False

            # ログインボタンをクリック
            login_button_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("ログイン")',
                'input[value*="ログイン"]'
            ]

            login_button_found = False
            for selector in login_button_selectors:
                try:
                    login_button = page.locator(selector).first
                    if await login_button.count() > 0:
                        logger.info("ログインボタンをクリック中...")
                        await login_button.click()
                        login_button_found = True
                        break
                except Exception:
                    continue

            if not login_button_found:
                logger.error("ログインボタンが見つかりません")
                return False

            # ページ遷移を待機（ログイン後のページ）
            try:
                await page.wait_for_url(f"{self.base_url}/home", timeout=10000)
                logger.info("✓ ログイン成功")
                return True
            except PlaywrightTimeout:
                # URLが変わらない場合、エラーメッセージをチェック
                error_element = page.locator('.error, .alert, [role="alert"]')
                if await error_element.count() > 0:
                    error_text = await error_element.first.text_content()
                    logger.error(f"ログインエラー: {error_text}")
                else:
                    logger.warning("ログイン後のページ遷移を確認できませんでしたが、続行します")
                    return True

        except Exception as e:
            logger.error(f"ログインエラー: {e}", exc_info=True)
            return False

    async def get_notice_page_html(self, page: Page) -> Optional[str]:
        """
        お知らせページのHTMLを取得

        Args:
            page: Playwrightのページオブジェクト

        Returns:
            HTMLコンテンツ、失敗時はNone
        """
        try:
            logger.info("お知らせページにアクセス中...")
            await page.goto(f"{self.base_url}/notice", wait_until="networkidle")

            # window.appPropsが存在するまで待機
            await page.wait_for_function(
                "typeof window.appProps !== 'undefined'",
                timeout=10000
            )

            # HTMLを取得
            html_content = await page.content()
            logger.info(f"HTMLを取得しました（{len(html_content)} bytes）")
            return html_content

        except Exception as e:
            logger.error(f"お知らせページ取得エラー: {e}", exc_info=True)
            return None

    async def download_pdf(
        self,
        page: Page,
        pdf_url: str,
        pdf_title: str
    ) -> Optional[bytes]:
        """
        PDFをダウンロード

        Args:
            page: Playwrightのページオブジェクト
            pdf_url: PDFのURL（相対パス可）
            pdf_title: PDFのタイトル

        Returns:
            PDFのバイトデータ、失敗時はNone
        """
        try:
            # 完全なURLを構築
            if pdf_url.startswith('/'):
                full_url = f"{self.base_url}{pdf_url}"
            elif pdf_url.startswith('http'):
                full_url = pdf_url
            else:
                full_url = f"{self.base_url}/{pdf_url}"

            logger.info(f"PDFダウンロード開始: {pdf_title}")
            logger.debug(f"  URL: {full_url}")

            # ページのコンテキストを使用してAPIリクエストでPDFを取得
            # これにより認証済みセッションでPDFをダウンロードできる
            response = await page.request.get(full_url)

            if response.status != 200:
                logger.error(f"PDFダウンロード失敗: HTTP {response.status}")
                return None

            # PDFデータを取得
            pdf_data = await response.body()

            # Content-Typeを確認
            content_type = response.headers.get('content-type', '')
            if 'application/pdf' not in content_type.lower():
                logger.warning(f"PDFではないコンテンツ: {content_type}")
                # それでもPDFかもしれないので続行

            logger.info(f"PDFダウンロード完了: {len(pdf_data)} bytes")
            return pdf_data

        except Exception as e:
            logger.error(f"PDFダウンロードエラー ({pdf_title}): {e}")
            return None

    async def run_automated_session(
        self
    ) -> Tuple[Optional[str], Dict[str, bytes]]:
        """
        自動化セッションを実行（ログイン → HTML取得 → PDF取得）

        Returns:
            (HTMLコンテンツ, {notice_id: pdf_data}の辞書)
        """
        html_content = None
        pdfs = {}

        async with async_playwright() as p:
            # ブラウザを起動
            logger.info(f"ブラウザ起動中（headless={self.headless}）...")
            browser = await p.chromium.launch(headless=self.headless)

            try:
                # 新しいコンテキストを作成
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                )
                page = await context.new_page()

                # ログイン
                if not await self.login(page):
                    logger.error("ログインに失敗しました")
                    return None, {}

                # お知らせページのHTMLを取得
                html_content = await self.get_notice_page_html(page)
                if not html_content:
                    logger.error("HTMLの取得に失敗しました")
                    return None, {}

                logger.info("✓ 自動化セッション完了")

            finally:
                await browser.close()

        return html_content, pdfs

    async def download_pdfs_batch(
        self,
        pdf_info_list: List[Dict[str, str]]
    ) -> Dict[str, bytes]:
        """
        複数のPDFを一括ダウンロード

        Args:
            pdf_info_list: [{
                'notice_id': 'xxx',
                'pdf_url': '/notice/xxx/pdf/0',
                'pdf_title': 'タイトル'
            }, ...]

        Returns:
            {notice_id: pdf_data}の辞書
        """
        pdfs = {}

        async with async_playwright() as p:
            logger.info(f"ブラウザ起動中（PDF一括ダウンロード）...")
            browser = await p.chromium.launch(headless=self.headless)

            try:
                context = await browser.new_context()
                page = await context.new_page()

                # ログイン
                if not await self.login(page):
                    logger.error("ログインに失敗しました")
                    return {}

                # 各PDFをダウンロード
                for i, pdf_info in enumerate(pdf_info_list, 1):
                    notice_id = pdf_info['notice_id']
                    pdf_url = pdf_info['pdf_url']
                    pdf_title = pdf_info['pdf_title']

                    logger.info(f"[{i}/{len(pdf_info_list)}] {pdf_title}")

                    pdf_data = await self.download_pdf(page, pdf_url, pdf_title)
                    if pdf_data:
                        pdfs[notice_id] = pdf_data

                    # レート制限対策
                    await asyncio.sleep(1)

            finally:
                await browser.close()

        logger.info(f"PDFダウンロード完了: {len(pdfs)}/{len(pdf_info_list)}件")
        return pdfs


async def test_browser_automation():
    """ブラウザ自動化のテスト"""
    browser = WasedaAcademyBrowser(headless=False)  # デバッグ用にヘッドレスオフ

    html_content, pdfs = await browser.run_automated_session()

    if html_content:
        logger.info(f"HTML取得成功: {len(html_content)} bytes")
        # HTMLをファイルに保存
        with open('waseda_notice_page.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info("HTMLをwaseda_notice_page.htmlに保存しました")
    else:
        logger.error("HTML取得失敗")


if __name__ == "__main__":
    asyncio.run(test_browser_automation())
