"""
doda ページ構造確認スクリプト
- ログインしてマイページを開き、スクリーンショットを撮る
- 実行: python check_pages.py
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import stealth

load_dotenv(Path(__file__).parent.parent.parent / ".env")

DODA_EMAIL    = os.environ["DODA_EMAIL"]
DODA_PASSWORD = os.environ["DODA_PASSWORD"]

SHOTS_DIR = Path(__file__).parent / "screenshots"
SHOTS_DIR.mkdir(exist_ok=True)


async def main():
    async with async_playwright() as p:
        # デバッグポート付きで起動済みの Chrome に接続
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = await context.new_page()
        await stealth(page)

        # トップページから入る（直接ログインページへ飛ぶとbot判定される）
        print("トップページへ移動...")
        await page.goto("https://doda.jp/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # ログインボタンを探してクリック
        login_btn = page.locator('a[href*="login"], a:has-text("ログイン")').first
        if await login_btn.count() > 0:
            await login_btn.click()
            await page.wait_for_load_state("domcontentloaded")
        else:
            await page.goto("https://doda.jp/member/login/", wait_until="domcontentloaded")

        print(f"  ログインページURL: {page.url}")
        await page.screenshot(path=str(SHOTS_DIR / "01_login.png"), full_page=True)
        print(f"  URL: {page.url}")

        # フォーム確認
        inputs = await page.eval_on_selector_all("input", "els => els.map(el => ({type: el.type, name: el.name, id: el.id, placeholder: el.placeholder}))")
        print("  inputフィールド:", inputs)

        # ログイン入力
        email_input = page.locator('input[type="email"], input[name*="mail"], input[name*="login"], input[id*="mail"], input[id*="login"]').first
        pass_input  = page.locator('input[type="password"]').first

        if await email_input.count() > 0:
            await email_input.fill(DODA_EMAIL)
            print("  メールアドレス入力OK")
        else:
            print("  !! メールアドレス入力欄が見つかりません")

        if await pass_input.count() > 0:
            await pass_input.fill(DODA_PASSWORD)
            print("  パスワード入力OK")
        else:
            print("  !! パスワード入力欄が見つかりません")

        await page.screenshot(path=str(SHOTS_DIR / "02_filled.png"), full_page=True)

        # ログインボタンクリック
        submit = page.locator('button[type="submit"], input[type="submit"]').first
        if await submit.count() > 0:
            await submit.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            print(f"  ログイン後URL: {page.url}")
        else:
            print("  !! 送信ボタンが見つかりません")

        await page.screenshot(path=str(SHOTS_DIR / "03_after_login.png"), full_page=True)

        if "login" in page.url:
            print("  !! ログイン失敗")
            input("Enterキーを押して終了...")
            return

        print("  ログイン成功！")

        # マイページのリンク一覧を確認
        print("\nマイページのリンクを確認中...")
        await page.goto("https://doda.jp/member/", wait_until="domcontentloaded")
        await page.screenshot(path=str(SHOTS_DIR / "04_mypage.png"), full_page=True)
        print(f"  URL: {page.url}")

        # ナビゲーションリンクを全て取得
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g, ' ') })).filter(l => l.text && l.href.includes('doda.jp/member'))"
        )
        print("\n  マイページ内リンク:")
        seen = set()
        for lnk in links:
            if lnk["href"] not in seen and lnk["text"]:
                print(f"    {lnk['text'][:40]:40s} -> {lnk['href']}")
                seen.add(lnk["href"])

        print(f"\nスクリーンショット保存先: {SHOTS_DIR}")
        input("\nEnterキーを押して終了...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
