"""読み込みコンテキスト用エディタ（1対1表・グリッド表・テキスト）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Flask, render_template, request, jsonify

from config import SUPABASE_ADMIN_USER_ID, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from payload_builder import build_ai_payloads

app = Flask(__name__)

_sb = None


def _sb():
    global _sb
    if _sb is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("SUPABASE_URL と SUPABASE_SERVICE_ROLE_KEY が必要です")
        from supabase import create_client

        _sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _sb


def _owner() -> Tuple[Optional[str], Optional[str]]:
    if not (SUPABASE_ADMIN_USER_ID or "").strip():
        return None, "SUPABASE_ADMIN_USER_ID が未設定です（.env）"
    return SUPABASE_ADMIN_USER_ID.strip(), None


def _validate_editor_document(doc: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(doc, dict):
        return None, "editor_document はオブジェクトである必要があります"
    doc = dict(doc)
    doc.setdefault("version", 1)
    doc.setdefault("title", "")
    tables = doc.get("tables")
    if not isinstance(tables, list):
        return None, "tables は配列である必要があります"
    texts = doc.get("text_sections")
    if not isinstance(texts, list):
        return None, "text_sections は配列である必要があります"
    doc["tables"] = tables
    doc["text_sections"] = texts
    return doc, None


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/")
def index():
    return render_template("editor.html")


@app.get("/api/profiles")
def list_profiles():
    oid, err = _owner()
    if err:
        return jsonify({"success": False, "error": err}), 500
    person_name = (request.args.get("person_name") or "").strip()
    try:
        q = (
            _sb()
            .table("reading_context_profiles")
            .select("id,person_name,title,updated_at")
            .eq("owner_id", oid)
        )
        if person_name:
            q = q.eq("person_name", person_name)
        res = q.order("updated_at", desc=True).execute()
        return jsonify({"success": True, "profiles": res.data or []})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.get("/api/persons")
def list_persons():
    oid, err = _owner()
    if err:
        return jsonify({"success": False, "error": err}), 500
    try:
        res = (
            _sb()
            .table("reading_context_profiles")
            .select("person_name")
            .eq("owner_id", oid)
            .execute()
        )
        names = sorted({(r.get("person_name") or "").strip() for r in (res.data or []) if (r.get("person_name") or "").strip()})
        return jsonify({"success": True, "persons": names})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.get("/api/profile/<profile_id>")
def get_profile(profile_id: str):
    oid, err = _owner()
    if err:
        return jsonify({"success": False, "error": err}), 500
    try:
        res = (
            _sb()
            .table("reading_context_profiles")
            .select("*")
            .eq("id", profile_id)
            .eq("owner_id", oid)
            .single()
            .execute()
        )
        if not res.data:
            return jsonify({"success": False, "error": "見つかりません"}), 404
        return jsonify({"success": True, "profile": res.data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 404


@app.post("/api/profile")
def save_profile():
    oid, err = _owner()
    if err:
        return jsonify({"success": False, "error": err}), 500
    data = request.get_json() or {}
    profile_id = (data.get("id") or "").strip() or None
    person_name = (data.get("person_name") or "").strip()
    if not person_name:
        return jsonify({"success": False, "error": "人の名前を指定してください"}), 400
    title = (data.get("title") or "").strip()
    raw_doc = data.get("editor_document")
    editor_doc, verr = _validate_editor_document(raw_doc)
    if verr:
        return jsonify({"success": False, "error": verr}), 400
    editor_doc["title"] = title

    ai_json, ai_md = build_ai_payloads(editor_doc)
    now = datetime.now(timezone.utc).isoformat()

    row: Dict[str, Any] = {
        "owner_id": oid,
        "person_name": person_name,
        "title": title,
        "editor_document": editor_doc,
        "ai_payload_json": ai_json,
        "ai_payload_md": ai_md,
        "updated_at": now,
    }

    try:
        if profile_id:
            up = (
                _sb()
                .table("reading_context_profiles")
                .update(row)
                .eq("id", profile_id)
                .eq("owner_id", oid)
                .execute()
            )
            if not up.data:
                return jsonify({"success": False, "error": "更新対象が見つかりません"}), 404
            saved = up.data[0]
        else:
            row["id"] = str(uuid.uuid4())
            row["created_at"] = now
            ins = _sb().table("reading_context_profiles").insert(row).execute()
            saved = (ins.data or [row])[0]
        return jsonify({"success": True, "profile": saved})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006, debug=True)
