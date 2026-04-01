"""
offerDetail ページのタブ構造を詳しく調べる
実行: python check_offer_tabs.py
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        # interviewOfferList の最初の詳細ページへ
        await page.goto("https://doda.jp/dcfront/referredJob/interviewOfferList/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

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
            await browser.close()
            return

        detail_url = links[0]["href"]
        print(f"URL: {detail_url}")
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        # クリッカブルな全ボタン・リンクを出す（テキストあり）
        print("\n--- 全 button 要素 ---")
        buttons = await page.eval_on_selector_all(
            "button",
            "els => els.map(el => ({ cls: el.className, text: el.innerText.trim().replace(/\\s+/g, ' '), html: el.outerHTML.slice(0,200) })).filter(e => e.text)"
        )
        for b in buttons:
            print(f"  [{b['text']}] class={b['cls']!r}")
            print(f"    {b['html']}")

        # class名に何も入っていない li 要素でクリッカブルなもの
        print("\n--- クリッカブルな li/div（onClick or cursor:pointer）---")
        clickable = await page.evaluate("""
            () => {
                const results = [];
                const els = document.querySelectorAll('li, div, span, a');
                for (const el of els) {
                    const style = window.getComputedStyle(el);
                    const text = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
                    if (!text) continue;
                    if (style.cursor === 'pointer' && el.tagName !== 'A') {
                        results.push({
                            tag: el.tagName,
                            cls: el.className,
                            text: text,
                            html: el.outerHTML.slice(0, 200)
                        });
                    }
                }
                return results.slice(0, 40);
            }
        """)
        seen = set()
        for el in clickable:
            k = el['cls']
            if k not in seen:
                seen.add(k)
                print(f"  {el['tag']} class={el['cls']!r} text={el['text']!r}")
                print(f"    {el['html']}")

        # ページ内の全 h1/h2/h3 を出す
        print("\n--- 全見出し(h1/h2/h3) ---")
        headings = await page.eval_on_selector_all(
            "h1, h2, h3",
            "els => els.map(el => ({ tag: el.tagName, cls: el.className, text: el.innerText.trim().replace(/\\s+/g, ' ') })).filter(e => e.text)"
        )
        for h in headings:
            print(f"  {h['tag']} class={h['cls']!r}: {h['text'][:80]}")

        # body テキスト全体（スクロール前）
        print("\n--- bodyテキスト全体（スクロール前）文字数 ---")
        text = (await page.locator("body").inner_text()).strip()
        print(f"文字数: {len(text)}")
        print(text[:3000])

        await browser.close()

asyncio.run(main())
