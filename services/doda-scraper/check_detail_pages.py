"""
各サイトの詳細ページ構造確認
実行: python check_detail_pages.py
"""
import asyncio
from playwright.async_api import async_playwright

TARGETS = [
    ("リクナビNEXT_詳細",
     "https://next.rikunabi.com/viewjob/jkf5fb5a89b1e94760/?referrerId=130&seqNo=1&pageNo=1"),
    ("マイナビ_詳細",
     "https://tenshoku.mynavi.jp/jobinfo-393753-5-5-1/?matchKbn=3&msgKbn=7&deliveryId=57&cs=0c15517c&ty=sm"),
    ("ダイレクトスカウト_詳細",
     "https://directscout.recruit.co.jp/job_descriptions/9660631"),
    ("ダイレクトスカウト_メッセージ一覧",
     "https://directscout.recruit.co.jp/messages"),
]

async def inspect(page, url, label):
    print(f"\n{'='*60}")
    print(f"【{label}】")
    print(f"{'='*60}")

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    print(f"実際のURL: {page.url}")

    # タブ
    print("\n--- タブ要素 ---")
    tabs = await page.evaluate("""
        () => {
            const results = [];
            for (const el of document.querySelectorAll('*')) {
                const cls = String(el.className || '').toLowerCase();
                const id  = String(el.id || '').toLowerCase();
                const text = (el.innerText || '').trim().replace(/\\s+/g,' ').slice(0,60);
                if (!text) continue;
                if (cls.includes('tab') || id.includes('tab')) {
                    results.push({ tag: el.tagName, cls: el.className, id: el.id, text });
                }
            }
            return results.slice(0, 20);
        }
    """)
    seen = set()
    for el in tabs:
        k = str(el['cls']) + str(el['id'])
        if k not in seen:
            seen.add(k)
            print(f"  {el['tag']} class={el['cls']!r} text={el['text']!r}")

    # h1-h3
    print("\n--- 見出し(h1-h3) ---")
    headings = await page.eval_on_selector_all(
        "h1,h2,h3",
        "els => els.map(el => ({ tag:el.tagName, cls:el.className, text:el.innerText.trim().replace(/\\s+/g,' ').slice(0,80) })).filter(e=>e.text)"
    )
    for h in headings[:15]:
        print(f"  {h['tag']} class={h['cls']!r}: {h['text']}")

    # bodyテキスト
    print("\n--- bodyテキスト冒頭1500文字 ---")
    text = (await page.locator("body").inner_text()).strip()
    print(f"文字数: {len(text)}")
    print(text[:1500])


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        for label, url in TARGETS:
            await inspect(page, url, label)
            await asyncio.sleep(2)

        await browser.close()

asyncio.run(main())
