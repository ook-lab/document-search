"""Subprocess runner + ingestion_run_log (moved from data-ingestion for 3 netsuper stores)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from subprocess import PIPE, STDOUT

from paths import NETSUPER_DIR, REPO_ROOT

PROJECT_ROOT = REPO_ROOT
SERVICE_ROOT = NETSUPER_DIR

_processes: dict[str, subprocess.Popen] = {}
_log_buffers: dict[str, list] = {}
_run_states: dict[str, dict] = {}
_db = None


def _pythonpath() -> str:
    sep = os.pathsep
    return f"{PROJECT_ROOT}{sep}{SERVICE_ROOT}"


def _get_db():
    global _db
    if _db is None:
        for p in (str(SERVICE_ROOT), str(PROJECT_ROOT)):
            if p not in sys.path:
                sys.path.insert(0, p)
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
        from shared.common.database.client import DatabaseClient

        _db = DatabaseClient(use_service_role=True)
    return _db


def start_run(source: str, script_path: str, extra_args: list | None = None) -> str:
    run_id = str(uuid.uuid4())
    extra_args = extra_args or []
    cmd = [sys.executable, script_path] + extra_args
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": _pythonpath()}
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=PIPE,
            stderr=STDOUT,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
            bufsize=1,
        )
    except Exception as e:
        run_id_err = str(uuid.uuid4())
        _log_buffers[run_id_err] = [json.dumps({"line": f"[ERROR] 起動失敗: {e}", "ts": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)]
        _run_states[run_id_err] = {"done": True, "status": "error"}
        _save_run_log(run_id_err, source, "error", f"起動失敗: {e}", f"起動失敗: {e}")
        return run_id_err

    _processes[run_id] = proc
    _log_buffers[run_id] = []
    _run_states[run_id] = {"done": False, "status": "running"}
    try:
        db = _get_db()
        db.client.table("ingestion_run_log").insert({"id": run_id, "source": source, "status": "running"}).execute()
    except Exception as e:
        print(f"[ingestion_runner] DB insert error: {e}", file=sys.stderr)

    threading.Thread(target=_collect_output, args=(run_id, source, proc), daemon=True).start()
    return run_id


def _collect_output(run_id: str, source: str, proc: subprocess.Popen):
    lines: list[str] = []
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            ts = datetime.now(timezone.utc).isoformat()
            entry = json.dumps({"line": line, "ts": ts}, ensure_ascii=False)
            _log_buffers[run_id].append(entry)
            lines.append(line)
        proc.wait()
        status = "success" if proc.returncode == 0 else "error"
    except Exception as e:
        status = "error"
        lines.append(f"[ERROR] {e}")

    _run_states[run_id] = {"done": True, "status": status}
    log_text = "\n".join(lines)
    error_msg = None if status == "success" else (log_text[-2000:] if log_text else "Unknown error")
    _save_run_log(run_id, source, status, log_text, error_msg)


def _save_run_log(run_id: str, source: str, status: str, log_output: str, error_message: str | None = None):
    try:
        db = _get_db()
        db.client.table("ingestion_run_log").update(
            {
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "log_output": log_output,
                "error_message": error_message,
            }
        ).eq("id", run_id).execute()
    except Exception as e:
        print(f"[ingestion_runner] DB update error: {e}", file=sys.stderr)


def stream_log(run_id: str):
    import time

    if run_id not in _log_buffers:
        yield f"data: {json.dumps({'line': 'run_id not found', 'ts': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True, 'status': 'error'}, ensure_ascii=False)}\n\n"
        return

    sent = 0
    last_heartbeat = time.time()
    while True:
        buf = _log_buffers.get(run_id, [])
        while sent < len(buf):
            yield f"data: {buf[sent]}\n\n"
            sent += 1
            last_heartbeat = time.time()
        state = _run_states.get(run_id, {})
        if state.get("done"):
            yield f"data: {json.dumps({'done': True, 'status': state.get('status', 'unknown')}, ensure_ascii=False)}\n\n"
            break
        if time.time() - last_heartbeat > 15:
            yield ": heartbeat\n\n"
            last_heartbeat = time.time()
        time.sleep(0.2)


def get_history(limit: int = 50) -> list:
    try:
        db = _get_db()
        result = db.client.table("ingestion_run_log").select("*").order("started_at", desc=True).limit(limit).execute()
        return result.data or []
    except Exception as e:
        print(f"[ingestion_runner] get_history error: {e}", file=sys.stderr)
        return []


def get_settings(source: str) -> dict:
    try:
        db = _get_db()
        result = db.client.table("ingestion_settings").select("*").eq("source", source).execute()
        if result.data:
            return result.data[0].get("settings", {})
        return {}
    except Exception as e:
        print(f"[ingestion_runner] get_settings error: {e}", file=sys.stderr)
        return {}


def save_settings(source: str, settings: dict) -> bool:
    try:
        db = _get_db()
        db.client.table("ingestion_settings").upsert(
            {"source": source, "settings": settings, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).execute()
        return True
    except Exception as e:
        print(f"[ingestion_runner] save_settings error: {e}", file=sys.stderr)
        return False
