"""Document Hub: merge review UI (services/doc-processor/review) into doc-processor.

Gmail cleaner is in services/gmail-service; not registered here.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from flask import Flask, g, session
from flask_wtf.csrf import CSRFProtect, generate_csrf
from jinja2 import ChoiceLoader, FileSystemLoader
from loguru import logger


def _review_bundle_root() -> Path:
    here = Path(__file__).resolve().parent
    bundled = here / "bundled" / "review"
    if bundled.is_dir() and (bundled / "blueprints").is_dir():
        return bundled
    return here / "review"


def _secret_key() -> str:
    explicit = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if explicit:
        return explicit
    rev = (os.environ.get("K_REVISION") or "").strip()
    svc = (os.environ.get("K_SERVICE") or "document-hub").strip()
    if rev:
        return hashlib.sha256(f"{svc}:{rev}".encode("utf-8")).hexdigest()
    return os.urandom(32).hex()


def register_document_hub(app: Flask) -> None:
    if getattr(app, "_document_hub_registered", False):
        return
    root = _review_bundle_root()
    if not (root / "blueprints").is_dir():
        logger.warning("review bundle not found at {}; hub disabled", str(root))
        return

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # /app/services/ が namespace package として先に解決されると
    # bundled/review/services/ の auth_service に到達できない。
    # 既に "services" が sys.modules に入っていたら退避して再解決させる。
    _stale = sys.modules.pop("services", None)

    app.config.setdefault("SECRET_KEY", _secret_key())
    app.config.setdefault("SESSION_COOKIE_SECURE", os.environ.get("FLASK_ENV") == "production")
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)
    app.config.setdefault("WTF_CSRF_CHECK_DEFAULT", False)

    CSRFProtect(app)

    from blueprints.api import api_bp
    from blueprints.documents import documents_bp
    from blueprints.emails import emails_bp

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(documents_bp, url_prefix="/documents")
    app.register_blueprint(emails_bp, url_prefix="/emails")

    main_tpl = Path(app.root_path) / "templates"
    rev_tpl = root / "templates"
    loaders = [FileSystemLoader(str(rev_tpl))]
    if main_tpl.is_dir():
        loaders.append(FileSystemLoader(str(main_tpl)))
    app.jinja_loader = ChoiceLoader(loaders)

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": generate_csrf}

    @app.before_request
    def hub_before_request():
        try:
            g.user_email = session.get("user_email")
            g.access_token = session.get("access_token")
            g.is_authenticated = g.access_token is not None
        except Exception as e:
            logger.warning("session read failed (treating as logged out): {}", e)
            g.user_email = None
            g.access_token = None
            g.is_authenticated = False

    @app.after_request
    def hub_after_request(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    app._document_hub_registered = True
    logger.info("Document hub registered from {}", str(root))