"""Session auth endpoints shared with static/js/main.js (navbar login)."""
from flask import Blueprint, jsonify, request, session
from flask_wtf.csrf import generate_csrf

from services.auth_service import auth_service

auth_min_bp = Blueprint("auth_min", __name__)


@auth_min_bp.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        return jsonify({"error_code": "BAD_REQUEST", "message": "Request body required", "details": {}}), 400
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return jsonify({"error_code": "BAD_REQUEST", "message": "Email and password required", "details": {}}), 400
    success, error_msg = auth_service.login(email, password)
    if success:
        return jsonify(
            {"success": True, "user_email": session.get("user_email"), "csrf_token": generate_csrf()}
        )
    return jsonify({"error_code": "AUTH_FAILED", "message": error_msg or "Login failed", "details": {}}), 401


@auth_min_bp.route("/auth/logout", methods=["POST"])
def logout():
    auth_service.logout()
    return jsonify({"success": True})


@auth_min_bp.route("/auth/session", methods=["GET"])
def get_session():
    session_info = auth_service.get_session_info()
    session_info["csrf_token"] = generate_csrf()
    return jsonify(session_info)
