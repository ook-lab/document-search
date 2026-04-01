"""
JobSearchDetail ページのタブ構造を調べる
実行: python check_jobdetail_tabs.py
"""
import asyncio
import random
from playwright.async_api import async_playwright

# ユーザーが教えてくれたURL（2件）
URLS = [
    ("saiyoproject経由",
     "https://doda.jp/DodaFront/View/JobSearchDetail.action?jid=3014354479&scoutlist_id=1052958552&message_id=1139685050&from=agent_message&info=-1&entryStatus=-1&target=0"),
    ("offer経由(action)",
     "https://doda.jp/DodaFront/View/JobSearchDetail.action?jid=3014530538&fm=list&msg=1&tp=1&mpsc_sid=10&message_id=1&scoutlist_id=999999999&from=scout_message&record_message_id=1139898371&record_scoutlist_id=999999999"),
    ("offer経由(最終URL)",
     "https://doda.jp/DodaFront/View/JobSearchDetail/j_jid__3014530538/-tp__1/-tab__jd/-fm__jobdetail/-mpsc_sid__10/-scoutlist_id__999999999/-message_id__1/-record_scoutlist_id__999999999/-record_message_id__1139898371/"),
]

async def inspect(page, url, label):
    print(f"\n{'='*60}")
    print(f"【{label}】")
    print(url)
    print(f"{'='*60}")

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    print(f"実際のURL: {page.url}")

    if "login" in page.url:
        print("セッション切れ")
        return

    # タブ要素を全クラス名で探す
    print("\n--- タブ要素（全パターン）---")
    tab_els = await page.evaluate("""
        () => {
            const results = [];
            for (const el of document.querySelectorAll('*')) {
                const cls = (el.className || '').toString().toLowerCase();
                const id = (el.id || '').toLowerCase();
                const text = (el.innerText || '').trim().replace(/\\s+/g,' ').slice(0,60);
                if (!text) continue;
                if (cls.includes('tab') || id.includes('tab')) {
                    results.push({
                        tag: el.tagName,
                        cls: el.className,
                        id: el.id,
                        text: text,
                        html: el.outerHTML.slice(0,300)
                    });
                }
            }
            return results.slice(0, 40);
        }
    """)
    seen = set()
    for el in tab_els:
        k = str(el['cls']) + str(el['id'])
        if k not in seen:
            seen.add(k)
            print(f"  {el['tag']} class={el['cls']!r} id={el['id']!r}")
            print(f"    text: {el['text']}")
            print(f"    html: {el['html']}")

    # 全見出し
    print("\n--- 見出し(h1-h3) ---")
    headings = await page.eval_on_selector_all(
        "h1,h2,h3",
        "els => els.map(el => ({ tag:el.tagName, cls:el.className, text:el.innerText.trim().replace(/\\s+/g,' ') })).filter(e=>e.text)"
    )
    for h in headings:
        print(f"  {h['tag']} class={h['cls']!r}: {h['text'][:80]}")

    # bodyテキスト冒頭
    print("\n--- bodyテキスト冒頭1000文字 ---")
    text = (await page.locator("body").inner_text()).strip()
    print(f"文字数: {len(text)}")
    print(text[:1000])


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        for label, url in URLS:
            await inspect(page, url, label)
            await asyncio.sleep(random.uniform(2, 4))

        await browser.close()

asyncio.run(main())
