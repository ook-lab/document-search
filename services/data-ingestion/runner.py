"""
Data Ingestion Runner - subprocess起動 + SSEストリーミング
"""
import os
import sys
import uuid
import json
import threading
import subprocess
from datetime import datetime, timezone
from subprocess import PIPE, STDOUT
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_ROOT.parent.parent

# プロセス管理（run_id → Popen）
_processes: dict[str, subprocess.Popen] = {}
# ログバッファ（run_id → list[str]）
_log_buffers: dict[str, list] = {}
# 完了フラグ（run_id → {'done': bool, 'status': str}）
_run_states: dict[str, dict] = {}
# DBクライアント（遅延インポート）
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


def start_run(source: str, script_path: str, extra_args: list = None) -> str:
    """subprocessを起動。run_idをDBに記録して返す"""
    run_id = str(uuid.uuid4())
    extra_args = extra_args or []

    cmd = [sys.executable, script_path] + extra_args
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": f"{SERVICE_ROOT}{os.pathsep}{PROJECT_ROOT}",
    }

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
        _log_buffers[run_id_err] = [f"[ERROR] 起動失敗: {e}"]
        _run_states[run_id_err] = {'done': True, 'status': 'error'}
        _save_run_log(run_id_err, source, 'error', f"起動失敗: {e}", f"起動失敗: {e}")
        return run_id_err

    _processes[run_id] = proc
    _log_buffers[run_id] = []
    _run_states[run_id] = {'done': False, 'status': 'running'}

    # DBにstatus='running'で挿入
    try:
        db = _get_db()
        db.client.table('ingestion_run_log').insert({
            'id': run_id,
            'source': source,
            'status': 'running',
        }).execute()
    except Exception as e:
        print(f"[runner] DB insert error: {e}", file=sys.stderr)

    # バックグラウンドスレッドでstdout収集
    t = threading.Thread(target=_collect_output, args=(run_id, source, proc), daemon=True)
    t.start()

    return run_id


def _collect_output(run_id: str, source: str, proc: subprocess.Popen):
    """バックグラウンドスレッドでstdout収集 → DB保存"""
    lines = []
    try:
        for line in proc.stdout:
            line = line.rstrip('\n')
            ts = datetime.now(timezone.utc).isoformat()
            entry = json.dumps({'line': line, 'ts': ts}, ensure_ascii=False)
            _log_buffers[run_id].append(entry)
            lines.append(line)

        proc.wait()
        status = 'success' if proc.returncode == 0 else 'error'
    except Exception as e:
        status = 'error'
        lines.append(f"[ERROR] {e}")

    _run_states[run_id] = {'done': True, 'status': status}

    log_text = '\n'.join(lines)
    error_msg = None if status == 'success' else log_text[-2000:] if log_text else 'Unknown error'
    _save_run_log(run_id, source, status, log_text, error_msg)


def _save_run_log(run_id: str, source: str, status: str, log_output: str, error_message: str = None):
    """DBのingestion_run_logを更新"""
    try:
        db = _get_db()
        db.client.table('ingestion_run_log').update({
            'ended_at': datetime.now(timezone.utc).isoformat(),
            'status': status,
            'log_output': log_output,
            'error_message': error_message,
        }).eq('id', run_id).execute()
    except Exception as e:
        print(f"[runner] DB update error: {e}", file=sys.stderr)


def stream_log(run_id: str):
    """SSEジェネレータ。stdout行をyield"""
    import time
    if run_id not in _log_buffers:
        yield f"data: {json.dumps({'line': 'run_id not found', 'ts': datetime.now(timezone.utc).isoformat()})}\n\n"
        yield f"data: {json.dumps({'done': True, 'status': 'error'})}\n\n"
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
        if state.get('done'):
            yield f"data: {json.dumps({'done': True, 'status': state.get('status', 'unknown')})}\n\n"
            break

        # ハートビートを送る（15秒ごと）→ Cloud Runのアイドルタイムアウト防止
        if time.time() - last_heartbeat > 15:
            yield ": heartbeat\n\n"
            last_heartbeat = time.time()

        time.sleep(0.2)


def get_history(limit: int = 50) -> list:
    """実行履歴を取得"""
    try:
        db = _get_db()
        result = db.client.table('ingestion_run_log') \
            .select('*') \
            .order('started_at', desc=True) \
            .limit(limit) \
            .execute()
        return result.data or []
    except Exception as e:
        print(f"[runner] get_history error: {e}", file=sys.stderr)
        return []


def get_settings(source: str) -> dict:
    """ソース設定を取得"""
    try:
        db = _get_db()
        result = db.client.table('ingestion_settings') \
            .select('*') \
            .eq('source', source) \
            .execute()
        if result.data:
            return result.data[0].get('settings', {})
        return {}
    except Exception as e:
        print(f"[runner] get_settings error: {e}", file=sys.stderr)
        return {}


def save_settings(source: str, settings: dict) -> bool:
    """ソース設定を保存"""
    try:
        db = _get_db()
        db.client.table('ingestion_settings').upsert({
            'source': source,
            'settings': settings,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }).execute()
        return True
    except Exception as e:
        print(f"[runner] save_settings error: {e}", file=sys.stderr)
        return False
