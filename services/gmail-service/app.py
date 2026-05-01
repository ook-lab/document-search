"""Gmail service: cleaner + Gmail ingest UI."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template
from flask_wtf.csrf import CSRFProtect, generate_csrf
from jinja2 import FileSystemLoader
from loguru import logger

_here = Path(__file__).resolve().parent
_repo_root = _here.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

load_dotenv(_repo_root / ".env")
load_dotenv(_here / ".env")


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.jinja_loader = FileSystemLoader(str(_here / "templates"))
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32).hex()
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    CSRFProtect(app)

    from blueprints.auth_min import auth_min_bp
    from blueprints.gmail_cleaner import gmail_cleaner_bp

    app.register_blueprint(auth_min_bp, url_prefix="/api")
    app.register_blueprint(gmail_cleaner_bp)

    _register_gmail_ingestion(app)

    @app.route("/")
    def home():
        return render_template("home.html")

    @app.context_processor
    def inject_csrf():
        return {"csrf_token": generate_csrf}

    logger.info("gmail-service ready (standalone templates + static)")
    return app


def _register_gmail_ingestion(app: Flask) -> None:
    """Gmail取込 UI + runner。"""
    if getattr(app, "_gmail_ingestion_routes", False):
        return
    from flask import Response, jsonify, request, stream_with_context

    import runner

    _root = Path(__file__).resolve().parents[2]
    sources = {
        "gmail": {
            "name": "Gmail取込",
            "script": "services/gmail-service/gmail/gmail_ingestion.py",
            "group": "gmail",
            "mail_types": ["DM", "JOB"],
        },
    }

    @app.route("/ingest")
    def ingest_index():
        return render_template("ingest_index.html")

    @app.route("/api/sources")
    def api_sources():
        return jsonify(sources)

    @app.route("/api/gmail/labels")
    def api_gmail_labels():
        try:
            from shared.common.connectors.gmail_connector import GmailConnector

            user_email = os.getenv("GMAIL_DM_USER_EMAIL") or os.getenv("GMAIL_USER_EMAIL")
            if not user_email:
                return jsonify({"error": "GMAIL_USER_EMAIL is not set"}), 500
            gmail = GmailConnector(user_email=user_email)
            all_labels = gmail.list_labels()
            labels = [
                {"id": lb["id"], "name": lb["name"]}
                for lb in all_labels
                if lb.get("type") == "user"
            ]
            labels.sort(key=lambda x: x["name"])
            return jsonify(labels)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/run/<src>", methods=["POST"])
    def api_run(src: str):
        if src not in sources:
            return jsonify({"error": f"Unknown source: {src}"}), 404
        meta = sources[src]
        script_path = str(_root / meta["script"])
        body = request.get_json(silent=True) or {}
        if "extra_args" in body:
            extra_args = body["extra_args"]
            if isinstance(extra_args, str):
                extra_args = extra_args.split()
        else:
            st = runner.get_settings(src)
            extra_args = st.get("extra_args", [])
            if isinstance(extra_args, str):
                extra_args = extra_args.split()
        run_id = runner.start_run(src, script_path, extra_args)
        return jsonify({"run_id": run_id, "source": src})

    @app.route("/api/stream/<run_id>")
    def api_stream(run_id: str):
        def generate():
            yield from runner.stream_log(run_id)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/api/history")
    def api_history():
        limit = request.args.get("limit", 50, type=int)
        return jsonify(runner.get_history(limit))

    @app.route("/api/settings/<src>", methods=["GET"])
    def api_get_settings(src: str):
        if src not in sources:
            return jsonify({"error": f"Unknown source: {src}"}), 404
        return jsonify(runner.get_settings(src))

    @app.route("/api/settings/<src>", methods=["POST"])
    def api_save_settings(src: str):
        if src not in sources:
            return jsonify({"error": f"Unknown source: {src}"}), 404
        data = request.get_json()
        if data is None:
            return jsonify({"error": "Invalid JSON"}), 400
        ok = runner.save_settings(src, data)
        return jsonify({"ok": ok})

    app._gmail_ingestion_routes = True


app = create_app()
