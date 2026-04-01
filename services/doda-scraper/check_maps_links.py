"""
mapsScoutDetail の JobSearchDetail リンクを確認する
実行: python check_maps_links.py
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://doda.jp/dcfront/referredJob/mapsScoutList/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        links = await page.eval_on_selector_all(
            "a[href]",
            """els => els.map(el => ({ href: el.href }))
                .filter(l => l.href.includes('dcfront/referredJob') &&
                    (l.href.includes('Detail') || l.href.includes('detail')) &&
                    !l.href.endsWith('#') && !l.href.includes('#wrapper'))
            """
        )
        if not links:
            print("詳細リンクなし")
            await browser.close()
            return

        detail_url = links[0]["href"]
        print(f"詳細URL: {detail_url}")

        await page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        # btnJob07 リンク
        print("\n--- a.btnJob07 リンク ---")
        btns = await page.eval_on_selector_all(
            "a.btnJob07",
            "els => els.map(el => ({ href: el.href, text: el.innerText.trim(), cls: el.className }))"
        )
        for b in btns:
            print(f"  text={b['text']!r} cls={b['cls']!r}")
            print(f"  href={b['href']}")

        # JobSearchDetail を含む全リンク
        print("\n--- JobSearchDetail へのリンク ---")
        jd_links = await page.eval_on_selector_all(
            "a[href*='JobSearchDetail']",
            "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g,' ').slice(0,60), cls: el.className }))"
        )
        for lnk in jd_links:
            print(f"  text={lnk['text']!r} cls={lnk['cls']!r}")
            print(f"  href={lnk['href']}")

        await browser.close()

asyncio.run(main())
