"""
doda マイページ スクレイパー

【起動手順】
1. launch_chrome.bat を実行して Chrome を起動
2. 初回のみ: Chromeの画面でdoda.jpに手動ログイン（以降は自動）
3. python scraper.py を実行

【デバッグ】
python scraper.py --debug  (スクリーンショット保存)
"""
import asyncio
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout
from supabase import create_client, Client

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

DEBUG = "--debug" in sys.argv

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]

BASE_URL = "https://doda.jp"

MAX_JOBS_PER_CATEGORY = 2  # 1回の実行で取得する最大件数

CATEGORIES = {
    "agent_recommend": {"label": "キャリアアドバイザー紹介求人",        "url": f"{BASE_URL}/dcfront/referredJob/referredJobList/"},
    "project_manager": {"label": "採用プロジェクト担当紹介求人",        "url": f"{BASE_URL}/dcfront/referredJob/saiyoprojectList/"},
    "company_offer":   {"label": "企業からのオファー",                   "url": f"{BASE_URL}/dcfront/referredJob/interviewOfferList/"},
    "partner_agent":   {"label": "パートナーエージェントからのスカウト", "url": f"{BASE_URL}/dcfront/referredJob/mapsScoutList/"},
}


def get_db() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


async def screenshot(page: Page, name: str):
    if DEBUG:
        path = Path(__file__).parent / "screenshots" / f"{name}.png"
        path.parent.mkdir(exist_ok=True)
        try:
            await page.screenshot(path=str(path), full_page=True, timeout=60000)
            print(f"  [screenshot] {path}")
        except Exception as e:
            print(f"  [screenshot失敗スキップ] {name}: {e}")


async def ensure_logged_in(page: Page):
    """ログイン確認。未ログインなら初回ログインページを表示して完了を自動検知する。"""
    await page.goto(f"{BASE_URL}/member/", wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)

    if "login" not in page.url:
        print(f"ログイン済み: {page.url}")
        return

    # 初回: ログインページを表示してユーザーのログイン完了を待つ
    print("初回ログインが必要です。Chromeのウィンドウでdoda.jpにログインしてください。")
    print("ログイン完了後、自動的に処理を開始します...")
    await page.wait_for_url(lambda url: "login" not in url, timeout=300000)
    print(f"ログイン完了: {page.url}")


async def get_job_list(page: Page, category_key: str, url: str) -> list[dict]:
    """一覧ページから全件の求人URLを収集（ページネーション対応）"""
    print(f"  一覧取得: {url}")
    jobs = []
    page_num = 1

    while True:
        page_url = url if page_num == 1 else f"{url}?page={page_num}"
        await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(random.randint(2000, 4000))
        await page.mouse.wheel(0, random.randint(300, 600))
        await page.wait_for_timeout(random.randint(1000, 2000))
        await screenshot(page, f"{category_key}_list_p{page_num}")

        if "login" in page.url:
            raise RuntimeError("セッション切れ。launch_chrome.bat を再起動してください。")

        links = await page.eval_on_selector_all(
            "a[href]",
            """els => els
                .map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g, ' ') }))
                .filter(l =>
                    l.href.includes('doda.jp') &&
                    l.href.includes('dcfront/referredJob') &&
                    (l.href.includes('Detail') || l.href.includes('detail')) &&
                    l.href !== window.location.href &&
                    !l.href.endsWith('#') &&
                    !l.href.includes('#wrapper')
                )
            """
        )

        seen_urls = {j["url"] for j in jobs} | {url.rstrip("/")}
        new_links = []
        for lnk in links:
            href = lnk["href"].rstrip("/")
            if href not in seen_urls:
                seen_urls.add(href)
                new_links.append(lnk)

        if not new_links:
            print(f"    p{page_num}: 求人リンクなし → 終了")
            break

        before = len(jobs)
        for lnk in new_links:
            if len(jobs) >= MAX_JOBS_PER_CATEGORY:
                break
            jobs.append({"url": lnk["href"], "link_text": lnk["text"]})

        added = len(jobs) - before
        print(f"    p{page_num}: {added} 件追加（累計 {len(jobs)} 件）")

        if added == 0 or len(jobs) >= MAX_JOBS_PER_CATEGORY:
            break

        next_btn = page.locator('a.next, a[rel="next"], .pagination .next a, li.next a')
        if await next_btn.count() == 0:
            break
        page_num += 1

    return jobs


def _find(patterns: list[str], text: str) -> str:
    for p in patterns:
        m = re.search(p, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


def parse_job_text(text: str) -> dict:
    """ページ全文テキストから求人情報を正規表現で抽出（全4カテゴリ対応）"""

    # 雇用企業名を優先（エージェント名より先にマッチ）
    company_name = _find([
        r'◆企業名[：:]\s*([^\n◆]+)',              # saiyoproject
        r'■企業名[：:]\s*([^\n■━─\-]+)',          # mapsScout（実雇用企業）
        r'【企業名】\s*\n([^\n【]+)',              # offer
        r'会社名\s*\t([^\n]+)',                   # fallback
    ], text)
    # referredJobDetail: "転職なら..." の次の非空行が会社名
    if not company_name:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for i, line in enumerate(lines):
            if '転職なら' in line and i + 1 < len(lines):
                company_name = lines[i + 1]
                break

    job_title = _find([
        r'件名\s*\t([^\n]+)',                     # saiyoproject / offer / mapsScout
        r'◆職種名[：:]\s*([^\n◆]+)',              # saiyoproject
        r'■職種名[：:]\s*([^\n■━─\-]+)',          # mapsScout
        r'【職種名】\s*\n([^\n【]+)',              # offer
    ], text)
    # referredJobDetail: 会社名の次の行が求人タイトル
    if not job_title:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for i, line in enumerate(lines):
            if line == company_name and i + 1 < len(lines):
                job_title = lines[i + 1]
                break

    salary = _find([
        r'◆想定年収[：:]\s*([^\n◆]+)',
        r'【想定年収】\s*\n([^\n【]+)',
        r'【給与条件】([^\n【]+)',
        r'\[給与形態\][^\n]*\n.*?年収([0-9０-９万円,，〜～\-–\s]+)',
        r'年収([0-9０-９,，万円〜～\-–]+)',
    ], text)

    location = _find([
        r'◆勤務地1[：:]\s*([^\n◆]+)',
        r'【勤務地】\s*\n(.*?)(?=【想定年収】|【雇用形態】|■|━━)',  # offerDetail: 複数行
        r'【勤務地】([^\n━─■【]+)',                                 # mapsScout: 1行
        r'勤務地[：:]\s*([^\n転勤]+)',
    ], text)

    employment_type = _find([
        r'◆雇用形態[：:]\s*([^\n◆]+)',
        r'【雇用形態】([^\n【]+)',
        r'■雇用形態\s*\n---+\n【雇用形態】([^\n【]+)',
        r'雇用形態[：:]\s*([^\n]+)',
    ], text)

    description = _find([
        r'◆仕事内容\n(.*?)(?=◆|＜doda|本求人票)',
        r'■業務内容\n-+\n(.*?)(?=■|━━)',
        r'職務内容\t(.*?)(?=応募資格|最終学歴)',
        r'【職務内容】\n(.*?)(?=【|■)',
        # JobSearchDetail形式（H3見出し）
        r'仕事内容\n(.*?)(?=\n対象となる方|\n勤務地|\n勤務時間|\n雇用形態)',
    ], text)

    requirements = _find([
        r'◆応募要件[：:]\n(.*?)(?=◆|＜doda|本求人票)',
        r'■応募要件\n-+\n(.*?)(?=■|━━)',
        r'応募資格/応募条件\t(.*?)(?=最終学歴|資格|語学)',
        r'【応募要件】\n(.*?)(?=【|■)',
        # JobSearchDetail形式（H3見出し）
        r'対象となる方\n(.*?)(?=\n勤務地|\n勤務時間|\n雇用形態)',
    ], text)

    working_hours = _find([
        r'◆就業時間[：:]\s*([^\n◆]+(?:\n[^◆\n]{1,80})*)',
        r'【勤務時間】\n([^\n【]+)',
        # JobSearchDetail形式
        r'勤務時間\n(.*?)(?=\n雇用形態|\n給与|\n待遇)',
    ], text)

    holidays = _find([
        r'◆休日休暇[：:]\s*([^\n◆]+(?:\n[^◆\n]{1,80})*)',
        r'【休日休暇】\n([0-9０-９]+\s*日[^\n【]*)',
        # JobSearchDetail形式
        r'休日・休暇\n(.*?)(?=\n会社概要|\n応募方法|\n同じ地域)',
    ], text)

    return {
        "company_name":    company_name,
        "job_title":       job_title,
        "salary":          salary,
        "location":        location,
        "employment_type": employment_type,
        "description":     description,
        "requirements":    requirements,
        "working_hours":   working_hours,
        "holidays":        holidays,
    }


async def _scroll_full_page(page: Page):
    """ページ全体をスクロールして遅延ロードコンテンツを全て描画させる"""
    scroll_height = await page.evaluate("document.body.scrollHeight")
    viewport_height = await page.evaluate("window.innerHeight")
    current = 0
    while current < scroll_height:
        current = min(current + viewport_height, scroll_height)
        await page.evaluate(f"window.scrollTo(0, {current})")
        await page.wait_for_timeout(random.randint(600, 1000))
        scroll_height = await page.evaluate("document.body.scrollHeight")
    await page.wait_for_timeout(random.randint(1000, 2000))
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)


async def _get_job_search_detail_text(page: Page) -> str:
    """
    JobSearchDetail ページのテキストを取得。
    - saiyoproject経由: 「求人詳細」タブが既選択 → そのまま取得
    - offer経由: 「Pick up!」タブがデフォルト → 「求人詳細」タブをクリックして取得
    """
    await _scroll_full_page(page)

    # 選択中タブを確認
    selected = page.locator(".jobSearchDetail-tabArea__tab__item--selected").first
    selected_text = (await selected.inner_text()).strip() if await selected.count() > 0 else ""

    if selected_text != "求人詳細":
        # 「求人詳細」タブリンクをクリック（<a>タグ → 別URLへ遷移するので load_state 待ち）
        jd_link = page.locator("a.jobSearchDetail-tabArea__tab__item", has_text="求人詳細").first
        if await jd_link.count() > 0:
            print(f"    JobSearchDetail: 「求人詳細」タブクリック（現在: {selected_text!r}）")
            await page.evaluate("document.querySelectorAll('[class*=\"Modal\"],[class*=\"modal\"],[class*=\"overlay\"],[class*=\"Overlay\"]').forEach(el => el.remove())")
            await page.wait_for_timeout(300)
            await jd_link.click(force=True)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await page.wait_for_timeout(random.randint(1000, 2000))
            await _scroll_full_page(page)

    return (await page.locator("body").inner_text()).strip()


async def scrape_job_detail(page: Page, job_url: str) -> dict:
    """求人詳細ページから情報を取得（全タブ・リンク先も含めて結合）"""
    await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(random.randint(2000, 3000))

    if "login" in page.url:
        raise RuntimeError("セッション切れ。")

    await _scroll_full_page(page)

    all_texts: list[str] = []

    # ── referredJobDetail: タブ「求人概要」「就業・企業概要」──
    tab_buttons = page.locator(".referredJobDetailTab__button")
    tab_count = await tab_buttons.count()
    if tab_count > 1:
        all_texts.append((await page.locator("body").inner_text()).strip())
        for i in range(1, tab_count):
            btn = tab_buttons.nth(i)
            tab_label = (await btn.inner_text()).strip()
            print(f"    タブクリック: {tab_label}")
            # promoteApplicationModal 等のオーバーレイをJSで除去してからクリック
            await page.evaluate("document.querySelectorAll('[class*=\"Modal\"],[class*=\"modal\"],[class*=\"overlay\"],[class*=\"Overlay\"]').forEach(el => el.remove())")
            await page.wait_for_timeout(300)
            await btn.click(force=True)
            await page.wait_for_timeout(random.randint(1500, 2500))
            await _scroll_full_page(page)
            all_texts.append((await page.locator("body").inner_text()).strip())
    else:
        all_texts.append((await page.locator("body").inner_text()).strip())

    # ── saiyoproject / offer: 「求人詳細」ボタン → JobSearchDetail ──
    jd_btn = page.locator("a.btnJob07").first
    if await jd_btn.count() > 0:
        # get_attribute("href") は相対URLを返すことがあるため evaluate で絶対URLを取得
        jd_url = await jd_btn.evaluate("el => el.href")
        if jd_url:
            print(f"    求人詳細ページへ: {jd_url[:80]}...")
            await page.goto(jd_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(random.randint(2000, 3000))
            all_texts.append(await _get_job_search_detail_text(page))

    body_text = "\n\n".join(all_texts)
    parsed = parse_job_text(body_text)

    await screenshot(page, f"detail_{job_url.split('/')[-2] or 'job'}")
    return {"url": job_url, **parsed, "raw_text": body_text}


async def upsert_jobs(db: Client, category: str, label: str, jobs: list[dict]):
    """Supabase に upsert（URLをキーに重複排除）"""
    if not jobs:
        return

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for job in jobs:
        rows.append({
            "category":        category,
            "category_label":  label,
            "url":             job.get("url", ""),
            "company_name":    job.get("company_name", ""),
            "job_title":       job.get("job_title", "") or job.get("link_text", ""),
            "salary":          job.get("salary", ""),
            "location":        job.get("location", ""),
            "employment_type": job.get("employment_type", ""),
            "industry":        job.get("industry", ""),
            "job_type":        job.get("job_type", ""),
            "description":     job.get("description", ""),
            "requirements":    job.get("requirements", ""),
            "working_hours":   job.get("working_hours", ""),
            "holidays":        job.get("holidays", ""),
            "features":        json.dumps(job.get("features", []), ensure_ascii=False),
            "raw_text":        job.get("raw_text", ""),
            "raw_data":        json.dumps({k: v for k, v in job.items() if k != "raw_text"}, ensure_ascii=False),
            "fetched_at":      now,
        })

    # URL重複を除去してからupsert
    seen = set()
    unique_rows = []
    for row in rows:
        if row["url"] not in seen:
            seen.add(row["url"])
            unique_rows.append(row)
    db.table("doda_jobs").upsert(unique_rows, on_conflict="url").execute()
    print(f"  Supabase 保存: {len(rows)} 件")


async def main():
    db = get_db()

    async with async_playwright() as p:
        print("Chrome (port 9222) に接続中...")
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]

        # 既存タブがあればそれを使い、なければ新規作成
        page = context.pages[0] if context.pages else await context.new_page()

        await ensure_logged_in(page)

        try:
            for cat_key, cat_info in CATEGORIES.items():
                print(f"\n{'='*50}")
                print(f"カテゴリ: {cat_info['label']}")
                print(f"{'='*50}")

                try:
                    job_list = await get_job_list(page, cat_key, cat_info["url"])
                    print(f"  合計 {len(job_list)} 件の求人を発見")

                    detailed_jobs = []
                    for i, job in enumerate(job_list, 1):
                        print(f"  詳細取得 {i}/{len(job_list)}: {job['url']}")
                        try:
                            detail = await scrape_job_detail(page, job["url"])
                            detail.update(job)
                            detailed_jobs.append(detail)
                            await asyncio.sleep(random.uniform(5, 12))
                        except Exception as e:
                            print(f"    詳細取得エラー: {e}")
                            detailed_jobs.append(job)

                    await upsert_jobs(db, cat_key, cat_info["label"], detailed_jobs)

                except RuntimeError as e:
                    print(f"  中断: {e}")
                    break
                except PlaywrightTimeout:
                    print(f"  タイムアウト: {cat_info['url']}")
                except Exception as e:
                    print(f"  エラー: {e}")

        finally:
            await browser.close()

    print("\n完了")


if __name__ == "__main__":
    asyncio.run(main())
