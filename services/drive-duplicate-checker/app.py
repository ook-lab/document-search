import os
import json
import sys
import hashlib
import shutil
import subprocess
from pathlib import Path
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, send_file, Response
from flask_cors import CORS  # [重要] 他PCやクラウドUIからの接続を許可
from loguru import logger
from dotenv import load_dotenv

# プロジェクトルート
project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(project_root / ".env")

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from shared.common.connectors.google_drive import GoogleDriveConnector

app = Flask(__name__)
app.secret_key = "universal-controller-secret"
CORS(app)  # すべてのオリジンからのアクセスを許可 (クラウドUIからの司令を受け取るため)

CLEANUP_FOLDER_NAME = "削除候補（重複）"
SCAN_STATUS = {"active": False, "interrupt": False}

# Driveコネクタ
try:
    drive = GoogleDriveConnector()
except Exception as e:
    logger.error(f"Google Drive 接続失敗: {e}")
    drive = None

# --- 基本処理 ---

def calculate_local_md5(file_path):
    hash_md5 = hashlib.md5()
    try:
        if not os.path.isfile(file_path): return None
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                if SCAN_STATUS["interrupt"]: return None
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except: return None

# --- ファイル重複スキャン ---

def scan_local_duplicates_stream(root_paths, same_folder_only=False):
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    groups = defaultdict(list); file_count = 0
    try:
        for root_path in root_paths:
            yield f"data: {json.dumps({'log': f'スキャン開始: {root_path}'})}\n\n"
            for root, dirs, files in os.walk(root_path):
                if SCAN_STATUS["interrupt"]: break
                curr_dir = os.path.normpath(root)
                for name in files:
                    if SCAN_STATUS["interrupt"]: break
                    file_path = os.path.join(curr_dir, name); file_count += 1
                    yield f"data: {json.dumps({'log': f'[{file_count}件] {name}'})}\n\n"
                    try:
                        stat = os.stat(file_path); size = stat.st_size
                        if size == 0: continue
                        md5 = calculate_local_md5(file_path)
                        if md5:
                            key = (size, md5, curr_dir) if same_folder_only else (size, md5)
                            groups[key].append({"id": file_path, "name": name, "path": file_path, "size": size, "createdTime": stat.st_ctime})
                    except: pass
        res = [{"size": v[0]['size'], "files": v, "main_name": v[0]['name']} for v in groups.values() if len(v) > 1]
        res.sort(key=lambda x: x['size'], reverse=True)
        yield f"data: {json.dumps({'log': '完了', 'done': True, 'results': res})}\n\n"
    finally: SCAN_STATUS["active"] = False

# --- フォルダ統合 分析 ---

def analyze_local_folders(root_paths):
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    folder_inventory = defaultdict(set); total = 0
    try:
        for root_path in root_paths:
            for root, dirs, files in os.walk(root_path):
                if SCAN_STATUS["interrupt"]: break
                curr = os.path.normpath(root)
                for name in files:
                    total += 1; yield f"data: {json.dumps({'log': f'分析[{total}]: {name}'})}\n\n"
                    md5 = calculate_local_md5(os.path.join(curr, name))
                    if md5: folder_inventory[curr].add(md5)
        
        candidates = []
        f_list = [p for p, h in folder_inventory.items() if h]
        for i in range(len(f_list)):
            p_a = f_list[i]; h_a = folder_inventory[p_a]
            for j in range(i + 1, len(f_list)):
                p_b = f_list[j]; h_b = folder_inventory[p_b]
                if p_b.startswith(p_a + os.sep) or p_a.startswith(p_b + os.sep): continue
                common = h_a.intersection(h_b)
                if len(common) < 2: continue
                r_a, r_b = len(common)/len(h_a), len(common)/len(h_b)
                if max(r_a, r_b) >= 0.3:
                    candidates.append({"path_a": p_a, "path_b": p_b, "count_a": len(h_a), "count_b": len(h_b), "overlap": len(common), "ratio_a": round(r_a*100, 1), "ratio_b": round(r_b*100, 1), "max_ratio": round(max(r_a, r_b)*100, 1)})
        candidates.sort(key=lambda x: x['max_ratio'], reverse=True)
        yield f"data: {json.dumps({'log': '分析完了', 'done': True, 'results': candidates[:100]})}\n\n"
    finally: SCAN_STATUS["active"] = False

# --- [新] クラウド通信/ヘルスチェック ---

@app.route("/api/health")
def api_health():
    """指令塔から、手元のPCに作業員がいるかを確認するための窓口"""
    return jsonify({"success": True, "mode": os.environ.get("RUN_MODE", "LOCAL"), "pc": os.environ.get("COMPUTERNAME", "UNKNOWN")})

# --- その他の基本操作 ---

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/select_folder")
def api_select_folder():
    if os.environ.get("RUN_MODE") == "CLOUD": return jsonify({"success": False, "error": "クラウド上ではフォルダ窓は開けません。"})
    try:
        py_cmd = [sys.executable, "-c", "import tkinter as tk; from tkinter import filedialog; r=tk.Tk(); r.withdraw(); r.attributes('-topmost', True); print(filedialog.askdirectory())"]
        result = subprocess.check_output(py_cmd, text=True).strip()
        if result: return jsonify({"path": os.path.normpath(result), "success": True})
        return jsonify({"success": False})
    except: return jsonify({"success": False}), 500

@app.route("/api/scan_stream")
def api_scan_stream():
    stype = request.args.get("type", "local"); targets = json.loads(request.args.get("targets", "[]"))
    same = request.args.get("same_folder") == "true"
    if stype == "local": return Response(scan_local_duplicates_stream(targets, same), mimetype='text/event-stream')
    return Response(scan_drive_duplicates_stream(), mimetype='text/event-stream')

@app.route("/api/analyze_folders")
def api_analyze_folders():
    targets = json.loads(request.args.get("targets", "[]"))
    return Response(analyze_local_folders(targets), mimetype='text/event-stream')

@app.route("/api/merge_folders", methods=["POST"])
def api_merge_folders():
    try:
        d = request.json
        src, dst = d.get("src"), d.get("dst")
        if not src or not dst: return jsonify({"success": False, "error": "パスが未指定です。"})
        if not os.path.exists(src): return jsonify({"success": False, "error": f"元フォルダが見つかりません: {src}"})
        if not os.path.exists(dst): return jsonify({"success": False, "error": f"先フォルダが見つかりません: {dst}"})
        
        for item in os.listdir(src):
            s = os.path.join(src, item); d_path = os.path.join(dst, item)
            if os.path.isfile(s):
                if os.path.exists(d_path):
                    n, e = os.path.splitext(item); c = 1
                    while os.path.exists(os.path.join(dst, f"{n} ({c}){e}")): c += 1
                    d_path = os.path.join(dst, f"{n} ({c}){e}")
                shutil.move(s, d_path)
            elif os.path.isdir(s):
                # 必要に応じてフォルダ移動も対応
                shutil.move(s, dst)
        
        if not os.listdir(src): os.rmdir(src)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"マージ失敗: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/move_to_cleanup", methods=["POST"])
def api_move_to_cleanup():
    for fid in request.json.get("file_ids", []):
        try:
            dest = os.path.join(os.path.expanduser("~"), "Desktop", CLEANUP_FOLDER_NAME, os.path.basename(fid))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            n, e = os.path.splitext(os.path.basename(fid)); c = 1
            while os.path.exists(dest): dest = os.path.join(os.path.dirname(dest), f"{n} ({c}){e}"); c += 1
            shutil.move(fid, dest)
        except: pass
    return jsonify({"success": True})

@app.route("/api/stop_scan", methods=["POST"])
def api_stop_scan(): SCAN_STATUS["interrupt"] = True; return jsonify({"success": True})

@app.route("/api/local_preview")
def local_preview():
    p = request.args.get("path")
    if not p or not os.path.isfile(p): return "Not Found", 404
    return send_file(p)

def scan_drive_duplicates_stream():
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    if not drive: yield f"data: {json.dumps({'log': 'Drive未接続'})}\n\n"; return
    all_f = []; pt = None
    try:
        while True:
            if SCAN_STATUS["interrupt"]: break
            res = drive.service.files().list(q="trashed=false and mimeType != 'application/vnd.google-apps.folder'", fields='nextPageToken, files(id, name, size, md5Checksum, createdTime, webViewLink)', pageToken=pt).execute()
            all_f.extend(res.get('files', [])); yield f"data: {json.dumps({'log': f'Drive取得中: {len(all_f)}'})}\n\n"; pt = res.get('nextPageToken')
            if not pt: break
        gr = defaultdict(list)
        for f in all_f:
            s, m = f.get('size'), f.get('md5Checksum')
            if s and m: gr[(s, m)].append(f)
        yield f"data: {json.dumps({'log': '完了', 'done': True, 'results': [{'size': int(k[0]), 'files': v, 'main_name': v[0]['name']} for k, v in gr.items() if len(v) > 1]})}\n\n"
    finally: SCAN_STATUS["active"] = False

if __name__ == "__main__":
    # Cloud Run環境では環境変数 PORT が自動セットされるので、それを使う
    port = int(os.environ.get("PORT", 8082))
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
