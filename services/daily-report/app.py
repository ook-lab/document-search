"""
日次レポート Flask アプリ

Routes:
  GET  /                         → /report/latest へリダイレクト
  GET  /report/latest            → 最新レポートへリダイレクト
  GET  /report/<date>            → レポート閲覧画面（YYYY-MM-DD）
  GET  /api/report/<date>        → レポート JSON
  GET  /api/reports              → レポート一覧
  POST /api/generate             → レポート生成（body: {"base_date":"YYYY-MM-DD"}）
"""
import os
import sys
from pathlib import Path
from datetime import datetime, date, timezone, timedelta

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

JST = timezone(timedelta(hours=9))

_db  = None
_llm = None


def _get_clients():
    global _db, _llm
    if _db is None:
        from shared.common.database.client import DatabaseClient
        from shared.ai.llm_client.llm_client import LLMClient
        _db  = DatabaseClient(use_service_role=True)
        _llm = LLMClient()
    return _db, _llm


# ─────────────────────────────────────────────────────────────
# 画面 Routes
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("report_latest"))


@app.route("/report/latest")
def report_latest():
    """最新レポートへリダイレクト（なければ今日）"""
    try:
        db, _ = _get_clients()
        result = (
            db.client.table("11_daily_reports")
            .select("base_date")
            .order("base_date", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return redirect(url_for("report_view", target_date=result.data[0]["base_date"]))
    except Exception:
        pass
    today = datetime.now(JST).date().isoformat()
    return redirect(url_for("report_view", target_date=today))


@app.route("/report/<target_date>")
def report_view(target_date: str):
    return render_template("index.html", target_date=target_date)


# ─────────────────────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────────────────────

@app.route("/api/report/<target_date>")
def api_report(target_date: str):
    try:
        db, _ = _get_clients()
        result = (
            db.client.table("11_daily_reports")
            .select("base_date,generated_at,report_json")
            .eq("base_date", target_date)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            return jsonify({
                "success":      True,
                "base_date":    row["base_date"],
                "generated_at": row["generated_at"],
                "report":       row["report_json"],
            })
        return jsonify({"success": False, "error": "レポートが見つかりません"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/reports")
def api_reports_list():
    try:
        db, _ = _get_clients()
        result = (
            db.client.table("11_daily_reports")
            .select("id,base_date,generated_at")
            .order("base_date", desc=True)
            .limit(30)
            .execute()
        )
        return jsonify({"success": True, "reports": result.data or []})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """レポート生成（同期）"""
    data = request.get_json() or {}
    base_date_str = data.get("base_date")
    try:
        base_date = (
            date.fromisoformat(base_date_str)
            if base_date_str
            else datetime.now(JST).date()
        )
        db, llm = _get_clients()
        from report_generator import ReportGenerator
        gen = ReportGenerator(db, llm)
        report = gen.generate(base_date)
        report_id = gen.save(report)
        return jsonify({
            "success":   True,
            "id":        report_id,
            "base_date": base_date.isoformat(),
        })
    except Exception as e:
        import traceback
        return jsonify({
            "success":   False,
            "error":     str(e),
            "traceback": traceback.format_exc(),
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
