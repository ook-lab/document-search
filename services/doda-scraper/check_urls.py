"""
dodaのマイページから実際のスカウト系URLを探す
実行: python check_urls.py
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://doda.jp/member/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        print(f"現在URL: {page.url}")

        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g, ' ') })).filter(l => l.href.includes('doda.jp') && l.text)"
        )

        seen = set()
        print("\n=== マイページ内リンク ===")
        for lnk in links:
            if lnk["href"] not in seen:
                print(f"{lnk['text'][:40]:40s} -> {lnk['href']}")
                seen.add(lnk["href"])

        await browser.close()

asyncio.run(main())
