"""
求人詳細ページのタブ・セクション構造を調べる
実行: python check_detail_tabs.py
"""
import asyncio
from playwright.async_api import async_playwright

TARGET_LIST_URLS = [
    "https://doda.jp/dcfront/referredJob/referredJobList/",
    "https://doda.jp/dcfront/referredJob/saiyoprojectList/",
    "https://doda.jp/dcfront/referredJob/interviewOfferList/",
    "https://doda.jp/dcfront/referredJob/mapsScoutList/",
]

async def check_detail(page, detail_url, label):
    print(f"\n{'='*60}")
    print(f"【{label}】{detail_url}")
    print(f"{'='*60}")

    await page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    if "login" in page.url:
        print("  → セッション切れ")
        return

    # タブ・ナビ要素を探す
    tab_selectors = [
        "ul.tab li",
        "ul.tabs li",
        "nav.tab",
        ".tabList li",
        ".tab-list li",
        "[role='tab']",
        "[role='tablist'] *",
        ".js-tab",
        ".tabArea li",
        ".tabNav li",
        "li.tab",
    ]
    print("\n--- タブ要素 ---")
    for sel in tab_selectors:
        els = await page.locator(sel).all()
        if els:
            texts = []
            for el in els:
                t = (await el.inner_text()).strip()
                if t:
                    texts.append(t[:40])
            if texts:
                print(f"  {sel}: {texts}")

    # a[href] でページ内リンク（#で始まるもの）を探す
    print("\n--- ページ内アンカーリンク(#) ---")
    anchors = await page.eval_on_selector_all(
        "a[href^='#']",
        "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g, ' ') })).filter(l => l.text)"
    )
    seen = set()
    for a in anchors:
        k = a["href"]
        if k not in seen:
            seen.add(k)
            print(f"  {a['text'][:50]:50s} -> {a['href']}")

    # ページ全体のセクション見出しを確認（h2/h3）
    print("\n--- h2/h3 見出し ---")
    headings = await page.eval_on_selector_all(
        "h2, h3",
        "els => els.map(el => ({ tag: el.tagName, text: el.innerText.trim().replace(/\\s+/g, ' ') })).filter(l => l.text)"
    )
    for h in headings:
        print(f"  {h['tag']}: {h['text'][:80]}")

    # bodyテキストの冒頭500文字（構造把握用）
    print("\n--- bodyテキスト冒頭 ---")
    body = (await page.locator("body").inner_text()).strip()
    print(body[:500])


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        for list_url in TARGET_LIST_URLS:
            label = list_url.rstrip("/").split("/")[-1]
            print(f"\n\n{'#'*60}")
            print(f"一覧: {list_url}")
            print(f"{'#'*60}")

            await page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

            if "login" in page.url:
                print("セッション切れ")
                continue

            # 最初の詳細リンクを1件だけ取得
            links = await page.eval_on_selector_all(
                "a[href]",
                """els => els
                    .map(el => ({ href: el.href, text: el.innerText.trim() }))
                    .filter(l =>
                        l.href.includes('dcfront/referredJob') &&
                        (l.href.includes('Detail') || l.href.includes('detail')) &&
                        !l.href.endsWith('#') &&
                        !l.href.includes('#wrapper')
                    )
                """
            )

            if not links:
                print("  詳細リンクなし（求人ゼロ）")
                continue

            detail_url = links[0]["href"]
            await check_detail(page, detail_url, label)

        await browser.close()

asyncio.run(main())
