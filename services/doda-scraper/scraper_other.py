"""
リクナビNEXT / マイナビ転職 / リクルートダイレクトスカウト スクレイパー
doda_jobs テーブルに保存（doda scraper と同じテーブル）
スクレイプ後に即 Gemini で構造化まで完了する。

実行: python scraper_other.py
     python scraper_other.py --debug
     python scraper_other.py --limit 3
"""
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import google.generativeai as genai
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout
from supabase import create_client, Client

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

DEBUG = "--debug" in sys.argv
LIMIT = None
for i, arg in enumerate(sys.argv):
    if arg == "--limit" and i + 1 < len(sys.argv):
        LIMIT = int(sys.argv[i + 1])

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_AI_API_KEY"]

MAX_JOBS = LIMIT or 50  # 1カテゴリあたりの最大取得件数

CATEGORIES = {
    "rikunabi_offer": {
        "label": "リクナビNEXT オファー",
        "list_url": "https://next.rikunabi.com/offers/",
    },
    "mynavi_scout": {
        "label": "マイナビ転職 スカウト",
        "list_url": "https://tenshoku.mynavi.jp/scout/messages/",
    },
    "directscout_recommend": {
        "label": "リクルートダイレクトスカウト おすすめ",
        "list_url": "https://directscout.recruit.co.jp/recommend",
    },
}

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    "gemini-2.5-flash-lite",
    generation_config=genai.types.GenerationConfig(
        response_mime_type="application/json",
        temperature=0.0,
    ),
)

EXTRACT_PROMPT = """\
あなたは日本の求人情報の構造化エキスパートです。
以下の求人テキストを読み込み、指定のJSONスキーマで情報を抽出してください。

【ルール】
- 数値が明記されていない場合は null を返す（推測・補完禁止）
- 「〜以上」「最大〜」など範囲がある場合は下限を min、上限を max に入れる
- boolean は true/false のみ（null 禁止）
- skill_tags・benefit_tags は具体的なキーワードを配列で（例: ["SAP","在庫管理","英語"]）
- listing_status: "unlisted"(非上場) / "listed_prime"(東証プライム) / "listed_growth"(東証グロース) / "listed_other"(その他上場) / "ipo_preparing"(上場準備中) / null(不明)
- salary_system: "monthly"(月給制) / "annual"(年俸制) / null(不明)
- remote_type: "full"(フルリモート) / "partial"(一部リモート) / "none"(出社のみ) / null(不明)
- english_level: "none"(不要) / "daily"(日常会話) / "business"(ビジネス) / "native"(ネイティブ) / null(不明)
- metadata には、上記カラムに収まらない企業固有の数値や特徴を自由形式で入れる
- summary: 求人全体を2〜3文で要約した日本語テキスト。どんな会社で何をする仕事か・魅力を簡潔に。
- description: 「仕事内容」「業務内容」「職務内容」セクションの本文（全文）。見出しラベル自体は含めない
- requirements: 「必須要件」「応募資格」「必須スキル」「必須条件」セクションの本文（全文）。見出しラベル自体は含めない
- preferred_requirements: 「歓迎要件」「あれば尚可」「優遇条件」「歓迎スキル」セクションの本文（全文）。見出しラベル自体は含めない。該当セクションがない場合は null

【求人テキスト】
{raw_text}

【出力JSONスキーマ】
{{
  "company_name": <string|null>,
  "job_title": <string|null>,
  "location": <string|null>,
  "employment_type": <string|null>,
  "salary_min": <integer|null>,
  "salary_max": <integer|null>,
  "salary_system": <"monthly"|"annual"|null>,
  "base_salary_monthly": <integer|null>,
  "fixed_overtime_pay": <integer|null>,
  "fixed_overtime_hours": <integer|null>,
  "has_incentive": <boolean>,
  "annual_holidays": <integer|null>,
  "avg_overtime_hours": <integer|null>,
  "is_remote_allowed": <boolean>,
  "remote_type": <"full"|"partial"|"none"|null>,
  "is_flex_time": <boolean>,
  "probation_months": <integer|null>,
  "is_managerial": <boolean>,
  "is_inexperienced_ok": <boolean>,
  "management_exp_required": <boolean>,
  "english_level": <"none"|"daily"|"business"|"native"|null>,
  "required_exp_years": <integer|null>,
  "listing_status": <"unlisted"|"listed_prime"|"listed_growth"|"listed_other"|"ipo_preparing"|null>,
  "company_employee_count": <integer|null>,
  "company_average_age": <number|null>,
  "foreign_employee_ratio": <integer|null>,
  "skill_tags": <string[]>,
  "benefit_tags": <string[]>,
  "description": <string|null>,
  "summary": <string|null>,
  "requirements": <string|null>,
  "preferred_requirements": <string|null>,
  "metadata": {{}}
}}
"""


def get_db() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def extract_with_gemini(raw_text: str) -> dict | None:
    if not raw_text or len(raw_text.strip()) < 50:
        return None
    prompt = EXTRACT_PROMPT.format(raw_text=raw_text[:15000])
    try:
        response = gemini_model.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        print(f"    [Gemini ERROR] {e}")
        return None


async def screenshot(page: Page, name: str):
    if DEBUG:
        path = Path(__file__).parent / "screenshots" / f"{name}.png"
        path.parent.mkdir(exist_ok=True)
        try:
            await page.screenshot(path=str(path), full_page=True, timeout=30000)
            print(f"  [screenshot] {path}")
        except Exception:
            pass


async def scroll_full(page: Page):
    try:
        scroll_h = await page.evaluate("document.body.scrollHeight")
        vh = await page.evaluate("window.innerHeight")
        pos = 0
        while pos < scroll_h:
            pos = min(pos + vh, scroll_h)
            await page.evaluate(f"window.scrollTo(0, {pos})")
            await page.wait_for_timeout(random.randint(500, 900))
            scroll_h = await page.evaluate("document.body.scrollHeight")
        await page.wait_for_timeout(800)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass  # SPA遷移によるコンテキスト破棄は無視して続行


# ── URL正規化 ────────────────────────────────────────────────────

def normalize_rikunabi_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") + "/"
    return f"https://next.rikunabi.com{path}"


def normalize_mynavi_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    delivery_id = params.get("deliveryId", [""])[0]
    path = parsed.path.rstrip("/") + "/"
    if delivery_id:
        return f"https://tenshoku.mynavi.jp{path}?deliveryId={delivery_id}"
    return f"https://tenshoku.mynavi.jp{path}"


# ── リクナビNEXT ──────────────────────────────────────────────────

async def rikunabi_get_list(page: Page) -> list[dict]:
    list_url = CATEGORIES["rikunabi_offer"]["list_url"]
    await page.goto(list_url, wait_until="load", timeout=60000)
    await page.wait_for_timeout(3000)

    if "login" in page.url or "signin" in page.url:
        raise RuntimeError("リクナビNEXT: 未ログイン（Chromeでログインしてください）")

    await scroll_full(page)

    links = await page.eval_on_selector_all(
        "a[href*='/viewjob/jk']",
        "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g,' ').slice(0,80) }))"
        ".filter(l => !l.href.includes('#') && l.href.includes('/viewjob/jk'))"
    )

    seen, jobs = set(), []
    for lnk in links:
        norm = normalize_rikunabi_url(lnk["href"])
        if norm not in seen:
            seen.add(norm)
            jobs.append({"url": norm, "link_text": lnk["text"]})
        if len(jobs) >= MAX_JOBS:
            break

    print(f"  リクナビNEXT: {len(jobs)} 件発見")
    return jobs


async def rikunabi_get_detail(page: Page, url: str) -> str:
    await page.goto(url, wait_until="load", timeout=60000)
    await page.wait_for_timeout(random.randint(2000, 3000))
    await scroll_full(page)
    return (await page.locator("body").inner_text()).strip()


# ── マイナビ転職 ──────────────────────────────────────────────────

async def mynavi_get_list(page: Page) -> list[dict]:
    base_url = CATEGORIES["mynavi_scout"]["list_url"]
    seen, jobs = set(), []
    page_num = 1

    while True:
        list_url = base_url if page_num == 1 else f"{base_url}?filter=0&pageNo={page_num}&check=0&order=2"
        await page.goto(list_url, wait_until="load", timeout=60000)
        await page.wait_for_timeout(3000)

        if "login" in page.url or "signin" in page.url:
            raise RuntimeError("マイナビ: 未ログイン（Chromeでログインしてください）")

        await scroll_full(page)

        links = await page.eval_on_selector_all(
            "a[href*='/jobinfo-']",
            "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g,' ').slice(0,80) }))"
            ".filter(l => l.href.includes('ty=sm') || l.href.includes('msgKbn=7'))"
        )

        added = 0
        for lnk in links:
            norm = normalize_mynavi_url(lnk["href"])
            if norm not in seen:
                seen.add(norm)
                jobs.append({"url": norm, "original_url": lnk["href"], "link_text": lnk["text"]})
                added += 1
            if len(jobs) >= MAX_JOBS:
                break

        print(f"  マイナビ p{page_num}: {added} 件追加（累計 {len(jobs)} 件）")

        if len(jobs) >= MAX_JOBS or added == 0:
            break

        next_btn = page.locator("a[href*='pageNo=']", has_text="次へ")
        if await next_btn.count() == 0:
            break
        page_num += 1

    return jobs


async def mynavi_get_detail(page: Page, job: dict) -> str:
    url = job.get("original_url") or job["url"]
    await page.goto(url, wait_until="load", timeout=60000)
    await page.wait_for_timeout(random.randint(2000, 3000))
    await scroll_full(page)
    return (await page.locator("body").inner_text()).strip()


# ── リクルートダイレクトスカウト ────────────────────────────────────

async def directscout_get_list(page: Page) -> list[dict]:
    list_url = CATEGORIES["directscout_recommend"]["list_url"]
    await page.goto(list_url, wait_until="load", timeout=60000)
    await page.wait_for_timeout(3000)

    if "login" in page.url or "signin" in page.url:
        raise RuntimeError("ダイレクトスカウト: 未ログイン（Chromeでログインしてください）")

    await scroll_full(page)

    links = await page.eval_on_selector_all(
        "a[href*='/job_descriptions/']",
        "els => els.map(el => ({ href: el.href, text: el.innerText.trim().replace(/\\s+/g,' ').slice(0,80) }))"
        ".filter(l => !l.href.endsWith('#'))"
    )

    seen, jobs = set(), []
    for lnk in links:
        href = lnk["href"].split("?")[0]
        if href not in seen and "/job_descriptions/" in href:
            seen.add(href)
            jobs.append({"url": href, "link_text": lnk["text"]})
        if len(jobs) >= MAX_JOBS:
            break

    print(f"  ダイレクトスカウト: {len(jobs)} 件発見")
    return jobs


async def directscout_get_detail(page: Page, url: str) -> str:
    await page.goto(url, wait_until="load", timeout=60000)
    await page.wait_for_timeout(random.randint(2000, 3000))
    await scroll_full(page)

    texts = [(await page.locator("body").inner_text()).strip()]

    tab2 = page.locator("label.Tab_tab__O7kX2", has_text="選考・企業概要")
    if await tab2.count() > 0:
        print("    [DS] 「選考・企業概要」タブをクリック")
        await tab2.click(force=True)
        await page.wait_for_timeout(random.randint(1500, 2500))
        await scroll_full(page)
        texts.append((await page.locator("body").inner_text()).strip())

    return "\n\n".join(texts)


# ── Supabase保存 ──────────────────────────────────────────────────

def upsert_jobs(db: Client, category: str, label: str, jobs: list[dict]):
    if not jobs:
        return

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    seen = set()
    for job in jobs:
        url = job.get("url", "")
        if url in seen:
            continue
        seen.add(url)

        ex = job.get("_extracted") or {}

        row = {
            "category":                category,
            "category_label":          label,
            "url":                     url,
            "company_name":            ex.get("company_name") or job.get("company_name", ""),
            "job_title":               ex.get("job_title") or job.get("job_title", "") or job.get("link_text", ""),
            "salary":                  job.get("salary", ""),
            "location":                ex.get("location") or job.get("location", ""),
            "employment_type":         ex.get("employment_type") or job.get("employment_type", ""),
            "industry":                "",
            "job_type":                "",
            "summary":                 ex.get("summary"),
            "description":             ex.get("description") or job.get("description", ""),
            "requirements":            ex.get("requirements") or job.get("requirements", ""),
            "preferred_requirements":  ex.get("preferred_requirements"),
            "working_hours":           job.get("working_hours", ""),
            "holidays":                job.get("holidays", ""),
            "features":                "[]",
            "raw_text":                job.get("raw_text", ""),
            "raw_data":                json.dumps({k: v for k, v in job.items() if k not in ("raw_text", "_extracted")}, ensure_ascii=False),
            "fetched_at":              now,
            # 構造化フィールド
            "salary_min":              ex.get("salary_min"),
            "salary_max":              ex.get("salary_max"),
            "salary_system":           ex.get("salary_system"),
            "base_salary_monthly":     ex.get("base_salary_monthly"),
            "fixed_overtime_pay":      ex.get("fixed_overtime_pay"),
            "fixed_overtime_hours":    ex.get("fixed_overtime_hours"),
            "has_incentive":           ex.get("has_incentive", False),
            "annual_holidays":         ex.get("annual_holidays"),
            "avg_overtime_hours":      ex.get("avg_overtime_hours"),
            "is_remote_allowed":       ex.get("is_remote_allowed", False),
            "remote_type":             ex.get("remote_type"),
            "is_flex_time":            ex.get("is_flex_time", False),
            "probation_months":        ex.get("probation_months"),
            "is_managerial":           ex.get("is_managerial", False),
            "is_inexperienced_ok":     ex.get("is_inexperienced_ok", False),
            "management_exp_required": ex.get("management_exp_required", False),
            "english_level":           ex.get("english_level"),
            "required_exp_years":      ex.get("required_exp_years"),
            "listing_status":          ex.get("listing_status"),
            "company_employee_count":  ex.get("company_employee_count"),
            "company_average_age":     ex.get("company_average_age"),
            "foreign_employee_ratio":  ex.get("foreign_employee_ratio"),
            "skill_tags":              ex.get("skill_tags") or [],
            "benefit_tags":            ex.get("benefit_tags") or [],
            "metadata":                json.dumps(ex.get("metadata") or {}, ensure_ascii=False),
            "structured_at":           now if ex else None,
        }
        # null値除去（boolean/配列/必須フィールドは残す）
        keep_nulls = {
            "has_incentive", "is_remote_allowed", "is_flex_time",
            "is_managerial", "is_inexperienced_ok", "management_exp_required",
            "skill_tags", "benefit_tags", "metadata", "structured_at",
        }
        row = {k: v for k, v in row.items() if v is not None or k in keep_nulls}
        rows.append(row)

    db.table("doda_jobs").upsert(rows, on_conflict="url").execute()
    print(f"  Supabase 保存: {len(rows)} 件")


# ── メイン ────────────────────────────────────────────────────────

async def process_category(page: Page, db: Client, cat_key: str, cat_info: dict):
    print(f"\n{'='*50}")
    print(f"カテゴリ: {cat_info['label']}")
    print(f"{'='*50}")

    if cat_key == "rikunabi_offer":
        job_list = await rikunabi_get_list(page)
    elif cat_key == "mynavi_scout":
        job_list = await mynavi_get_list(page)
    else:
        job_list = await directscout_get_list(page)

    print(f"  合計 {len(job_list)} 件")

    detailed = []
    for i, job in enumerate(job_list, 1):
        print(f"  詳細取得 {i}/{len(job_list)}: {job['url'][:70]}")
        try:
            if cat_key == "rikunabi_offer":
                raw_text = await rikunabi_get_detail(page, job["url"])
            elif cat_key == "mynavi_scout":
                raw_text = await mynavi_get_detail(page, job)
            else:
                raw_text = await directscout_get_detail(page, job["url"])

            job["raw_text"] = raw_text

            # Gemini で構造化
            print(f"    Gemini 抽出中...")
            extracted = extract_with_gemini(raw_text)
            if extracted:
                job["_extracted"] = extracted
                print(f"    → {extracted.get('company_name')} / 年収{extracted.get('salary_min')}〜{extracted.get('salary_max')} / 休日{extracted.get('annual_holidays')}日")
            else:
                print(f"    → Gemini 抽出失敗（raw_text のみ保存）")

            detailed.append(job)
            await screenshot(page, f"{cat_key}_{i}")
            await asyncio.sleep(random.uniform(4, 8))

        except Exception as e:
            print(f"    エラー: {e}")
            detailed.append(job)

    upsert_jobs(db, cat_key, cat_info["label"], detailed)


async def main():
    db = get_db()

    async with async_playwright() as p:
        print("Chrome (port 9222) に接続中...")
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        for cat_key, cat_info in CATEGORIES.items():
            try:
                await process_category(page, db, cat_key, cat_info)
            except RuntimeError as e:
                print(f"  中断: {e}")
                break
            except PlaywrightTimeout:
                print(f"  タイムアウト: {cat_key}")
            except Exception as e:
                print(f"  エラー: {e}")

        await browser.close()

    print("\n完了")


if __name__ == "__main__":
    asyncio.run(main())
