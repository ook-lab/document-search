"""
doda_jobs の raw_text を Gemini で構造化して DB を更新する ETL スクリプト

【実行方法】
  python enrich_jobs.py            # 未処理レコードをすべて処理
  python enrich_jobs.py --rerun    # structured_at 済みも含めて全件再処理
  python enrich_jobs.py --limit 5  # 最大5件のみ処理（動作確認用）
"""

import io
import json
import os
import sys
import time

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from datetime import datetime, timezone
from pathlib import Path

import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
RERUN  = "--rerun" in sys.argv
LIMIT  = None
for i, arg in enumerate(sys.argv):
    if arg == "--limit" and i + 1 < len(sys.argv):
        LIMIT = int(sys.argv[i + 1])

vertexai.init(location="asia-northeast1")
model = GenerativeModel(
    "gemini-2.5-flash-lite",
    generation_config=GenerationConfig(
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
- boolean は True/False のみ（null 禁止）
- skill_tags・benefit_tags は具体的なキーワードを配列で（例: ["SAP","在庫管理","英語"]）
- listing_status: "unlisted"(非上場) / "listed_prime"(東証プライム) / "listed_growth"(東証グロース) / "listed_other"(その他上場) / "ipo_preparing"(上場準備中) / null(不明)
- salary_system: "monthly"(月給制) / "annual"(年俸制) / null(不明)
- remote_type: "full"(フルリモート) / "partial"(一部リモート) / "none"(出社のみ) / null(不明)
- english_level: "none"(不要) / "daily"(日常会話) / "business"(ビジネス) / "native"(ネイティブ) / null(不明)
- metadata には、上記カラムに収まらない企業固有の数値や特徴を自由形式で入れる
- industry: 求人の業界を以下から1つ選択: "金融・会計" / "IT・通信" / "製造・メーカー" / "商社・物流" / "人材・教育" / "広告・マスコミ" / "不動産・建設" / "コンサル・経営" / "医療・福祉" / "その他" / null(不明)
- summary: 求人全体を2〜3文で要約した日本語テキスト。どんな会社で何をする仕事か・魅力を簡潔に。
- description: 仕事内容・業務内容セクションの本文（全文）。見出しラベルは含めず本文のみ。
- requirements: 「必須要件」「応募資格」「必須スキル」「必須条件」セクションの本文（全文）。見出しラベルは含めず本文のみ。
- preferred_requirements: 「歓迎要件」「あれば尚可」「優遇条件」「歓迎スキル」セクションの本文（全文）。見出しラベルは含めず本文のみ。該当セクションがない場合は null。

【求人テキスト】
{raw_text}

【出力JSONスキーマ】
{{
  "company_name": <string|null>,
  "job_title": <string|null>,
  "location": <string|null>,
  "employment_type": <string|null>,
  "industry": <"金融・会計"|"IT・通信"|"製造・メーカー"|"商社・物流"|"人材・教育"|"広告・マスコミ"|"不動産・建設"|"コンサル・経営"|"医療・福祉"|"その他"|null>,
  "summary": <string|null>,
  "description": <string|null>,
  "requirements": <string|null>,
  "preferred_requirements": <string|null>,
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
  "metadata": {{}}
}}
"""


def get_db() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_unprocessed(db: Client) -> list[dict]:
    # partner_agent（mapsScoutDetail）はスカウトメッセージのみで求人詳細なし → ETL対象外
    query = db.table("doda_jobs").select("id, url, raw_text, company_name, category").neq("category", "partner_agent")
    if not RERUN:
        query = query.is_("structured_at", "null")
    if LIMIT:
        query = query.limit(LIMIT)
    result = query.execute()
    return result.data or []


def extract_with_gemini(raw_text: str) -> dict | None:
    if not raw_text or len(raw_text.strip()) < 50:
        print("    [SKIP] raw_text が短すぎる")
        return None

    prompt = EXTRACT_PROMPT.format(raw_text=raw_text[:15000])  # 長すぎるテキストは先頭15000文字
    try:
        response = model.generate_content(prompt)
        data = json.loads(response.text)
        return data
    except Exception as e:
        print(f"    [Gemini ERROR] {e}")
        return None


def upsert_company(db: Client, company_name: str, extracted: dict) -> str | None:
    """企業を doda_companies に upsert して ID を返す"""
    if not company_name:
        return None
    try:
        company_data = {
            "name": company_name,
            "listing_status": extracted.get("listing_status"),
            "employee_count": extracted.get("company_employee_count"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # metadata に企業固有情報を格納
        company_meta = {}
        if extracted.get("company_average_age") is not None:
            company_meta["average_age"] = extracted["company_average_age"]
        if extracted.get("foreign_employee_ratio") is not None:
            company_meta["foreign_employee_ratio"] = extracted["foreign_employee_ratio"]
        if company_meta:
            company_data["metadata"] = json.dumps(company_meta, ensure_ascii=False)

        result = db.table("doda_companies").upsert(
            company_data, on_conflict="name"
        ).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception as e:
        print(f"    [Company upsert ERROR] {e}")
    return None


def update_job(db: Client, job_id: str, extracted: dict, company_id: str | None):
    """doda_jobs の構造化フィールドを更新する"""
    now = datetime.now(timezone.utc).isoformat()

    update_data = {
        "company_name":            extracted.get("company_name"),
        "job_title":               extracted.get("job_title"),
        "location":                extracted.get("location"),
        "employment_type":         extracted.get("employment_type"),
        "industry":                extracted.get("industry"),
        "summary":                 extracted.get("summary"),
        "description":             extracted.get("description"),
        "requirements":            extracted.get("requirements"),
        "preferred_requirements":  extracted.get("preferred_requirements"),
        "salary_min":              extracted.get("salary_min"),
        "salary_max":              extracted.get("salary_max"),
        "salary_system":           extracted.get("salary_system"),
        "base_salary_monthly":     extracted.get("base_salary_monthly"),
        "fixed_overtime_pay":      extracted.get("fixed_overtime_pay"),
        "fixed_overtime_hours":    extracted.get("fixed_overtime_hours"),
        "has_incentive":           extracted.get("has_incentive", False),
        "annual_holidays":         extracted.get("annual_holidays"),
        "avg_overtime_hours":      extracted.get("avg_overtime_hours"),
        "is_remote_allowed":       extracted.get("is_remote_allowed", False),
        "remote_type":             extracted.get("remote_type"),
        "is_flex_time":            extracted.get("is_flex_time", False),
        "probation_months":        extracted.get("probation_months"),
        "is_managerial":           extracted.get("is_managerial", False),
        "is_inexperienced_ok":     extracted.get("is_inexperienced_ok", False),
        "management_exp_required": extracted.get("management_exp_required", False),
        "english_level":           extracted.get("english_level"),
        "required_exp_years":      extracted.get("required_exp_years"),
        "listing_status":          extracted.get("listing_status"),
        "company_employee_count":  extracted.get("company_employee_count"),
        "company_average_age":     extracted.get("company_average_age"),
        "foreign_employee_ratio":  extracted.get("foreign_employee_ratio"),
        "skill_tags":              extracted.get("skill_tags") or [],
        "benefit_tags":            extracted.get("benefit_tags") or [],
        "metadata":                json.dumps(extracted.get("metadata") or {}, ensure_ascii=False),
        "structured_at":           now,
    }
    if company_id:
        update_data["company_id"] = company_id

    # salary_min > salary_max の場合は salary_max を無効化
    if update_data.get("salary_min") and update_data.get("salary_max"):
        if update_data["salary_min"] > update_data["salary_max"]:
            update_data["salary_max"] = None

    # null値のキーを除去（更新しない）
    update_data = {k: v for k, v in update_data.items() if v is not None or k in (
        "has_incentive", "is_remote_allowed", "is_flex_time",
        "is_managerial", "is_inexperienced_ok", "management_exp_required",
        "skill_tags", "benefit_tags", "metadata", "structured_at",
    )}

    db.table("doda_jobs").update(update_data).eq("id", job_id).execute()


def main():
    db = get_db()

    jobs = fetch_unprocessed(db)
    print(f"対象: {len(jobs)} 件")
    if not jobs:
        print("処理対象なし")
        return

    for i, job in enumerate(jobs, 1):
        job_id       = job["id"]
        url          = job.get("url", "")
        company_name = job.get("company_name", "")
        raw_text     = job.get("raw_text", "")
        category     = job.get("category", "")

        print(f"\n[{i}/{len(jobs)}] {company_name or url[:60]} ({category})")

        extracted = extract_with_gemini(raw_text)
        if extracted is None:
            continue

        # 結果サマリーを表示
        print(f"    業界: {extracted.get('industry')}  職種: {extracted.get('job_title', '')[:40]}")
        print(f"    年収: {extracted.get('salary_min')}〜{extracted.get('salary_max')}")
        print(f"    年間休日: {extracted.get('annual_holidays')} 残業: {extracted.get('avg_overtime_hours')}h")
        print(f"    スキルタグ: {extracted.get('skill_tags')}")
        print(f"    リモート: {extracted.get('remote_type')} 固定残業: {extracted.get('fixed_overtime_hours')}h")

        company_id = upsert_company(db, company_name, extracted)
        update_job(db, job_id, extracted, company_id)

        print(f"    → 保存完了")

        # Gemini API レート制限対策（無料枠: 15 req/min）
        time.sleep(4)

    print(f"\n完了: {len(jobs)} 件処理")


if __name__ == "__main__":
    main()
