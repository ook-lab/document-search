"""
求人詳細ページをスクロールして全テキストを確認する
実行: python check_detail_fulltext.py
"""
import asyncio
import random
from playwright.async_api import async_playwright

# referredJobDetail を1件確認（他カテゴリも確認したい場合はURLを変える）
TARGET_LIST_URLS = [
    ("referredJobList",    "https://doda.jp/dcfront/referredJob/referredJobList/"),
    ("saiyoprojectList",   "https://doda.jp/dcfront/referredJob/saiyoprojectList/"),
    ("interviewOfferList", "https://doda.jp/dcfront/referredJob/interviewOfferList/"),
    ("mapsScoutList",      "https://doda.jp/dcfront/referredJob/mapsScoutList/"),
]

async def scroll_and_get_text(page, url) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    scroll_height = await page.evaluate("document.body.scrollHeight")
    viewport_height = await page.evaluate("window.innerHeight")
    current = 0
    while current < scroll_height:
        current = min(current + viewport_height, scroll_height)
        await page.evaluate(f"window.scrollTo(0, {current})")
        await page.wait_for_timeout(random.randint(600, 1000))
        scroll_height = await page.evaluate("document.body.scrollHeight")

    await page.wait_for_timeout(1500)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)

    return (await page.locator("body").inner_text()).strip()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        for label, list_url in TARGET_LIST_URLS:
            print(f"\n{'#'*60}")
            print(f"カテゴリ: {label}")
            print(f"{'#'*60}")

            await page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

            if "login" in page.url:
                print("セッション切れ")
                continue

            links = await page.eval_on_selector_all(
                "a[href]",
                """els => els
                    .map(el => ({ href: el.href }))
                    .filter(l =>
                        l.href.includes('dcfront/referredJob') &&
                        (l.href.includes('Detail') || l.href.includes('detail')) &&
                        !l.href.endsWith('#') &&
                        !l.href.includes('#wrapper')
                    )
                """
            )

            if not links:
                print("詳細リンクなし")
                continue

            detail_url = links[0]["href"]
            print(f"URL: {detail_url}")
            print("スクロール中...")

            text = await scroll_and_get_text(page, detail_url)
            print(f"文字数: {len(text)}")
            print(f"\n{'='*60} 全文 {'='*60}")
            print(text)
            print(f"{'='*60} 終了 {'='*60}")

            # 1カテゴリ確認したら次へ（全部見たい場合はコメントアウト解除不要）
            await asyncio.sleep(3)

        await browser.close()

asyncio.run(main())
