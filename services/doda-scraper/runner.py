"""
Doda Scraper Runner - subprocess起動 + SSEストリーミング
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

# プロジェクトルート
PROJECT_ROOT = Path(__file__).parent.parent.parent

# プロセス管理（run_id → Popen）
_processes: dict[str, subprocess.Popen] = {}
# ログバッファ（run_id → list[str]）
_log_buffers: dict[str, list] = {}
# 完了フラグ（run_id → {'done': bool, 'status': str}）
_run_states: dict[str, dict] = {}

def start_run(source: str, script_path: str, extra_args: list = None) -> str:
    """subprocessを起動。run_idをDBに記録して返す"""
    run_id = str(uuid.uuid4())
    extra_args = extra_args or []

    # 仮想環境のpythonではなく、現在のpythonを使用
    cmd = [sys.executable, script_path] + extra_args
    env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONPATH': str(PROJECT_ROOT)}

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
        _log_buffers[run_id_err] = [json.dumps({'line': f"[ERROR] 起動失敗: {e}", 'ts': datetime.now(timezone.utc).isoformat()})]
        _run_states[run_id_err] = {'done': True, 'status': 'error'}
        return run_id_err

    _processes[run_id] = proc
    _log_buffers[run_id] = []
    _run_states[run_id] = {'done': False, 'status': 'running'}

    # バックグラウンドスレッドでstdout収集
    t = threading.Thread(target=_collect_output, args=(run_id, source, proc), daemon=True)
    t.start()

    return run_id

def _collect_output(run_id: str, source: str, proc: subprocess.Popen):
    """バックグラウンドスレッドでstdout収集"""
    try:
        for line in proc.stdout:
            line = line.rstrip('\n')
            ts = datetime.now(timezone.utc).isoformat()
            entry = json.dumps({'line': line, 'ts': ts}, ensure_ascii=False)
            if run_id not in _log_buffers:
                _log_buffers[run_id] = []
            _log_buffers[run_id].append(entry)

        proc.wait()
        status = 'success' if proc.returncode == 0 else 'error'
    except Exception as e:
        status = 'error'
        ts = datetime.now(timezone.utc).isoformat()
        _log_buffers[run_id].append(json.dumps({'line': f"[ERROR] {e}", 'ts': ts}))

    _run_states[run_id] = {'done': True, 'status': status}

def stream_log(run_id: str):
    """SSEジェネレータ。stdout行をyield"""
    sent = 0
    while True:
        buf = _log_buffers.get(run_id, [])
        while sent < len(buf):
            yield f"data: {buf[sent]}\n\n"
            sent += 1

        state = _run_states.get(run_id, {})
        if state.get('done'):
            yield f"data: {json.dumps({'done': True, 'status': state.get('status', 'unknown')})}\n\n"
            break

        import time
        time.sleep(0.2)
