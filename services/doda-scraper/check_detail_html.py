"""
求人詳細ページのタブ周辺HTML構造を調べる
実行: python check_detail_html.py
"""
import asyncio
from playwright.async_api import async_playwright

TARGET_LIST_URLS = [
    ("referredJobList",    "https://doda.jp/dcfront/referredJob/referredJobList/"),
    ("saiyoprojectList",   "https://doda.jp/dcfront/referredJob/saiyoprojectList/"),
    ("interviewOfferList", "https://doda.jp/dcfront/referredJob/interviewOfferList/"),
    ("mapsScoutList",      "https://doda.jp/dcfront/referredJob/mapsScoutList/"),
]

async def inspect_detail(page, detail_url, label):
    print(f"\n{'='*60}")
    print(f"【{label}】{detail_url}")
    print(f"{'='*60}")

    await page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    if "login" in page.url:
        print("セッション切れ")
        return

    # クリッカブルな要素で「タブ」らしきものを全部出す
    # class名にtab/nav/menu/panelを含むli/a/button要素
    clickable_html = await page.evaluate("""
        () => {
            const results = [];
            const els = document.querySelectorAll('li, a, button, span, div');
            for (const el of els) {
                const cls = (el.className || '').toLowerCase();
                const id = (el.id || '').toLowerCase();
                const text = el.innerText ? el.innerText.trim().replace(/\\s+/g, ' ').slice(0, 50) : '';
                if (!text) continue;
                if (cls.match(/tab|nav|menu|panel|anchor|switch|link/) ||
                    id.match(/tab|nav|menu|panel/)) {
                    results.push({
                        tag: el.tagName,
                        cls: el.className,
                        id: el.id,
                        text: text,
                        html: el.outerHTML.slice(0, 200)
                    });
                }
            }
            return results.slice(0, 50);
        }
    """)

    print("\n--- class/idにtab/nav/menu/panel等を含む要素 ---")
    seen = set()
    for el in clickable_html:
        key = el['cls'] + el['id']
        if key not in seen:
            seen.add(key)
            print(f"  {el['tag']} class={el['cls']!r} id={el['id']!r}")
            print(f"    text: {el['text']}")
            print(f"    html: {el['html']}")

    # main/article/section の直下構造を確認
    print("\n--- main > * の直下要素 ---")
    main_children = await page.evaluate("""
        () => {
            const main = document.querySelector('main, #main, .main, article, #contents, .contents');
            if (!main) return ['main要素なし'];
            return Array.from(main.children).map(el => {
                return el.tagName + ' class=' + el.className + ' id=' + el.id + ' text=' + (el.innerText || '').trim().slice(0, 80);
            });
        }
    """)
    for c in main_children[:20]:
        print(f"  {c}")

    # body直下の構造
    print("\n--- body直下の主要要素 ---")
    body_children = await page.evaluate("""
        () => {
            return Array.from(document.body.children).map(el => {
                return el.tagName + ' class=' + el.className + ' id=' + el.id;
            });
        }
    """)
    for c in body_children:
        print(f"  {c}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        for label, list_url in TARGET_LIST_URLS:
            print(f"\n\n{'#'*60}")
            print(f"カテゴリ: {label}")

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

            await inspect_detail(page, links[0]["href"], label)

        await browser.close()

asyncio.run(main())
