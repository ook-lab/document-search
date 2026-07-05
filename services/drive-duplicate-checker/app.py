import os
import re
import io
import json
import sys
import hashlib
import shutil
import subprocess
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import concurrent.futures
from googleapiclient.http import MediaIoBaseDownload
from flask import Flask, render_template, request, jsonify, send_file, Response
from flask_cors import CORS  # [重要] 他PCやクラウドUIからの接続を許可
from loguru import logger
logger.add("app.log", rotation="10 MB", level="DEBUG")
from dotenv import load_dotenv
import gc
import ctypes
from ctypes import wintypes

_service_dir = Path(__file__).resolve().parent
project_root = _service_dir.parent.parent
load_dotenv(_service_dir / ".env")
load_dotenv(project_root / ".env", override=False)
if str(_service_dir) not in sys.path:
    sys.path.insert(0, str(_service_dir))

from oauth2_drive_connector import OAuth2DriveConnector

app = Flask(__name__)
app.secret_key = "universal-controller-secret"
CORS(app)  # すべてのオリジンからのアクセスを許可 (クラウドUIからの司令を受け取るため)

CLEANUP_FOLDER_NAME = "削除候補（重複）"
SCAN_STATUS = {"active": False, "interrupt": False}

GOOGLE_EXPORT_MIME = {
    'application/vnd.google-apps.spreadsheet': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.google-apps.document': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.google-apps.presentation': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.google-apps.drawing': 'application/pdf',
}
# 軽量テキストで事前スクリーニングできる種別（→ 候補のみ本格エクスポート）
GOOGLE_PRESCREEN_MIME = {
    'application/vnd.google-apps.spreadsheet': 'text/csv',
    'application/vnd.google-apps.document': 'text/plain',
    'application/vnd.google-apps.presentation': 'text/plain',
}

def _collect_all_folder_ids(service, root_ids):
    """指定フォルダとその全サブフォルダのIDを再帰収集する。"""
    all_ids = set(root_ids)
    queue = list(root_ids)
    while queue:
        batch, queue = queue[:30], queue[30:]
        conditions = " or ".join(f"'{fid}' in parents" for fid in batch)
        q = f"trashed=false and mimeType='application/vnd.google-apps.folder' and ({conditions})"
        pt = None
        while True:
            res = service.files().list(q=q, fields='nextPageToken, files(id)', pageToken=pt).execute()
            for f in res.get('files', []):
                if f['id'] not in all_ids:
                    all_ids.add(f['id'])
                    queue.append(f['id'])
            pt = res.get('nextPageToken')
            if not pt: break
    return all_ids


def _list_files_in_folders(service, folder_ids, extra_q=""):
    """フォルダID群（大量でも可）に含まれるファイルを全件取得する。"""
    all_files = []
    id_list = list(folder_ids)
    # Drive APIのクエリ長制限を避けるため30件ずつ分割
    for i in range(0, len(id_list), 30):
        batch = id_list[i:i+30]
        conditions = " or ".join(f"'{fid}' in parents" for fid in batch)
        q = f"trashed=false and mimeType != 'application/vnd.google-apps.folder' and ({conditions}){extra_q}"
        pt = None
        while True:
            res = service.files().list(
                q=q,
                fields='nextPageToken, files(id, name, mimeType, size, md5Checksum, quotaBytesUsed, createdTime, webViewLink)',
                pageToken=pt
            ).execute()
            all_files.extend(res.get('files', []))
            pt = res.get('nextPageToken')
            if not pt: break
    return all_files


def _export_md5(session, file_id, export_mime):
    """スレッドセーフなrequestsセッションでエクスポートしMD5を返す。429は指数バックオフでリトライ。"""
    import time
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
    for attempt in range(4):
        try:
            resp = session.get(url, params={'mimeType': export_mime}, timeout=60)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                logger.warning(f"エクスポート失敗 {file_id}: HTTP {resp.status_code}")
                return None
            return hashlib.md5(resp.content).hexdigest()
        except Exception as e:
            logger.warning(f"エクスポート失敗 {file_id}: {e}")
            return None

drive_connector = OAuth2DriveConnector()

# --- 基本処理 ---

def calculate_local_md5(file_path):
    """巨大ファイルはサンプリングハッシュ、通常ファイルはフルハッシュで高速計算"""
    hash_md5 = hashlib.md5()
    try:
        if not os.path.isfile(file_path): return None
        size = os.path.getsize(file_path)
        
        # 10MB未満のファイルは通常通りフルハッシュ
        if size < 10 * 1024 * 1024:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    if SCAN_STATUS["interrupt"]: return None
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
            
        # 10MB以上の巨大ファイルはサンプリングハッシュ (先頭、中間、末尾からそれぞれ1MBずつサンプリング)
        # これにより数GBのファイルでも一瞬で終わる
        sample_size = 1 * 1024 * 1024  # 1MB
        with open(file_path, "rb") as f:
            # 1. 先頭 1MB
            hash_md5.update(f.read(sample_size))
            
            # 2. 中間 1MB
            if SCAN_STATUS["interrupt"]: return None
            f.seek(size // 2)
            hash_md5.update(f.read(sample_size))
            
            # 3. 末尾 1MB
            if SCAN_STATUS["interrupt"]: return None
            f.seek(max(0, size - sample_size))
            hash_md5.update(f.read(sample_size))
            
            # 4. サイズ情報もハッシュに混ぜる（衝突防止）
            hash_md5.update(str(size).encode())
            
        return hash_md5.hexdigest()
    except: return None

def calculate_partial_md5(file_path):
    """最初の8KBだけをハッシュ化して高速判定"""
    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read(8192)).hexdigest()
    except: return None

# --- ファイル重複スキャン ---

def scan_local_duplicates_stream(root_paths, same_folder_only=False, fast_mode=False):
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    size_groups = defaultdict(list); file_count = 0
    
    try:
        # スキャンから除外する一般的なシステム/開発用フォルダ名
        EXCLUDE_DIR_NAMES = {
            'node_modules', '.git', 'venv', '.venv', '__pycache__', 'env', 
            '.idea', '.vscode', 'appdata', 'temp', 'tmp', '.cache'
        }

        # フェーズ1: サイズのみ収集 (os.scandirによる超高速スキャン)
        file_count = 0
        for root_path in root_paths:
            yield f"data: {json.dumps({'log': f'リスト構築中: {root_path}'})}\n\n"
            
            # ディレクトリごとに一度だけ正規化を行うため、スタックには (生のパス, 正規化されたパス) を格納する
            norm_root = os.path.normpath(root_path)
            stack = [(root_path, norm_root)]
            
            while stack:
                if SCAN_STATUS["interrupt"]: break
                curr_path, norm_curr = stack.pop()
                logger.info(f"ローカル走査中: {norm_curr}")
                try:
                    with os.scandir(curr_path) as it:
                        for entry in it:
                            if SCAN_STATUS["interrupt"]: break
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    # 特定の除外フォルダは走査しない
                                    if entry.name.lower() in EXCLUDE_DIR_NAMES:
                                        continue
                                    
                                    # 子フォルダの正規化パスは親の正規化パスと結合するだけで作れる (normpathの呼び出し回避)
                                    norm_child = os.path.join(norm_curr, entry.name)
                                    stack.append((entry.path, norm_child))
                                elif entry.is_file(follow_symlinks=False):
                                    file_count += 1
                                    
                                    # entry.stat() は Windows では追加システムコールなしでキャッシュからサイズ取得可能
                                    stat = entry.stat()
                                    size = stat.st_size

                                    name = entry.name
                                    ext = os.path.splitext(name)[1].lower()
                                    
                                    # ファイルごとの os.path.normpath(entry.path) を完全に排除
                                    file_path = os.path.join(norm_curr, name)
                                    
                                    # メタデータを保持しておくことで後の os.stat 呼び出しを回避
                                    meta = {
                                        "id": file_path, "name": name, 
                                        "path": file_path, "size": size, "createdTime": stat.st_ctime
                                    }
                                    
                                    key = (size, norm_curr, ext) if same_folder_only else (size, ext)
                                    size_groups[key].append(meta)
                                    
                                    if file_count % 1000 == 0:
                                        # 現在スキャン中のディレクトリの末尾を表示して視認性を上げる
                                        short_path = norm_curr if len(norm_curr) < 40 else "..." + norm_curr[-37:]
                                        yield f"data: {json.dumps({'log': f'探索中... {file_count}件発見 ({short_path})'})}\n\n"
                            except Exception:
                                pass
                except Exception:
                    pass

        # フェーズ2: サイズ一致組のみ詳細チェック (部分ハッシュによる高速1次スクリーニング)
        results = []
        md5_groups = defaultdict(list)
        potential_candidates = {k: v for k, v in size_groups.items() if len(v) > 1}
        size_groups.clear() # メモリ解放
        
        # 厳密スキャン (ハッシュ計算あり) -> マルチスレッドによる並列処理で劇的に高速化
        # 1次スクリーニング: 部分ハッシュでグループ分けする
        all_targets = [meta for metas in potential_candidates.values() for meta in metas]
        total_candidates = len(all_targets)
        part_results = {}
        cand_count = 0
        WORKERS = 32

        if total_candidates > 0:
            yield f"data: {json.dumps({'log': f'部分ハッシュ分析を開始します... (並列対象: {total_candidates}件)'})}\n\n"
            logger.info(f"部分ハッシュ分析を開始します... (並列対象: {total_candidates}件)")

            # スレッドプールによる並列ファイルオープン & 部分ハッシュ計算 (32スレッドに増強)
            with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
                futures = {executor.submit(calculate_partial_md5, meta["path"]): meta for meta in all_targets}
                for future in concurrent.futures.as_completed(futures):
                    if SCAN_STATUS["interrupt"]: break
                    meta = futures[future]
                    f_path = meta["path"]
                    try:
                        p_md5 = future.result()
                        if p_md5:
                            part_results[f_path] = p_md5
                    except Exception:
                        pass
                    
                    cand_count += 1
                    fname = meta["name"]
                    prog = int(cand_count / total_candidates * 100)
                    yield f"data: {json.dumps({'log': f'部分ハッシュ分析中... {prog}% ({cand_count}/{total_candidates}) - {fname}'})}\n\n"
                    logger.info(f"部分ハッシュ分析中 ({cand_count}/{total_candidates}): {f_path}")

        # 部分ハッシュの計算結果から part_groups を構築
        part_groups = defaultdict(list)
        for key, metas in potential_candidates.items():
            for meta in metas:
                p_md5 = part_results.get(meta["path"])
                if p_md5:
                    part_key = (key[0], p_md5, key[1], key[2]) if same_folder_only else (key[0], p_md5, key[1])
                    part_groups[part_key].append(meta)

        # 2次スクリーニング: 部分ハッシュも一致したファイル（真の重複候補）に対してのみフルハッシュを計算する
        # ただしサイズが 8KB (8192バイト) 以下のファイルは部分ハッシュ == フルハッシュのため、
        # 精密分析（2回目のファイルオープン）からはスキップする。
        md5_results = {}
        for part_key, metas in part_groups.items():
            if len(metas) < 2: continue
            p_md5 = part_key[1]
            for meta in metas:
                if meta["size"] <= 8192:
                    md5_results[meta["path"]] = p_md5

        # 8KBを超えるファイルのみを精密分析の対象とする
        full_hash_targets = [meta for g in part_groups.values() if len(g) > 1 for meta in g if meta["size"] > 8192]
        total_potentials = len(full_hash_targets)
        checked_count = 0

        if total_potentials > 0:
            yield f"data: {json.dumps({'log': f'精密分析を開始します... (並列対象: {total_potentials}件, 8KB以下スキップ済)'})}\n\n"
            logger.info(f"精密分析を開始します... (並列対象: {total_potentials}件, 8KB以下スキップ済)")

            # スレッドプールによる並列フルハッシュ計算
            with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
                futures = {executor.submit(calculate_local_md5, meta["path"]): meta for meta in full_hash_targets}
                for future in concurrent.futures.as_completed(futures):
                    if SCAN_STATUS["interrupt"]: break
                    meta = futures[future]
                    f_path = meta["path"]
                    try:
                        f_md5 = future.result()
                        if f_md5:
                            md5_results[f_path] = f_md5
                    except Exception:
                        pass
                    
                    checked_count += 1
                    fname = meta["name"]
                    prog = int(checked_count / total_potentials * 100)
                    yield f"data: {json.dumps({'log': f'精密分析中... {prog}% ({checked_count}/{total_potentials}) - {fname}'})}\n\n"
                    logger.info(f"精密分析中 ({checked_count}/{total_potentials}): {f_path}")

        # フルハッシュの計算結果から md5_groups を構築 (os.statの呼び出しを完全に排除)
        for part_key, metas in part_groups.items():
            if len(metas) < 2: continue  # 部分ハッシュが重複していないものは除外
            for meta in metas:
                f_md5 = md5_results.get(meta["path"])
                if f_md5:
                    res_key = (part_key[0], f_md5, part_key[2], part_key[3]) if same_folder_only else (part_key[0], f_md5, part_key[2])
                    md5_groups[res_key].append(meta)
        
        # 1500件を超える場合はサイズが大きい順に上位1500件のみに絞る (ブラウザのパンク防止)
        res = [{"size": v[0]['size'], "files": v, "main_name": v[0]['name']} for v in md5_groups.values() if len(v) > 1]
        res.sort(key=lambda x: x['size'], reverse=True)
        final_res = res[:1500] 
        
        yield f_data({"log": "完了 (上位1500件を表示中)", "done": True, "results": final_res})
        
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

def clean_path_brackets(path):
    import re
    normalized = path.replace('\\', '/')
    parts = normalized.split('/')
    clean_parts = []
    for part in parts:
        if not part:
            clean_parts.append("")
            continue
        # カッコ除去 (1), （１）, .1), [1], 【1】 など
        c = re.sub(r'\s*[\[\(（【\.][^\]\)）】]*[\]\)）】]\s*', '', part)
        # 連番除去 _1, -1
        c = re.sub(r'[\s_-]+\d+$', '', c).strip().lower()
        clean_parts.append(c)
    return '/'.join(clean_parts)

def determine_merge_direction(path_a, path_b):
    import re
    name_a = os.path.basename(path_a)
    name_b = os.path.basename(path_b)
    
    def count_brackets_and_serials(name):
        has_bracket = bool(re.search(r'[\[\(（【\.][^\]\)）】]*[\]\)）】]', name))
        has_serial = bool(re.search(r'[\s_-]+\d+$', name))
        count = 0
        if has_bracket or has_serial:
            count += 1
        return count

    def get_bracket_numbers(name):
        digits = []
        for x in re.findall(r'\d+', name):
            try:
                digits.append(int(x))
            except ValueError:
                pass
        return digits

    count_a = count_brackets_and_serials(name_a)
    count_b = count_brackets_and_serials(name_b)
    
    if count_a < count_b:
        return path_b, path_a # src: B, dst: A
    elif count_b < count_a:
        return path_a, path_b # src: A, dst: B
        
    nums_a = get_bracket_numbers(name_a)
    nums_b = get_bracket_numbers(name_b)
    min_a = min(nums_a) if nums_a else 999
    min_b = min(nums_b) if nums_b else 999
    
    if min_a < min_b:
        return path_b, path_a
    elif min_b < min_a:
        return path_a, path_b
        
    if len(name_a) < len(name_b):
        return path_b, path_a
    else:
        return path_a, path_b

def analyze_local_folders(root_paths, simple_mode=False):
    import re
    from collections import Counter
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    
    if simple_mode:
        try:
            candidates = []
            folder_count = 0
            for root_path in root_paths:
                for root, dirs, files in os.walk(root_path):
                    # システムフォルダや同期用一時フォルダには侵入しない
                    dirs[:] = [d for d in dirs if not is_system_or_temp_path(os.path.join(root, d))]
                    if SCAN_STATUS["interrupt"]: break
                    
                    folder_count += 1
                    short_path = root if len(root) < 45 else "..." + root[-42:]
                    yield f_data({"log": f"フォルダ走査中... {folder_count}件走査 - {short_path}"})
                    
                    # 同一親フォルダ(root)の直下にある子フォルダ(dirs)が2つ以上ある場合のみ処理
                    if len(dirs) < 2: continue
                    
                    clean_groups = defaultdict(list)
                    for d_name in dirs:
                        # カッコ除去 (1), （１）, .1), [1], 【1】 など
                        c = re.sub(r'\s*[\[\(（【\.][^\]\)）】]*[\]\)）】]\s*', '', d_name)
                        # 連番除去 _1, -1
                        c = re.sub(r'[\s_-]+\d+$', '', c).strip().lower()
                        if c:
                            clean_groups[c].append(d_name)
                            
                    # 同一親の直下で、クリーン名が重複する子フォルダのグループを処理
                    for c_name, sub_dirs in clean_groups.items():
                        if len(sub_dirs) < 2: continue
                        if len(sub_dirs) > 30: continue # 安全弁
                        
                        # トーナメントで最も「残す側(dst)」にふさわしいフォルダを選ぶ
                        best_dst_name = sub_dirs[0]
                        for idx in range(1, len(sub_dirs)):
                            candidate_name = sub_dirs[idx]
                            path_best = os.path.join(root, best_dst_name)
                            path_cand = os.path.join(root, candidate_name)
                            _, winner_path = determine_merge_direction(path_best, path_cand)
                            best_dst_name = os.path.basename(winner_path)
                            
                        # ベストなマージ先（best_dst_name）に向けて、他のすべてのフォルダをマージするペアを作る
                        path_dst = os.path.normpath(os.path.join(root, best_dst_name))
                        for d_name in sub_dirs:
                            if d_name == best_dst_name: continue
                            
                            path_src = os.path.normpath(os.path.join(root, d_name))
                            if is_empty_dir_recursive(path_src):
                                continue
                            depth = len(path_dst.replace('\\', '/').split('/'))
                            
                            candidates.append({
                                "path_a": path_src,
                                "path_b": path_dst,
                                "ratio": 100,
                                "common": 0,
                                "name_match": True,
                                "rec_src": path_src,
                                "rec_dst": path_dst,
                                "depth": depth
                            })
                                
            # 階層の深さが浅い順（中階層優先）でソート
            candidates.sort(key=lambda x: x["depth"])
            final_candidates = candidates[:150]
            
            yield f_data({"log": "分析完了 (上位150件を表示中)", "done": True, "results": final_candidates})
            return
        finally:
            SCAN_STATUS["active"] = False
            gc.collect()

    folder_inventory = defaultdict(Counter); total = 0; folder_count = 0
    try:
        # 爆速化: ファイルサイズの分布をフォルダの「指紋」として記録
        for root_path in root_paths:
            for root, dirs, files in os.walk(root_path):
                # システムフォルダや同期用一時フォルダには侵入しない
                dirs[:] = [d for d in dirs if not is_system_or_temp_path(os.path.join(root, d))]
                if SCAN_STATUS["interrupt"]: break
                folder_count += 1
                curr = os.path.normpath(root)
                short_path = curr if len(curr) < 45 else "..." + curr[-42:]
                yield f_data({"log": f"分析中... {folder_count}フォルダ ({total}ファイル走査) - {short_path}"})
                for name in files:
                    total += 1
                    f_path = os.path.join(curr, name)
                    try:
                        if name.lower() in ['.ds_store', 'thumbs.db', 'desktop.ini']: continue
                        size = os.path.getsize(f_path)
                        if size > 0: # 0バイトの空ファイルだけは無視
                            # ファイル名は一切見ず、「サイズ」と「中身のハッシュ（先頭8KBのダイジェスト）」の両方を見る
                            p_md5 = calculate_partial_md5(f_path)
                            if p_md5:
                                folder_inventory[curr][(size, p_md5)] += 1
                    except: pass

        candidates = []
        f_list = list(folder_inventory.keys())
        
        for i in range(len(f_list)):
            p_a = f_list[i]; map_a = folder_inventory[p_a]
            len_a = sum(map_a.values())
            if len_a == 0: continue
            
            for j in range(i + 1, len(f_list)):
                if SCAN_STATUS["interrupt"]: break
                p_b = f_list[j]; map_b = folder_inventory[p_b]
                len_b = sum(map_b.values())
                if len_b == 0: continue
                
                # 親子関係は無視（マージ先のループ防止）
                if p_b.startswith(p_a + os.sep) or p_a.startswith(p_b + os.sep): continue
                
                # フォルダ名が「カッコ付き」かどうか等を確認（例: data と data (1)）
                a_name = os.path.basename(p_a)
                b_name = os.path.basename(p_b)
                # カッコ除去 (1), （１）, .1)
                clean_a = re.sub(r'\s*[\(（\.][^\)）]*[\)）]\s*', '', a_name)
                clean_b = re.sub(r'\s*[\(（\.][^\)）]*[\)）]\s*', '', b_name)
                # 連番除去 _1, -1
                clean_a = re.sub(r'[\s_-]+\d+$', '', clean_a).strip().lower()
                clean_b = re.sub(r'[\s_-]+\d+$', '', clean_b).strip().lower()
                # 完全に同名、またはカッコを除けば同名になる場合はフラグを立てる
                is_name_match = (clean_a == clean_b and clean_a != "")

                # 「同じサイズかつ同じハッシュ値を持つファイルデータ」がそれぞれいくつあるかを交差チェック
                common = map_a & map_b
                common_count = sum(common.values())
                
                # 共通ファイルがあるか、またはカッコ違い同名フォルダの場合は候補に入れる（中身不一致でも）
                if common_count > 0 or is_name_match:
                    ratio = int((common_count / min(len_a, len_b)) * 100) if min(len_a, len_b) > 0 else 0
                    candidates.append({
                        "path_a": p_a, 
                        "path_b": p_b, 
                        "ratio": ratio, 
                        "common": common_count,
                        "name_match": is_name_match
                    })

        # ソート順: カッコ違い（または同名）ペアを最優先で上に表示し、次に共通ファイル数、割合の順
        candidates.sort(key=lambda x: (x.get('name_match', False), x['common'], x['ratio']), reverse=True)
        final_candidates = candidates[:150] # 上位150件に絞る
        
        yield f_data({"log": "分析完了 (上位150件を表示中)", "done": True, "results": final_candidates})
        
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
    return jsonify({"success": True, "mode": os.environ.get("RUN_MODE", "LOCAL"), "pc": os.environ.get("COMPUTERNAME") or ""})

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
    stype = request.args.get("type", "local")
    raw_targets = json.loads(request.args.get("targets", "[]"))
    targets = [t.strip('"').strip("'").strip() for t in raw_targets if t]
    # JS側のパラメータ名 same_parent_only に合わせる
    same = request.args.get("same_parent_only") == "true"
    fast = request.args.get("fast_mode") == "true"
    if stype == "local": return Response(scan_local_duplicates_stream(targets, same, fast), mimetype='text/event-stream')
    if stype == "link_check": return Response(scan_local_links_stream(targets), mimetype='text/event-stream')
    return Response(scan_drive_duplicates_stream(targets), mimetype='text/event-stream')

@app.route("/api/analyze_folders")
def api_analyze_folders():
    raw_targets = json.loads(request.args.get("targets", "[]"))
    targets = [t.strip('"').strip("'").strip() for t in raw_targets if t]
    simple = request.args.get("simple_mode") == "true"
    return Response(analyze_local_folders(targets, simple), mimetype='text/event-stream')

def find_actual_path(path):
    """
    大文字小文字や NFC/NFD の表記ゆれを許容し、実際にディスク上に存在する正しいパスに変換して返す。
    存在しない場合は元のパスをそのまま返す。
    """
    if os.path.exists(path):
        return os.path.normpath(path)
        
    import unicodedata
    # パスをNFCに正規化
    path_nfc = unicodedata.normalize('NFC', path)
    if os.path.exists(path_nfc):
        return os.path.normpath(path_nfc)
        
    # NFDに正規化
    path_nfd = unicodedata.normalize('NFD', path)
    if os.path.exists(path_nfd):
        return os.path.normpath(path_nfd)
        
    # それでも見つからない場合は、親ディレクトリから大文字小文字/正規化ゆれで探索する
    normalized = os.path.normpath(path).replace('\\', '/')
    parts = normalized.split('/')
    
    current = parts[0] + '/' if parts[0].endswith(':') else parts[0] # Windowsドライブレター
    if not os.path.exists(current):
        return path # ドライブ自体が存在しなければ諦める
        
    for part in parts[1:]:
        if not part: continue
        next_path = os.path.join(current, part)
        if os.path.exists(next_path):
            current = next_path
            continue
            
        # 表記ゆれ探索
        try:
            items = os.listdir(current)
        except:
            return path # 読み込めなければ諦める
            
        part_nfc = unicodedata.normalize('NFC', part).lower()
        found = False
        for item in items:
            item_nfc = unicodedata.normalize('NFC', item).lower()
            if item_nfc == part_nfc:
                current = os.path.join(current, item)
                found = True
                break
        if not found:
            current = next_path
            
    return os.path.normpath(current)

def force_remove_dir(path):
    """読み取り専用属性などを解除して、空のディレクトリを強制的に削除する。"""
    import stat
    if not os.path.exists(path):
        return
    try:
        os.chmod(path, stat.S_IWRITE)
    except:
        pass
    os.rmdir(path)

def is_empty_dir_recursive(path):
    """ディレクトリとその配下が完全に空（ファイルが存在しない）であるか判定"""
    try:
        for root, dirs, files in os.walk(path):
            if files:
                return False
        return True
    except:
        return False

def is_system_or_temp_path(path):
    """システムフォルダや同期用の一時フォルダなどを判定して除外対象にする"""
    normalized = path.replace('\\', '/').lower()
    parts = normalized.split('/')
    
    exclude_names = {
        'node_modules', '.git', 'venv', '.venv', '__pycache__', 'env', 
        '.idea', '.vscode', 'appdata', 'temp', 'tmp', '.cache',
        '#recycle', '.synologyworkingdirectory', '.synologydrive',
        '.temporaryitems', 'system volume information'
    }
    
    for part in parts:
        if not part: continue
        # リストに完全一致するか、頭にドットがつく隠し/一時ファイル、または synology を含む
        if part in exclude_names or part.startswith('.') or 'synology' in part:
            return True
    return False

def merge_directories(src_dir, dst_dir):
    """src_dir の中身を dst_dir に再帰的にマージ移動する"""
    import shutil
    import unicodedata
    moved = 0
    errors = []
    
    # パス自体の大文字小文字や NFC/NFD の正規化を統一
    src_dir = find_actual_path(src_dir)
    dst_dir = find_actual_path(dst_dir)
    
    # src_dir と dst_dir が実質的に同じディレクトリの場合は何もしない（無限ループ防止）
    if src_dir.lower() == dst_dir.lower():
        logger.info(f"マージ元と先が同じフォルダのためスキップ: {src_dir}")
        return 0, []
        
    if not os.path.exists(dst_dir):
        try:
            os.makedirs(dst_dir, exist_ok=True)
            # 作成した後は正確なケース・センシティブなパスを再取得
            dst_dir = find_actual_path(dst_dir)
        except Exception as e:
            return 0, [f"フォルダ作成失敗 {dst_dir}: {str(e)}"]
        
    try:
        items = os.listdir(src_dir)
    except Exception as e:
        return 0, [f"フォルダ読み込み失敗 {src_dir}: {str(e)}"]

    # マージ先フォルダ内の既存ファイル・フォルダ名を把握 (大文字小文字・NFCを標準化)
    try:
        dst_items = os.listdir(dst_dir)
    except:
        dst_items = []
    existing_normalized = {unicodedata.normalize('NFC', x).lower() for x in dst_items}

    for item in items:
        # NFCに統一されたアイテム名
        item_nfc = unicodedata.normalize('NFC', item)
        item_key = item_nfc.lower()
        
        s = os.path.join(src_dir, item)
        d = os.path.join(dst_dir, item_nfc)
        
        try:
            if os.path.isdir(s):
                # 【防衛策1】 マージ先 d がディレクトリではなくファイルとして既に存在する場合、
                # そのファイルを退避（カッコ付き）させて、d がディレクトリになれるように道を切り開く
                if os.path.exists(d) and not os.path.isdir(d):
                    n, e = os.path.splitext(item_nfc)
                    c = 1
                    new_name = f"{n} ({c}){e}"
                    while os.path.exists(os.path.join(dst_dir, new_name)) or (new_name.lower() in existing_normalized):
                        c += 1
                        new_name = f"{n} ({c}){e}"
                    try:
                        shutil.move(d, os.path.join(dst_dir, new_name))
                        existing_normalized.add(new_name.lower())
                        if item_key in existing_normalized:
                            existing_normalized.remove(item_key)
                        logger.info(f"衝突ファイルを退避移動しました: {d} ➔ {new_name}")
                    except Exception as ex:
                        errors.append(f"衝突ファイルの退避失敗 {d}: {str(ex)}")
                        continue # 退避できなければ、このサブフォルダのマージはスキップ

                # 再帰的にマージ
                sub_moved, sub_errors = merge_directories(s, d)
                moved += sub_moved
                errors.extend(sub_errors)
                
                # 【防衛策2】 マージしたディレクトリ名を existing_normalized に追加し、
                # 後続の同名ファイルがこのディレクトリに衝突・誤吸入されるのを防ぐ
                existing_normalized.add(item_key)
            else:
                # ファイル移動の衝突回避
                d_path = d
                # すでに存在するファイル/ディレクトリ名と衝突するか確認
                if item_key in existing_normalized:
                    n, e = os.path.splitext(item_nfc)
                    c = 1
                    new_name = f"{n} ({c}){e}"
                    while (new_name.lower() in existing_normalized) or os.path.exists(os.path.join(dst_dir, new_name)):
                        c += 1
                        new_name = f"{n} ({c}){e}"
                    d_path = os.path.join(dst_dir, new_name)
                    existing_normalized.add(new_name.lower())
                else:
                    # 【防衛策3】 existing_normalized には入っていないが、万が一ディスク上に
                    # 同名のディレクトリまたはファイルとして実在する場合の最終チェック
                    if os.path.exists(d):
                        n, e = os.path.splitext(item_nfc)
                        c = 1
                        new_name = f"{n} ({c}){e}"
                        while (new_name.lower() in existing_normalized) or os.path.exists(os.path.join(dst_dir, new_name)):
                            c += 1
                            new_name = f"{n} ({c}){e}"
                        d_path = os.path.join(dst_dir, new_name)
                        existing_normalized.add(new_name.lower())
                    else:
                        existing_normalized.add(item_key)
                    
                shutil.move(s, d_path)
                moved += 1
        except Exception as e:
            errors.append(f"{item}: {str(e)}")
            
    try:
        if os.path.exists(src_dir) and not os.listdir(src_dir):
            force_remove_dir(src_dir)
    except:
        pass
        
    return moved, errors

@app.route("/api/drive/check_existence", methods=["POST"])
def api_drive_check_existence():
    """
    指定されたGoogle Driveファイルのリストに対して、実物が各アカウントに存在するかチェックする。
    並列処理を用いて超高速にチェックを完了させる。
    """
    try:
        req_data = request.json or {}
        files = req_data.get("files", [])
        
        logger.info(f"並列Driveファイル存在確認要求を受信: {len(files)}件")
        
        results = {}
        if not files:
            return jsonify({"success": True, "results": {}})
            
        def check_single_file(f_info):
            fid = f_info.get("id")
            account_email = f_info.get("account")
            if not fid:
                return fid, False
                
            target_accounts = []
            if account_email:
                acc = next((a for a in drive_connector.accounts if a.email == account_email), None)
                if acc:
                    target_accounts.append(acc)
            
            for acc in drive_connector.accounts:
                if acc not in target_accounts:
                    target_accounts.append(acc)
                    
            import requests
            from google.auth.transport.requests import Request
            
            for acc in target_accounts:
                try:
                    creds = acc.credentials
                    if not creds.valid:
                        creds.refresh(Request())
                    
                    token = creds.token
                    headers = {"Authorization": f"Bearer {token}"}
                    url = f"https://www.googleapis.com/drive/v3/files/{fid}"
                    params = {
                        "fields": "id, trashed",
                        "supportsAllDrives": "true"
                    }
                    
                    resp = requests.get(url, headers=headers, params=params, timeout=5)
                    if resp.status_code == 200:
                        res = resp.json()
                        if not res.get("trashed", False):
                            return fid, True
                    elif resp.status_code == 404:
                        continue
                except Exception as e:
                    logger.warning(f"並列通信エラー ({acc.email}): {e}")
            return fid, False

        from concurrent.futures import ThreadPoolExecutor
        # 最大25スレッドで並列処理
        with ThreadPoolExecutor(max_workers=25) as executor:
            task_results = list(executor.map(check_single_file, files))
            
        for fid, exists in task_results:
            if fid:
                results[fid] = exists
                
        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Drive存在確認で致命的エラー: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/drive/check_existence_stream", methods=["POST"])
def api_drive_check_existence_stream():
    """
    ファイルを並列検証し、結果をリアルタイムにSSEストリームで返却する（POST形式）。
    """
    try:
        req_data = request.json or {}
        targets = req_data.get("targets", [])
    except Exception:
        targets = []
        
    def generator():
        if not targets:
            yield f"data: {json.dumps({'done': True})}\n\n"
            return
            
        logger.info(f"並列Drive存在確認(ストリーム)開始: {len(targets)}件")
        
        # 1件検証するタスク
        def check_single(f_info):
            fid = f_info.get("id")
            account_email = f_info.get("account")
            if not fid:
                return fid, False
                
            target_accounts = []
            if account_email:
                acc = next((a for a in drive_connector.accounts if a.email == account_email), None)
                if acc:
                    target_accounts.append(acc)
            for acc in drive_connector.accounts:
                if acc not in target_accounts:
                    target_accounts.append(acc)
                    
            import time
            for acc in target_accounts:
                try:
                    creds = acc.session.credentials
                    if not creds.valid:
                        continue
                    
                    url = f"https://www.googleapis.com/drive/v3/files/{fid}"
                    params = {
                        "fields": "id, trashed",
                        "supportsAllDrives": "true"
                    }
                    
                    # 429 Too Many Requests に対する指数バックオフ付きリトライ
                    for attempt in range(3):
                        resp = acc.session.get(url, params=params, timeout=5)
                        if resp.status_code == 200:
                            res = resp.json()
                            if not res.get("trashed", False):
                                return fid, True
                            break
                        elif resp.status_code == 404:
                            break
                        elif resp.status_code == 429:
                            logger.warning(f"Google API レートリミット(429)を検出しました。リトライします ({attempt + 1}/3)...")
                            time.sleep(2 ** attempt)
                            continue
                        else:
                            break
                except Exception as e:
                    logger.warning(f"並列通信エラー ({acc.email}): {e}")
            return fid, False

        # スレッドセーフ確保のため、スレッドプールに入る前にすべてのアカウントの資格情報をメインスレッドでリフレッシュしておく
        from google.auth.transport.requests import Request
        for acc in drive_connector.accounts:
            try:
                creds = acc.session.credentials
                if not creds.valid:
                    logger.info(f"メインスレッドでトークンをリフレッシュします: {acc.email}")
                    creds.refresh(Request())
            except Exception as e:
                logger.error(f"アカウント {acc.email} のトークンリフレッシュに失敗: {e}")

        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        # 25スレッドで並列処理し、終わった順に yield する
        with ThreadPoolExecutor(max_workers=25) as executor:
            future_to_fid = {executor.submit(check_single, f): f.get("id") for f in targets if f.get("id")}
            
            completed_count = 0
            for future in as_completed(future_to_fid):
                fid = future_to_fid[future]
                try:
                    res_fid, exists = future.result()
                    completed_count += 1
                    yield f"data: {json.dumps({'fid': res_fid, 'exists': exists, 'progress': completed_count, 'total': len(targets)})}\n\n"
                except Exception as e:
                    logger.error(f"ファイル {fid} の検証中にエラー: {e}")
                    completed_count += 1
                    yield f"data: {json.dumps({'fid': fid, 'exists': False, 'progress': completed_count, 'total': len(targets)})}\n\n"
                    
        yield f"data: {json.dumps({'done': True})}\n\n"
        
    return Response(generator(), mimetype='text/event-stream')

@app.route("/api/merge_folders", methods=["POST"])
def api_merge_folders():
    try:
        d = request.json
        raw_src = d.get("src")
        raw_dst = d.get("dst")
        
        logger.info(f"フォルダマージ要求を受信: {raw_src} ➔ {raw_dst}")
        
        src = find_actual_path(raw_src)
        dst = find_actual_path(raw_dst)
        
        logger.info(f"実在パス解決結果: {src} ➔ {dst}")
        
        if not src or not dst: 
            logger.warning("マージ失敗: パスが未指定です。")
            return jsonify({"success": False, "error": "パスが未指定です。"})
        if not os.path.exists(src): 
            logger.warning(f"マージ失敗: 元フォルダが見つかりません: {src}")
            return jsonify({"success": False, "error": f"元フォルダが見つかりません: {src}"})
        if not os.path.exists(dst): 
            logger.warning(f"マージ失敗: 先フォルダが見つかりません: {dst}")
            return jsonify({"success": False, "error": f"先フォルダが見つかりません: {dst}"})
            
        # 安全ガード: システムフォルダや同期用一時フォルダのマージを拒否
        if is_system_or_temp_path(src) or is_system_or_temp_path(dst):
            logger.warning(f"マージ拒否: システム/同期一時フォルダは処理できません: {src} or {dst}")
            return jsonify({"success": False, "error": "システムフォルダや同期用一時フォルダはマージできません。"})
        
        logger.info(f"再帰的マージを開始します...")
        moved_count, errors = merge_directories(src, dst)
        logger.info(f"マージ結果: 移動完了={moved_count}件, エラー={len(errors)}件")
        
        try:
            if os.path.exists(src) and not os.listdir(src):
                force_remove_dir(src)
                logger.info(f"空になった元フォルダを削除しました: {src}")
        except Exception as ex:
            logger.warning(f"元フォルダ削除失敗 {src}: {ex}")
            
        if errors:
            logger.warning(f"マージ中に一部エラーが発生しました:\n" + "\n".join(errors))
            return jsonify({"success": True, "partial_error": True, "error": "\n".join(errors[:3]), "moved": moved_count})
        
        logger.info("マージ完了: 成功")
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

class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]

def send_to_recycle_bin(path):
    """Windows APIを使ってファイルをPCのごみ箱に移動する"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"ファイルが見つかりません: {path}")
    
    # パスを正規化してWindowsのバックスラッシュにする
    path_normalized = os.path.abspath(path).replace('/', '\\')
    # SHFileOperationW の仕様として、pFromはダブルヌルで終わる必要がある
    p_from = path_normalized + "\0\0"
    
    # SHFileOperationの定数
    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040       # ごみ箱に送る
    FOF_NOCONFIRMATION = 0x0010  # 確認ダイアログを表示しない
    FOF_NOERRORUI = 0x0400       # エラーUIを表示しない
    FOF_SILENT = 0x0004          # 進行状況ダイアログを表示しない
    
    fileop = SHFILEOPSTRUCTW()
    fileop.hwnd = None
    fileop.wFunc = FO_DELETE
    fileop.pFrom = p_from
    fileop.pTo = None
    fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(fileop))
    if result != 0:
        raise OSError(f"ごみ箱への移動に失敗しました (エラーコード: {result})")

@app.route("/api/open_cleanup_folder")
def api_open_cleanup_folder():
    """ごみ箱フォルダを開く"""
    try:
        subprocess.Popen(['explorer', 'shell:RecycleBinFolder'])
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"ごみ箱のオープンに失敗しました: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/move_to_cleanup", methods=["POST"])
def api_move_to_cleanup():
    req_data = request.json or {}
    files = req_data.get("files", [])
    scan_type = req_data.get("scan_type", "local")
    
    # 後方互換性の確保
    if not files and "file_ids" in req_data:
        files = [{"id": fid, "account": None} for fid in req_data.get("file_ids", [])]

    for f_info in files:
        fid = f_info.get("id")
        account_email = f_info.get("account")
        
        if not fid: continue
        
        # スキャンタイプが「Driveリンク確認（link_check）」である場合、
        # またはパス形式である場合は、ローカルファイルとしてごみ箱へ移動する
        is_local = (scan_type == "link_check") or '\\' in fid or '/' in fid or os.path.exists(fid)
        
        if is_local:
            # ローカルファイルをごみ箱へ移動する
            if os.path.exists(fid):
                try:
                    send_to_recycle_bin(fid)
                    logger.info(f"ローカルファイルをごみ箱に移動しました: {fid}")
                except Exception as e:
                    logger.error(f"ローカルファイルのごみ箱移動に失敗しました {fid}: {e}")
            else:
                logger.warning(f"移動対象のローカルファイルが見つかりません: {fid}")
        else:
            # Google Drive上の本物のファイルの場合
            if account_email:
                acc = next((a for a in drive_connector.accounts if a.email == account_email), None)
                if acc:
                    try:
                        acc.service.files().update(
                            fileId=fid,
                            body={'trashed': True},
                            supportsAllDrives=True
                        ).execute()
                        logger.info(f"Driveファイルをゴミ箱に移動しました: {fid} ({account_email})")
                    except Exception as e:
                        logger.error(f"Driveファイルのゴミ箱移動に失敗しました {fid} ({account_email}): {e}")
                else:
                    logger.warning(f"対応するDriveアカウントが見つかりません: {account_email}")
            else:
                # アカウント未指定の場合、全アカウントでゴミ箱移動を試みる
                success = False
                for acc in drive_connector.accounts:
                    try:
                        acc.service.files().update(
                            fileId=fid,
                            body={'trashed': True},
                            supportsAllDrives=True
                        ).execute()
                        logger.info(f"Driveファイルをゴミ箱に移動しました (フォールバック): {fid} ({acc.email})")
                        success = True
                        break
                    except Exception:
                        pass
                if not success:
                    logger.error(f"Google Drive上のファイルゴミ箱移動に失敗しました: {fid}")

    return jsonify({"success": True})

@app.route("/api/stop_scan", methods=["POST"])
def api_stop_scan(): SCAN_STATUS["interrupt"] = True; return jsonify({"success": True})

@app.route("/api/local_preview")
def local_preview():
    p = request.args.get("path")
    if not p or not os.path.isfile(p): return "Not Found", 404
    return send_file(p)

def scan_drive_duplicates_stream(folder_ids=None):
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    accounts = drive_connector.accounts
    if not accounts:
        yield f_data({"log": "Driveアカウントが未設定です。アカウントを追加してください。", "done": True, "results": []})
        SCAN_STATUS["active"] = False; return
    services = {acc.email: acc.service for acc in accounts}   # listing用（シングルスレッド）
    sessions = {acc.email: acc.session for acc in accounts}   # export用（スレッドセーフ）

    all_f = []
    try:
        # フェーズ1: 全アカウントからメタデータ取得
        for acc in accounts:
            if SCAN_STATUS["interrupt"]: break
            if folder_ids:
                # サブフォルダも含めて再帰収集
                yield f_data({"log": f"[{acc.email}] サブフォルダを再帰収集中..."})
                all_folder_ids = _collect_all_folder_ids(acc.service, folder_ids)
                yield f_data({"log": f"[{acc.email}] {len(all_folder_ids)}フォルダを対象にファイル取得中..."})
                files = _list_files_in_folders(acc.service, all_folder_ids)
            else:
                files = []
                pt = None
                while True:
                    if SCAN_STATUS["interrupt"]: break
                    res = acc.service.files().list(
                        q="trashed=false and mimeType != 'application/vnd.google-apps.folder'",
                        fields='nextPageToken, files(id, name, mimeType, size, md5Checksum, quotaBytesUsed, createdTime, webViewLink)',
                        pageToken=pt
                    ).execute()
                    files.extend(res.get('files', []))
                    yield f_data({"log": f"[{acc.email}] {len(files)}件取得中..."})
                    pt = res.get('nextPageToken')
                    if not pt: break
            for f in files:
                f['account'] = acc.email
            all_f.extend(files)
            yield f_data({"log": f"[{acc.email}] 取得完了: {len(files)}件"})

        # フェーズ2: 一般ファイルはメタデータで分類、ネイティブは別リストへ
        gr = defaultdict(list)
        native_files = []
        for f in all_f:
            s, m, n = f.get('size'), f.get('md5Checksum'), f.get('name')
            mime = f.get('mimeType', '')
            if not n: continue
            if s and m and int(s) > 0:  # 0バイトは除外
                ext = os.path.splitext(n)[1].lower()
                gr[(s, m, ext)].append(f)
            elif mime in GOOGLE_EXPORT_MIME:
                native_files.append(f)

        # フェーズ2.5: quotaBytesUsed で事前フィルタ（容量が一致するものだけエクスポート候補に残す）
        quota_groups = defaultdict(list)
        for f in native_files:
            key = (f.get('mimeType', ''), f.get('quotaBytesUsed', '0') or '0')
            quota_groups[key].append(f)
        native_files_filtered = [f for g in quota_groups.values() if len(g) > 1 for f in g]
        skipped = len(native_files) - len(native_files_filtered)
        yield f_data({"log": f"容量フィルタ: {len(native_files)}件 → {len(native_files_filtered)}件（{skipped}件をスキップ）"})

        # フェーズ3: 種別ごとに2段階スクリーニング（並列エクスポート）
        WORKERS = 10

        prescreen_groups = defaultdict(lambda: defaultdict(list))
        no_prescreen = [f for f in native_files_filtered if f.get('mimeType') not in GOOGLE_PRESCREEN_MIME]

        for mime_type in GOOGLE_PRESCREEN_MIME:
            targets = [f for f in native_files_filtered if f.get('mimeType') == mime_type]
            if not targets: continue
            prescreen_mime = GOOGLE_PRESCREEN_MIME[mime_type]
            label = {'text/csv': 'CSV', 'text/plain': 'テキスト'}.get(prescreen_mime, prescreen_mime)
            type_label = mime_type.split('.')[-1].capitalize()
            yield f_data({"log": f"{type_label} {len(targets)}件を{label}で事前スクリーニング中..."})

            completed = 0
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futures = {ex.submit(_export_md5, sessions[f['account']], f['id'], prescreen_mime): f for f in targets}
                for future in as_completed(futures):
                    if SCAN_STATUS["interrupt"]: break
                    f = futures[future]
                    md5 = future.result()
                    if md5:
                        prescreen_groups[mime_type][md5].append(f)
                    completed += 1
                    if completed % 10 == 0 or completed == len(targets):
                        prog = int(completed / len(targets) * 100)
                        yield f_data({"log": f"{label}スクリーニング中... {prog}% ({completed}/{len(targets)})"})

            candidates = [f for g in prescreen_groups[mime_type].values() if len(g) > 1 for f in g]
            total_c = len(candidates)
            full_mime = GOOGLE_EXPORT_MIME[mime_type]
            yield f_data({"log": f"{label}一致 {total_c}件を本格エクスポートで最終確認中..."})
            if candidates:
                completed = 0
                with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                    futures = {ex.submit(_export_md5, sessions[f['account']], f['id'], full_mime): f for f in candidates}
                    for future in as_completed(futures):
                        if SCAN_STATUS["interrupt"]: break
                        f = futures[future]
                        md5 = future.result()
                        if md5:
                            gr[(md5, mime_type)].append(f)
                        completed += 1
                        if completed % 5 == 0 or completed == total_c:
                            prog = int(completed / total_c * 100)
                            yield f_data({"log": f"本格エクスポート中... {prog}% ({completed}/{total_c})"})

        if no_prescreen:
            yield f_data({"log": f"Drawing等 {len(no_prescreen)}件をエクスポート中..."})
            completed = 0
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futures = {ex.submit(_export_md5, sessions[f['account']], f['id'], GOOGLE_EXPORT_MIME[f['mimeType']]): f for f in no_prescreen}
                for future in as_completed(futures):
                    if SCAN_STATUS["interrupt"]: break
                    f = futures[future]
                    md5 = future.result()
                    if md5:
                        gr[(md5, f['mimeType'])].append(f)
                    completed += 1
                    if completed % 5 == 0 or completed == len(no_prescreen):
                        prog = int(completed / len(no_prescreen) * 100)
                        yield f_data({"log": f"エクスポート中... {prog}% ({completed}/{len(no_prescreen)})"})

        # 結果構築
        yield f_data({"log": "結果を整理中..."})
        try:
            full_match_ids = {f['id'] for v in gr.values() if len(v) > 1 for f in v}
            first_only_results = []
            for mime_type, groups in prescreen_groups.items():
                for group in groups.values():
                    if len(group) < 2: continue
                    first_only = [f for f in group if f['id'] not in full_match_ids]
                    if len(first_only) >= 2:
                        first_only_results.append({
                            'size': 0, 'files': first_only,
                            'main_name': first_only[0]['name'],
                            'match_type': 'first_sheet'
                        })
            results = [
                {'size': int(v[0].get('size', 0) or 0), 'files': v,
                 'main_name': v[0]['name'], 'match_type': 'full'}
                for v in gr.values() if len(v) > 1
            ] + first_only_results
            yield f_data({"log": "完了", "done": True, "results": results})
        except Exception as e:
            logger.error(f"結果構築失敗: {e}", exc_info=True)
            yield f_data({"log": f"エラー: {e}", "done": True, "results": []})
    except Exception as e:
        logger.error(f"スキャン失敗: {e}", exc_info=True)
        yield f_data({"log": f"エラー: {e}", "done": True, "results": []})
    finally:
        SCAN_STATUS["active"] = False

def parse_google_link_file(file_path):
    """
    .gsheet などのローカルリンクファイルから Drive ID, Web URL, アカウント(Email)を抽出する。
    """
    import json
    import re
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        
        doc_id = data.get("doc_id") or data.get("id")
        url = data.get("url")
        email = data.get("email")
        
        if url and not doc_id:
            m = re.search(r'id=([a-zA-Z0-9-_]+)', url)
            if m:
                doc_id = m.group(1)
                
        return doc_id, url, email
    except Exception:
        return None, None, None

def scan_local_links_stream(root_paths):
    """
    指定されたローカルフォルダから .gsheet などのリンクファイルを収集してストリーム返却する。
    """
    SCAN_STATUS["active"] = True; SCAN_STATUS["interrupt"] = False
    files_found = []
    
    try:
        for root_path in root_paths:
            yield f"data: {json.dumps({'log': f'リンクファイル走査中: {root_path}'})}\n\n"
            norm_root = os.path.normpath(root_path)
            stack = [root_path]
            
            while stack:
                if SCAN_STATUS["interrupt"]: break
                curr_path = stack.pop()
                try:
                    with os.scandir(curr_path) as it:
                        for entry in it:
                            if SCAN_STATUS["interrupt"]: break
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                name = entry.name
                                ext = os.path.splitext(name)[1].lower()
                                if ext in {'.gsheet', '.gdoc', '.gslides', '.gdraw', '.gtable'}:
                                    file_path = os.path.normpath(entry.path)
                                    drive_id, web_url, email = parse_google_link_file(file_path)
                                    stat = entry.stat()
                                    
                                    meta = {
                                        "id": file_path,
                                        "name": name,
                                        "path": file_path,
                                        "size": stat.st_size,
                                        "createdTime": stat.st_ctime,
                                        "drive_id": drive_id,
                                        "webViewLink": web_url or (f"https://docs.google.com/open?id={drive_id}" if drive_id else None),
                                        "account": email
                                    }
                                    files_found.append(meta)
                                    if len(files_found) % 50 == 0:
                                        yield f"data: {json.dumps({'log': f'リンクファイル {len(files_found)}件発見'})}\n\n"
                except Exception:
                    pass
        
        yield f"data: {json.dumps({'done': True, 'results': files_found})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'log': f'エラー発生: {str(e)}', 'done': True, 'results': []})}\n\n"
    finally:
        SCAN_STATUS["active"] = False


@app.route("/api/move_to_account_folders", methods=["POST"])
def api_move_to_account_folders():
    """
    指定されたファイルを、それぞれの紐付けアカウント（メールアドレス）名のフォルダ配下へ移動する。
    例: M:\doublecheck1234567890\... -> M:\[email]\doublecheck1234567890\...
    """
    try:
        req_data = request.json or {}
        files = req_data.get("files", [])
        
        logger.info(f"アカウント別フォルダ仕分け移動要求を受信: {len(files)}件")
        
        moved_ids = []
        errors = []
        
        for f_info in files:
            fid = f_info.get("id")
            account_email = f_info.get("account")
            
            if not fid or not account_email: continue
            if not os.path.exists(fid):
                errors.append(f"{fid}: ファイルが見つかりません。")
                continue
                
            try:
                # 移動先パスの計算
                drive, rest = os.path.splitdrive(fid)
                rest_clean = rest.lstrip('\\/')
                dest_path = os.path.join(drive, os.sep, account_email, rest_clean)
                dest_path = os.path.normpath(dest_path)
                
                # 移動先親フォルダの作成
                dest_dir = os.path.dirname(dest_path)
                os.makedirs(dest_dir, exist_ok=True)
                
                # 同名衝突の回避
                final_dest = dest_path
                if os.path.exists(final_dest):
                    base, ext = os.path.splitext(dest_path)
                    counter = 1
                    while True:
                        new_dest = f"{base} ({counter}){ext}"
                        if not os.path.exists(new_dest):
                            final_dest = new_dest
                            break
                        counter += 1
                
                # ファイル移動
                import shutil
                shutil.move(fid, final_dest)
                logger.info(f"アカウント仕分け移動完了: {fid} ➔ {final_dest}")
                moved_ids.append(fid)
            except Exception as e:
                logger.error(f"アカウント仕分け移動に失敗しました {fid}: {e}")
                errors.append(f"{fid}: {str(e)}")
                
        return jsonify({"success": True, "moved_ids": moved_ids, "errors": errors})
    except Exception as e:
        logger.error(f"アカウント仕分け移動で致命的エラー: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/drive/batch_grant_permissions", methods=["POST"])
def api_drive_batch_grant_permissions():
    try:
        req_data = request.json or {}
        files = req_data.get("files", [])
        target_account = req_data.get("target_account")
        
        if not target_account:
            return jsonify({"success": False, "error": "target_accountが指定されていません。"})
            
        success_ids = []
        errors = []
        
        user_permission = {
            'type': 'user',
            'role': 'reader',
            'emailAddress': target_account
        }
        
        logger.info(f"一括権限付与要求を受信: {len(files)}件, ターゲット: {target_account}")
        
        for f_info in files:
            fid = f_info.get("id")
            source_account = f_info.get("source_account")
            if not fid: continue
            
            logger.info(f"ファイル権限付与処理中: ID={fid}, 指定ソース={source_account}")
            
            # 1. ターゲットアカウント自身がすでにアクセスできるかチェック
            acc_target = next((a for a in drive_connector.accounts if a.email == target_account), None)
            if acc_target:
                try:
                    acc_target.service.files().get(fileId=fid, fields="id", supportsAllDrives=True).execute()
                    logger.info(f"ターゲットアカウント {target_account} はすでにアクセス可能です: {fid}")
                    success_ids.append(fid)
                    continue
                except Exception:
                    pass # アクセスできないので権限付与が必要
            
            # 2. 共有権限を付与できる「親アカウント（共有元）」を探す
            acc_src = None
            
            # 指定された source_account が有効かまず確認
            if source_account and source_account != target_account:
                a_candidate = next((a for a in drive_connector.accounts if a.email == source_account), None)
                if a_candidate:
                    try:
                        a_candidate.service.files().get(fileId=fid, fields="id", supportsAllDrives=True).execute()
                        acc_src = a_candidate
                        logger.info(f"指定ソースアカウント {source_account} のアクセスを確認しました: {fid}")
                    except Exception:
                        logger.info(f"指定ソースアカウント {source_account} からはアクセスできません: {fid}")
            
            # 指定ソースアカウントが使えない場合、他のアカウントからアクセス可能なものを探す
            if not acc_src:
                logger.info(f"アクセス可能な代替アカウントを探索中: {fid}")
                for a in drive_connector.accounts:
                    if a.email == target_account: continue
                    if a.email == source_account: continue # すでに確認済み
                    try:
                        a.service.files().get(fileId=fid, fields="id", supportsAllDrives=True).execute()
                        acc_src = a
                        logger.info(f"代替アカウント {a.email} でアクセス可能であることを確認しました: {fid}")
                        break
                    except Exception:
                        continue
            
            if not acc_src:
                logger.warning(f"ファイルに対してアクセス権を持つ登録済みアカウントが見つかりません: {fid}")
                errors.append({"id": fid, "error": "ファイルに対してアクセス権を持つ連携アカウントが見つかりません。"})
                continue
                
            # 3. 見つかったアカウントからターゲットアカウントへ共有権限を付与
            try:
                acc_src.service.permissions().create(
                    fileId=fid,
                    body=user_permission,
                    fields='id',
                    supportsAllDrives=True
                ).execute()
                logger.info(f"権限付与成功: ID={fid} (From {acc_src.email} To {target_account})")
                success_ids.append(fid)
            except Exception as e:
                logger.error(f"権限の付与に失敗しました {fid} (From {acc_src.email} To {target_account}): {e}")
                errors.append({"id": fid, "error": str(e)})
                
        return jsonify({"success": True, "granted_ids": success_ids, "errors": errors})
    except Exception as e:
        logger.error(f"一括権限付与で致命的エラー: {e}")
        return jsonify({"success": False, "error": str(e)})


def get_or_create_drive_folder(service, name, parent_id="root"):
    query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{name}' and trashed = false"
    if parent_id == "root":
        query += " and 'root' in parents"
    else:
        query += f" and '{parent_id}' in parents"
        
    res = service.files().list(q=query, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
        
    folder_metadata = {
        'name': name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id != "root":
        folder_metadata['parents'] = [parent_id]
        
    folder = service.files().create(body=folder_metadata, fields='id').execute()
    return folder.get('id')


@app.route("/api/drive/batch_open_and_cleanup", methods=["POST"])
def api_drive_batch_open_and_cleanup():
    """
    ブラウザでファイルを開いた後のクリーンアップ処理。
    GoogleドキュメントはブラウザでURLを開くだけでGoogleが自動的にマイドライブへ保存する。
    このAPIはそれ以外の後処理（ローカルのショートカットファイルの削除）だけを担当する。
    """
    try:
        req_data = request.json or {}
        files = req_data.get("files", [])
        target_account = req_data.get("target_account")

        success_ids = []
        errors = []

        for f_info in files:
            fid = f_info.get("id")
            ui_id = f_info.get("ui_id") or fid
            local_path = f_info.get("path")
            if not fid:
                continue

            if local_path:
                local_path = local_path.strip('"').strip("'").strip()

            try:
                # ローカルのショートカットファイルをゴミ箱へ移動する
                if local_path and os.path.exists(local_path):
                    send_to_recycle_bin(local_path)
                    logger.info(f"ローカルファイルをゴミ箱へ移動しました: {local_path}")

                success_ids.append(ui_id)
            except Exception as e:
                logger.error(f"ローカルファイルの削除に失敗しました {local_path}: {e}")
                errors.append({"ui_id": ui_id, "id": fid, "error": str(e)})

        return jsonify({"success": True, "success_ids": success_ids, "errors": errors})

    except Exception as e:
        logger.error(f"クリーンアップで致命的エラー: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/drive/accounts")
def api_drive_accounts():
    return jsonify({"accounts": drive_connector.list_accounts()})

@app.route("/api/drive/add_account", methods=["POST"])
def api_drive_add_account():
    try:
        email = drive_connector.add_account()
        return jsonify({"success": True, "email": email})
    except Exception as e:
        logger.error(f"アカウント追加失敗: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/drive/remove_account", methods=["POST"])
def api_drive_remove_account():
    email = request.json.get("email")
    ok = drive_connector.remove_account(email)
    return jsonify({"success": ok})


@app.route("/api/drive/scan_orphaned", methods=["POST"])
def api_drive_scan_orphaned():
    """
    マイドライブのルートから辿れない「孤立ファイル」を検索する。
    手順:
      1. マイドライブのルートから全フォルダIDを再帰的に収集する
      2. アカウント内の全ファイルを取得する
      3. どのフォルダにも属していないファイルを返す
    """
    try:
        req_data = request.json or {}
        account_email = req_data.get("account")

        if not account_email:
            return jsonify({"success": False, "error": "accountが指定されていません。"})

        acc = next((a for a in drive_connector.accounts if a.email == account_email), None)
        if not acc:
            return jsonify({"success": False, "error": f"アカウントが見つかりません: {account_email}"})

        service = acc.service

        # Step 1: マイドライブおよびマイコンピュータの全フォルダIDを再帰収集
        logger.info(f"[孤立ファイル検索] {account_email}: フォルダ収集開始")
        reachable_folder_ids = set()
        queue = ["root"]

        # 同期されている「マイ コンピュータ」などのルートフォルダも検索開始点に追加する
        try:
            pt_f = None
            all_folders = []
            while True:
                res_f = service.files().list(
                    q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                    fields="nextPageToken, files(id, name, parents)",
                    pageToken=pt_f,
                    pageSize=1000
                ).execute()
                all_folders.extend(res_f.get("files", []))
                pt_f = res_f.get("nextPageToken")
                if not pt_f:
                    break
            
            for f in all_folders:
                if not f.get("parents"):
                    name = f.get("name", "")
                    if any(kw in name for kw in ["コンピュータ", "パソコン", "Computer", "Laptop", "Desktop", "My PC"]):
                        logger.info(f"[孤立ファイル検索] PC同期ルートを追加: {name} ({f['id']})")
                        queue.append(f["id"])
                        reachable_folder_ids.add(f["id"])
        except Exception as e:
            logger.error(f"[孤立ファイル検索] PC同期ルートの取得に失敗: {e}")

        while queue:
            batch, queue = queue[:30], queue[30:]
            conditions = " or ".join(f"'{fid}' in parents" for fid in batch)
            q = f"trashed=false and mimeType='application/vnd.google-apps.folder' and ({conditions})"
            pt = None
            while True:
                res = service.files().list(
                    q=q,
                    fields="nextPageToken, files(id)",
                    pageToken=pt,
                    pageSize=1000
                ).execute()
                for f in res.get("files", []):
                    if f["id"] not in reachable_folder_ids:
                        reachable_folder_ids.add(f["id"])
                        queue.append(f["id"])
                pt = res.get("nextPageToken")
                if not pt:
                    break

        # rootも辿れるフォルダとして追加
        reachable_folder_ids.add("root")
        logger.info(f"[孤立ファイル検索] 辿れるフォルダ数: {len(reachable_folder_ids)}")

        # Step 2: 自分がオーナーのファイル（フォルダ以外）のみ取得
        # ※ 他人から共有されたファイルは「共有アイテム」であり孤立ではないため除外する
        all_files = []
        q = "trashed=false and mimeType != 'application/vnd.google-apps.folder' and 'me' in owners"
        pt = None
        while True:
            res = service.files().list(
                q=q,
                fields="nextPageToken, files(id, name, mimeType, parents, webViewLink, modifiedTime, owners)",
                pageToken=pt,
                pageSize=1000
            ).execute()
            all_files.extend(res.get("files", []))
            pt = res.get("nextPageToken")
            if not pt:
                break

        logger.info(f"[孤立ファイル検索] 自分がオーナーのファイル数: {len(all_files)}")

        # Step 3: 親フォルダがどれもマイドライブから辿れない = 孤立ファイル
        orphaned = []
        for f in all_files:
            parents = f.get("parents", [])
            if not parents:
                # 親が全くない = 孤立
                orphaned.append(f)
            else:
                # 親フォルダのうち、少なくとも1つでもrootから辿れるものがあれば正常
                if not any(p in reachable_folder_ids for p in parents):
                    orphaned.append(f)

        logger.info(f"[孤立ファイル検索] 孤立ファイル数: {len(orphaned)}")
        return jsonify({"success": True, "orphaned_files": orphaned, "total_scanned": len(all_files)})

    except Exception as e:
        logger.error(f"孤立ファイル検索で致命的エラー: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/drive/salvage_orphaned", methods=["POST"])
def api_drive_salvage_orphaned():
    """
    孤立ファイルをマイドライブのルートへ移動する。
    files.update で addParents="root" を指定するだけ。
    コピーは一切しない。ファイルIDも変わらない。
    """
    try:
        req_data = request.json or {}
        account_email = req_data.get("account")
        file_ids = req_data.get("file_ids", [])  # [{"id": "xxx", "parents": ["yyy"]}, ...]

        if not account_email:
            return jsonify({"success": False, "error": "accountが指定されていません。"})

        acc = next((a for a in drive_connector.accounts if a.email == account_email), None)
        if not acc:
            return jsonify({"success": False, "error": f"アカウントが見つかりません: {account_email}"})

        service = acc.service
        moved_count = 0
        errors = []

        for item in file_ids:
            fid = item.get("id")
            old_parents = item.get("parents", [])
            if not fid:
                continue
            try:
                # マイドライブのルートに追加するだけ（元の親は切り離さない）
                # removeParentsは使わない＝フォルダ構造を破壊しない
                service.files().update(
                    fileId=fid,
                    addParents="root",
                    fields="id, parents"
                ).execute()
                moved_count += 1
                logger.info(f"[サルベージ] マイドライブのルートに追加: {fid}")
            except Exception as e:
                logger.error(f"[サルベージ] 追加失敗 {fid}: {e}")
                errors.append({"id": fid, "error": str(e)})

        return jsonify({"success": True, "moved_count": moved_count, "errors": errors})

    except Exception as e:
        logger.error(f"サルベージで致命的エラー: {e}")
        return jsonify({"success": False, "error": str(e)})


if __name__ == "__main__":
    # Cloud Run環境では環境変数 PORT が自動セットされるので、それを使う
    port = int(os.environ.get("PORT", 8082))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
