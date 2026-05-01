"""Gmail service: single unified app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
from flask_wtf.csrf import CSRFProtect, generate_csrf
from jinja2 import FileSystemLoader
from loguru import logger

_here = Path(__file__).resolve().parent
_repo = _here.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

load_dotenv(_repo / ".env")
load_dotenv(_here / ".env")

from shared.common.database.client import DatabaseClient
from processing import GmailService


def _db() -> DatabaseClient:
    if not hasattr(_db, "_i"):
        _db._i = DatabaseClient(use_service_role=True)
    return _db._i


def _svc() -> GmailService:
    if not hasattr(_svc, "_i"):
        _svc._i = GmailService()
    return _svc._i


def create_app() -> Flask:
    a = Flask(__name__, static_folder="static", static_url_path="/static")
    a.jinja_loader = FileSystemLoader(str(_here / "templates"))
    a.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32).hex()
    a.config["WTF_CSRF_TIME_LIMIT"] = None
    a.config["WTF_CSRF_CHECK_DEFAULT"] = False
    CSRFProtect(a)

    @a.context_processor
    def _csrf():
        return {"csrf_token": generate_csrf}

    # === Page ==========================================================
    @a.route("/")
    def home():
        return render_template("home.html")

    # === Email list ====================================================
    @a.route("/api/emails")
    def api_emails():
        db = _db()
        q = db.client.table("01_gmail_01_raw").select(
            "id, message_id, thread_id, header_subject, from_name, from_email,"
            "header_to, sent_at, snippet, category, source, attachments, ingested_at"
        )
        kw = request.args.get("q", "").strip()
        if kw:
            q = q.or_(
                f"header_subject.ilike.%{kw}%,"
                f"from_name.ilike.%{kw}%,"
                f"from_email.ilike.%{kw}%,"
                f"snippet.ilike.%{kw}%"
            )
        cat = request.args.get("category", "").strip()
        if cat:
            q = q.eq("category", cat)
        q = q.order("sent_at", desc=True).limit(
            request.args.get("limit", 200, type=int))
        rows = (q.execute()).data or []

        if rows:
            ids = [r["id"] for r in rows]
            pm = (db.client.table("pipeline_meta")
                  .select("raw_id, processing_status")
                  .eq("raw_table", "01_gmail_01_raw")
                  .in_("raw_id", ids).execute())
            sm = {p["raw_id"]: p["processing_status"] for p in (pm.data or [])}
            for r in rows:
                r["processing_status"] = sm.get(r["id"], "none")
                att = r.get("attachments") or []
                r["image_count"] = sum(
                    1 for a in att if (a.get("mime_type") or "").startswith("image/"))
        return jsonify(rows)

    # === Categories ====================================================
    @a.route("/api/categories")
    def api_categories():
        db = _db()
        resp = (db.client.table("01_gmail_01_raw")
                .select("category")
                .not_.is_("category", "null").execute())
        return jsonify(sorted(set(
            r["category"] for r in (resp.data or []) if r.get("category"))))

    # === Gmail labels ===================================================
    @a.route("/api/gmail/labels")
    def api_labels():
        try:
            from shared.common.connectors.gmail_connector import GmailConnector
            ue = os.getenv("GMAIL_DM_USER_EMAIL") or os.getenv("GMAIL_USER_EMAIL")
            if not ue:
                return jsonify({"error": "GMAIL_USER_EMAIL not set"}), 500
            labs = GmailConnector(user_email=ue).list_labels()
            return jsonify(sorted(
                [{"id": l["id"], "name": l["name"]}
                 for l in labs if l.get("type") == "user"],
                key=lambda x: x["name"]))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # === Fetch ==========================================================
    @a.route("/api/fetch", methods=["POST"])
    def api_fetch():
        body = request.get_json(silent=True) or {}
        try:
            result = _svc().fetch(
                mail_type=body.get("mail_type", "DM"),
                query=body.get("query"),
                max_results=body.get("max_results", 50),
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # === Process (OCR + MD + chunk + embed) =============================
    @a.route("/api/process", methods=["POST"])
    def api_process():
        body = request.get_json(silent=True) or {}
        ids = body.get("ids", [])
        if not ids:
            return jsonify({"error": "ids required"}), 400
        return jsonify({"results": _svc().process(ids)})

    # === Analyze (Gemini expiry check) ==================================
    @a.route("/api/analyze", methods=["POST"])
    def api_analyze():
        body = request.get_json(silent=True) or {}
        ids = body.get("ids", [])
        if not ids:
            return jsonify({"error": "ids required"}), 400
        try:
            return jsonify({"results": _svc().analyze(ids)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # === Delete =========================================================
    @a.route("/api/emails", methods=["DELETE"])
    def api_delete():
        body = request.get_json(silent=True) or {}
        ids = body.get("ids", [])
        if not ids:
            return jsonify({"error": "ids required"}), 400
        return jsonify(_svc().delete(ids))

    logger.info("gmail-service ready (unified)")
    return a


app = create_app()
