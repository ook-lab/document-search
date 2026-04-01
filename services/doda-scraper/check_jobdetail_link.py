"""
saiyoprojectDetail / offerDetail から JobSearchDetail へのリンクを確認する
実行: python check_jobdetail_link.py
"""
import asyncio
from playwright.async_api import async_playwright

TARGETS = [
    ("saiyoprojectList",   "https://doda.jp/dcfront/referredJob/saiyoprojectList/"),
    ("interviewOfferList", "https://doda.jp/dcfront/referredJob/interviewOfferList/"),
]

async def inspect(page, detail_url, label):
    print(f"\n{'='*60}")
    print(f"【{label}】{detail_url}")

    await page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    # JobSearchDetail を含む全リンクを取得
    links = await page.eval_on_selector_all(
        "a[href*='JobSearchDetail']",
        "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g,' ').slice(0,60), cls: el.className }))"
    )
    print(f"\n--- JobSearchDetail へのリンク ---")
    for lnk in links:
        print(f"  text={lnk['text']!r} cls={lnk['cls']!r}")
        print(f"  href={lnk['href']}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        for label, list_url in TARGETS:
            await page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
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
                print(f"{label}: 詳細リンクなし")
                continue

            await inspect(page, links[0]["href"], label)

        await browser.close()

asyncio.run(main())
