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

def calculate_partial_md5(file_path):
    """最初の8KBだけをハッシュ化して高速判定"""
    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read(8192)).hexdigest()
    except: return None

def scan_local_duplicates_stream(root_paths, same_folder_only=False):
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    size_groups = defaultdict(list); file_count = 0
    
    try:
        # フェーズ1: サイズのみ収集 (一瞬で終わる)
        for root_path in root_paths:
            yield f"data: {json.dumps({'log': f'リスト構築中: {root_path}'})}\n\n"
            for root, dirs, files in os.walk(root_path):
                if SCAN_STATUS["interrupt"]: break
                curr_dir = os.path.normpath(root)
                for name in files:
                    if SCAN_STATUS["interrupt"]: break
                    file_path = os.path.join(curr_dir, name); file_count += 1
                    try:
                        size = os.path.getsize(file_path)
                        if size == 0: continue
                        key = (size, curr_dir) if same_folder_only else size
                        size_groups[key].append(file_path)
                    except: pass
                    if file_count % 1000 == 0:
                        yield f"data: {json.dumps({'log': f'探索中... {file_count}件発見'})}\n\n"

        # フェーズ2: サイズ一致組のみ詳細チェック (劇的に絞り込まれる)
        results = []
        md5_groups = defaultdict(list)
        potential_candidates = {k: v for k, v in size_groups.items() if len(v) > 1}
        size_groups.clear() # メモリ解放
        
        checked_count = 0
        total_potentials = sum(len(v) for v in potential_candidates.values())
        
        for key, paths in potential_candidates.items():
            for f_path in paths:
                if SCAN_STATUS["interrupt"]: break
                checked_count += 1
                if checked_count % 100 == 0:
                    prog = int(checked_count / total_potentials * 100)
                    yield f"data: {json.dumps({'log': f'精密分析中... {prog}% ({checked_count}/{total_potentials})'})}\n\n"
                
                # 最初は部分ハッシュで判定
                p_md5 = calculate_partial_md5(f_path)
                if not p_md5: continue
                
                # 部分一致があるならフルハッシュ
                f_md5 = calculate_local_md5(f_path)
                if f_md5:
                    stat = os.stat(f_path)
                    res_key = (key[0], f_md5, key[1]) if same_folder_only else (key, f_md5)
                    md5_groups[res_key].append({
                        "id": f_path, "name": os.path.basename(f_path), 
                        "path": f_path, "size": stat.st_size, "createdTime": stat.st_ctime
                    })
        
        # 500件を超える場合はサイズが大きい順に上位500件のみに絞る (ブラウザのパンク防止)
        res = [{"size": v[0]['size'], "files": v, "main_name": v[0]['name']} for v in md5_groups.values() if len(v) > 1]
        res.sort(key=lambda x: x['size'], reverse=True)
        final_res = res[:500] 
        
        yield f_data({"log": "完了 (上位500件を表示中)", "done": True, "results": final_res})
        
        # 巨大データの完全消去
        potential_candidates.clear(); del potential_candidates
        md5_groups.clear(); del md5_groups
        res.clear(); del res; final_res.clear(); del final_res
        gc.collect()
    finally:
        SCAN_STATUS["active"] = False
        gc.collect()

# --- フォルダ統合 分析 ---

def f_data(obj):
    return f"data: {json.dumps(obj)}\n\n"

def analyze_local_folders(root_paths):
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    folder_inventory = defaultdict(set); total = 0
    try:
        # 爆速化: まずファイルサイズと最初の一部だけ見てフォルダの「指紋」を作る
        for root_path in root_paths:
            for root, dirs, files in os.walk(root_path):
                if SCAN_STATUS["interrupt"]: break
                curr = os.path.normpath(root)
                for name in files:
                    total += 1
                    f_path = os.path.join(curr, name)
                    try:
                        # フォルダの中身の「サイズ」だけで最初の判定用指紋を作る (ハッシュ計算を省略)
                        size = os.path.getsize(f_path)
                        if size > 1024:
                            # 1KB超えのファイルのみ指紋に含める
                            folder_inventory[curr].add(size)
                    except: pass
                    if total % 1000 == 0:
                        yield f_data({"log": f"分析中... {total}ファイル走査"})

        candidates = []
        f_list = list(folder_inventory.keys())
        
        for i in range(len(f_list)):
            p_a = f_list[i]; set_a = folder_inventory[p_a]
            if not set_a: continue
            for j in range(i + 1, len(f_list)):
                if SCAN_STATUS["interrupt"]: break
                p_b = f_list[j]; set_b = folder_inventory[p_b]
                if not set_b: continue
                
                # 親子関係は無視
                if p_b.startswith(p_a + os.sep) or p_a.startswith(p_b + os.sep): continue
                
                # 共通のファイルサイズが多いかチェック
                common = set_a.intersection(set_b)
                if len(common) > 1: # 2つ以上サイズが一致すればマージ候補
                    ratio = (len(common) * 2) / (len(set_a) + len(set_b))
                    if ratio > 0.4: # 40%以上一致
                        candidates.append({"path_a": p_a, "path_b": p_b, "ratio": int(ratio * 100)})

        candidates.sort(key=lambda x: x['ratio'], reverse=True)
        final_candidates = candidates[:100] # 上位100件に絞る
        
        yield f_data({"log": "分析完了 (上位100件を表示中)", "done": True, "results": final_candidates})
        
        # 強制メモリ開放
        folder_inventory.clear(); del folder_inventory
        candidates.clear(); del candidates; final_candidates.clear(); del final_candidates
        f_list.clear(); del f_list
        gc.collect()
        
    finally:
        SCAN_STATUS["active"] = False
        gc.collect()

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
    # JS側のパラメータ名 same_parent_only に合わせる
    same = request.args.get("same_parent_only") == "true"
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
        
        moved_count = 0
        errors = []
        for item in os.listdir(src):
            s = os.path.join(src, item)
            d_path = os.path.join(dst, item)
            try:
                if os.path.isfile(s):
                    if os.path.exists(d_path):
                        n, e = os.path.splitext(item); c = 1
                        while os.path.exists(os.path.join(dst, f"{n} ({c}){e}")): c += 1
                        d_path = os.path.join(dst, f"{n} ({c}){e}")
                    shutil.move(s, d_path)
                    moved_count += 1
                elif os.path.isdir(s):
                    shutil.move(s, dst)
                    moved_count += 1
            except Exception as e:
                errors.append(f"{item}: {str(e)}")
        
        # すべて空になった場合のみ削除を試みる
        if not os.listdir(src):
            try:
                os.rmdir(src)
            except:
                pass # フォルダ消去失敗は通知せず、ファイル移動成功を優先
        
        if errors:
            return jsonify({"success": True, "partial_error": True, "error": "\n".join(errors[:3]), "moved": moved_count})
        return jsonify({"success": True, "moved": moved_count})
    except Exception as e:
        logger.error(f"マージ致命的失敗: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/delete_file", methods=["POST"])
def api_delete_file():
    try:
        path = request.json.get("path")
        if os.path.exists(path):
            os.remove(path)
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "ファイルが見つかりません"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/open_explorer")
def api_open_explorer():
    path = request.args.get("path")
    if os.path.exists(path):
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        subprocess.Popen(['explorer', '/select,' + os.path.normpath(path)] if os.path.isfile(path) else ['explorer', os.path.normpath(path)])
        return jsonify({"success": True})
    return jsonify({"success": False})

def get_cleanup_dir():
    """OneDrive等に対応したデスクトップ上の削除候補フォルダのパスを取得"""
    home = os.path.expanduser("~")
    # 候補1: 標準デスクトップ, 候補2: OneDrive内のデスクトップ
    candidates = [
        os.path.join(home, "Desktop", CLEANUP_FOLDER_NAME),
        os.path.join(home, "OneDrive", "Desktop", CLEANUP_FOLDER_NAME),
        os.path.join(home, "OneDrive", "デスクトップ", CLEANUP_FOLDER_NAME)
    ]
    for c in candidates:
        if os.path.exists(os.path.dirname(c)): return c
    return os.path.join(home, "Desktop", CLEANUP_FOLDER_NAME) # フォールバック

@app.route("/api/open_cleanup_folder")
def api_open_cleanup_folder():
    path = get_cleanup_dir()
    os.makedirs(path, exist_ok=True)
    subprocess.Popen(['explorer', os.path.normpath(path)])
    return jsonify({"success": True})

@app.route("/api/move_to_cleanup", methods=["POST"])
def api_move_to_cleanup():
    base_dest = get_cleanup_dir()
    os.makedirs(base_dest, exist_ok=True)
    for fid in request.json.get("file_ids", []):
        try:
            dest = os.path.join(base_dest, os.path.basename(fid))
            n, e = os.path.splitext(os.path.basename(fid)); c = 1
            while os.path.exists(dest): dest = os.path.join(base_dest, f"{n} ({c}){e}"); c += 1
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
