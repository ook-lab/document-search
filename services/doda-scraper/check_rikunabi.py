"""
リクナビNEXT スカウトページの構造確認
実行: python check_rikunabi.py
"""
import asyncio
from playwright.async_api import async_playwright

LIST_URL = "https://next.rikunabi.com/offers/"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        print(f"実際のURL: {page.url}")

        if "login" in page.url or "signin" in page.url:
            print("未ログイン")
            await browser.close()
            return

        # 全リンクからスカウト詳細っぽいものを抽出
        print("\n--- スカウト詳細へのリンク候補 ---")
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g,' ').slice(0,60) }))"
            ".filter(l => l.href.includes('rikunabi') && !l.href.endsWith('#') && l.href !== window.location.href)"
        )
        seen = set()
        for l in links:
            h = l['href']
            if h not in seen:
                seen.add(h)
                print(f"  {h}")
                print(f"    text: {l['text']!r}")

        # ページネーション
        print("\n--- ページネーション ---")
        pagers = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => ({ href: el.href, text: el.innerText.trim() }))"
            ".filter(l => l.href.includes('page') || l.href.includes('p=') || /次|next/i.test(l.text))"
        )
        for p_ in pagers:
            print(f"  {p_['href']}  text={p_['text']!r}")

        # h1〜h3
        print("\n--- 見出し ---")
        headings = await page.eval_on_selector_all(
            "h1,h2,h3",
            "els => els.map(el => ({ tag:el.tagName, text:el.innerText.trim().replace(/\\s+/g,' ').slice(0,80) })).filter(e=>e.text)"
        )
        for h in headings:
            print(f"  {h['tag']}: {h['text']}")

        # bodyテキスト冒頭
        print("\n--- bodyテキスト冒頭500文字 ---")
        text = (await page.locator("body").inner_text()).strip()
        print(f"文字数: {len(text)}")
        print(text[:500])

        await browser.close()

asyncio.run(main())
