"""
doda 求人分析ダッシュボード
実行: python app.py
"""
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from supabase import create_client, Client

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]

_db: Client | None = None
_enrich_running = False
_scrape_running = False


def get_db() -> Client:
    global _db
    if _db is None:
        _db = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _db


# ── API ──────────────────────────────────────────────────────────

@app.route("/api/jobs")
def api_jobs():
    """フィルタリング済み求人一覧を返す"""
    db = get_db()

    salary_min   = request.args.get("salary_min",   type=int)
    holidays_min = request.args.get("holidays_min", type=int)
    overtime_max = request.args.get("overtime_max", type=int)
    remote           = request.args.get("remote")           # "true" / ""
    inexperienced_ok = request.args.get("inexperienced_ok") # "true" / ""
    listing          = request.args.get("listing")          # "ipo_preparing" など
    english          = request.args.get("english")          # "business" など
    skill            = request.args.get("skill")            # タグ文字列

    query = db.table("doda_jobs").select(
        "id, company_name, job_title, category, category_label, "
        "salary, salary_min, salary_max, salary_average, real_base_annual, "
        "base_salary_monthly, salary_system, has_incentive, "
        "fixed_overtime_hours, fixed_overtime_pay, "
        "annual_holidays, avg_overtime_hours, "
        "is_remote_allowed, remote_type, is_flex_time, "
        "is_managerial, is_inexperienced_ok, english_level, "
        "listing_status, industry, location, "
        "company_employee_count, company_average_age, foreign_employee_ratio, "
        "skill_tags, benefit_tags, metadata, "
        "summary, description, requirements, preferred_requirements, url, structured_at"
    ).neq("category", "partner_agent")

    if salary_min:
        query = query.gte("real_base_annual", salary_min)
    if holidays_min:
        query = query.gte("annual_holidays", holidays_min)
    if overtime_max:
        query = query.lte("avg_overtime_hours", overtime_max)
    if remote == "true":
        query = query.eq("is_remote_allowed", True)
    if inexperienced_ok == "true":
        query = query.eq("is_inexperienced_ok", True)
    if listing:
        query = query.eq("listing_status", listing)
    if english:
        query = query.eq("english_level", english)
    if skill:
        query = query.contains("skill_tags", [skill])

    result = query.order("real_base_annual", desc=True).execute()
    jobs = result.data or []

    for j in jobs:
        if isinstance(j.get("metadata"), str):
            try:
                j["metadata"] = json.loads(j["metadata"])
            except Exception:
                j["metadata"] = {}
        j.pop("raw_text", None)

    return jsonify(jobs)


@app.route("/api/jobs/<job_id>")
def api_job_detail(job_id: str):
    """求人詳細"""
    db = get_db()
    result = db.table("doda_jobs").select("*").eq("id", job_id).single().execute()
    job = result.data or {}
    for field in ("metadata", "raw_data"):
        if isinstance(job.get(field), str):
            try:
                job[field] = json.loads(job[field])
            except Exception:
                job[field] = {}
    return jsonify(job)


@app.route("/api/chart/bubble")
def api_bubble():
    """バブルチャート用データ（実質年収 vs 残業時間 vs 休日数）"""
    db = get_db()
    result = db.table("doda_jobs").select(
        "id, company_name, real_base_annual, salary_average, "
        "avg_overtime_hours, annual_holidays, listing_status, "
        "fixed_overtime_hours, is_remote_allowed"
    ).neq("category", "partner_agent").not_.is_("real_base_annual", "null").execute()

    points = []
    for j in result.data or []:
        overtime = j.get("avg_overtime_hours") or 0
        holidays = j.get("annual_holidays") or 0
        points.append({
            "id":           j["id"],
            "label":        j.get("company_name", ""),
            "x":            160 + overtime,          # 月間拘束時間
            "y":            (j["real_base_annual"] or 0) // 10000,  # 万円
            "r":            max(8, (holidays - 100) * 1.2),
            "listing":      j.get("listing_status"),
            "remote":       j.get("is_remote_allowed"),
            "fixed_ot":     j.get("fixed_overtime_hours") or 0,
        })
    return jsonify(points)


@app.route("/api/jobs", methods=["DELETE"])
def api_delete_jobs():
    """求人を削除"""
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "no ids"}), 400
    db = get_db()
    db.table("doda_jobs").delete().in_("id", ids).execute()
    return jsonify({"deleted": len(ids)})


_ENRICH_LOG = Path(__file__).parent / "enrich.log"


@app.route("/api/enrich/trigger", methods=["POST"])
def api_enrich_trigger():
    """enrich_jobs.py をバックグラウンドで実行"""
    global _enrich_running
    if _enrich_running:
        return jsonify({"status": "already_running"})
    enrich_script = Path(__file__).parent.parent / "doda-scraper" / "enrich_jobs.py"
    if not enrich_script.exists():
        return jsonify({"status": "error", "message": "enrich_jobs.py が見つかりません"}), 404

    data = request.get_json() or {}
    rerun = data.get("rerun", False)
    cmd = ["python", str(enrich_script)]
    if rerun:
        cmd.append("--rerun")

    def run():
        global _enrich_running
        try:
            with open(_ENRICH_LOG, "w", encoding="utf-8") as f:
                subprocess.run(
                    cmd, stdout=f, stderr=subprocess.STDOUT,
                    timeout=1800,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "NODE_OPTIONS": "--no-warnings"},
                )
        finally:
            _enrich_running = False

    _enrich_running = True
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started", "rerun": rerun})


@app.route("/api/enrich/status")
def api_enrich_status():
    return jsonify({"running": _enrich_running})


@app.route("/api/enrich/log")
def api_enrich_log():
    try:
        if not _ENRICH_LOG.exists():
            return jsonify({"lines": []})
        lines = _ENRICH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return jsonify({"lines": lines[-40:]})
    except Exception as e:
        return jsonify({"lines": [str(e)]})


_SCRAPE_LOG = Path(__file__).parent / "scrape.log"


def _chrome_running() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 9222), timeout=1)
        s.close()
        return True
    except OSError:
        return False


@app.route("/api/scrape/trigger", methods=["POST"])
def api_scrape_trigger():
    """scraper_other.py をバックグラウンドで実行"""
    global _scrape_running
    if _scrape_running:
        return jsonify({"status": "already_running"})
    if not _chrome_running():
        return jsonify({"status": "error", "message": "Chrome が起動していません。launch_chrome.bat を実行してください。"}), 503
    scraper_script = Path(__file__).parent.parent / "doda-scraper" / "scraper_other.py"
    if not scraper_script.exists():
        return jsonify({"status": "error", "message": "scraper_other.py が見つかりません"}), 404

    def run():
        global _scrape_running
        try:
            with open(_SCRAPE_LOG, "w", encoding="utf-8") as f:
                subprocess.run(
                    ["python", str(scraper_script)],
                    stdout=f, stderr=subprocess.STDOUT,
                    timeout=600,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "NODE_OPTIONS": "--no-warnings"},
                )
        finally:
            _scrape_running = False

    _scrape_running = True
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/scrape/status")
def api_scrape_status():
    return jsonify({"running": _scrape_running})


@app.route("/api/scrape/log")
def api_scrape_log():
    try:
        if not _SCRAPE_LOG.exists():
            return jsonify({"lines": []})
        lines = _SCRAPE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return jsonify({"lines": lines[-40:]})
    except Exception as e:
        return jsonify({"lines": [str(e)]})


@app.route("/api/skills")
def api_skills():
    """スキルタグ一覧（フィルター用）"""
    db = get_db()
    result = db.table("doda_jobs").select("skill_tags").neq("category", "partner_agent").execute()
    tags = set()
    for j in result.data or []:
        for t in (j.get("skill_tags") or []):
            tags.add(t)
    return jsonify(sorted(tags))


# ── Pages ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/compare")
def compare():
    ids = request.args.getlist("id")
    return render_template("compare.html", ids=ids)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
