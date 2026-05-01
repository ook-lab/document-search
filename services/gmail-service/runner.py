"""Gmail ingestion runner (subprocess + SSE). Moved from data-ingestion."""
import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from subprocess import PIPE, STDOUT

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = Path(__file__).resolve().parent

_processes: dict = {}
_log_buffers: dict = {}
_run_states: dict = {}
_db = None


def _get_db():
    global _db
    if _db is None:
        if str(SERVICE_ROOT) not in sys.path:
            sys.path.insert(0, str(SERVICE_ROOT))
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
        from shared.common.database.client import DatabaseClient

        _db = DatabaseClient(use_service_role=True)
    return _db


def start_run(source: str, script_path: str, extra_args: list | None = None) -> str:
    run_id = str(uuid.uuid4())
    extra_args = extra_args or []
    cmd = [sys.executable, script_path] + extra_args
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(PROJECT_ROOT)}
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
        rid = str(uuid.uuid4())
        _log_buffers[rid] = [f"[ERROR] launch failed: {e}"]
        _run_states[rid] = {"done": True, "status": "error"}
        _save_run_log(rid, source, "error", str(e), str(e))
        return rid
    _processes[run_id] = proc
    _log_buffers[run_id] = []
    _run_states[run_id] = {"done": False, "status": "running"}
    try:
        db = _get_db()
        db.client.table("ingestion_run_log").insert(
            {"id": run_id, "source": source, "status": "running"}
        ).execute()
    except Exception as e:
        print(f"[gmail-runner] DB insert: {e}", file=sys.stderr)
    threading.Thread(target=_collect_output, args=(run_id, source, proc), daemon=True).start()
    return run_id


def _collect_output(run_id: str, source: str, proc: subprocess.Popen):
    lines: list = []
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            ts = datetime.now(timezone.utc).isoformat()
            _log_buffers[run_id].append(json.dumps({"line": line, "ts": ts}, ensure_ascii=False))
            lines.append(line)
        proc.wait()
        status = "success" if proc.returncode == 0 else "error"
    except Exception as e:
        status = "error"
        lines.append(f"[ERROR] {e}")
    _run_states[run_id] = {"done": True, "status": status}
    log_text = "\n".join(lines)
    err = None if status == "success" else (log_text[-2000:] if log_text else "Unknown error")
    _save_run_log(run_id, source, status, log_text, err)


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
        print(f"[gmail-runner] DB update: {e}", file=sys.stderr)


def stream_log(run_id: str):
    import time

    if run_id not in _log_buffers:
        yield "data: " + json.dumps(
            {"line": "run_id not found", "ts": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
        ) + "\n\n"
        yield "data: " + json.dumps({"done": True, "status": "error"}, ensure_ascii=False) + "\n\n"
        return
    sent = 0
    last_hb = time.time()
    while True:
        buf = _log_buffers.get(run_id, [])
        while sent < len(buf):
            yield "data: " + buf[sent] + "\n\n"
            sent += 1
            last_hb = time.time()
        st = _run_states.get(run_id, {})
        if st.get("done"):
            yield "data: " + json.dumps(
                {"done": True, "status": st.get("status", "unknown")}, ensure_ascii=False
            ) + "\n\n"
            break
        if time.time() - last_hb > 15:
            yield ": heartbeat\n\n"
            last_hb = time.time()
        time.sleep(0.2)


def get_history(limit: int = 50) -> list:
    try:
        db = _get_db()
        r = (
            db.client.table("ingestion_run_log")
            .select("*")
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return r.data or []
    except Exception as e:
        print(f"[gmail-runner] get_history: {e}", file=sys.stderr)
        return []


def get_settings(source: str) -> dict:
    try:
        db = _get_db()
        r = db.client.table("ingestion_settings").select("*").eq("source", source).execute()
        if r.data:
            return r.data[0].get("settings", {})
        return {}
    except Exception as e:
        print(f"[gmail-runner] get_settings: {e}", file=sys.stderr)
        return {}


def save_settings(source: str, settings: dict) -> bool:
    try:
        db = _get_db()
        db.client.table("ingestion_settings").upsert(
            {
                "source": source,
                "settings": settings,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
        return True
    except Exception as e:
        print(f"[gmail-runner] save_settings: {e}", file=sys.stderr)
        return False
