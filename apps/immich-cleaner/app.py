import os
import re
import threading
import hashlib
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response, send_file
from dotenv import load_dotenv
from db import get_db, init_db
from immich_api import ImmichAPI

load_dotenv()

app = Flask(__name__)

# ダウンロード先の初期化
DOWNLOAD_DIR = os.getenv("IMMICH_DOWNLOAD_DIR", "M:/Photo_seiri")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# グローバルな実行・スキャン状態
status_lock = threading.Lock()
scan_status = {"running": False, "message": "Idle", "progress": 0, "total": 0}
sync_status = {"running": False, "message": "Idle", "progress": 0, "total": 0}

# ----------------- ユーティリティ -----------------



def parse_sequence_num(file_name):
    """ファイル名から『純粋なカメラのインクリメント連番』のみを抽出する (LINE保存や日付スタンプは除外)"""
    if not file_name:
        return None
        
    fn_lower = file_name.lower()
    
    # 1. LINEの保存ファイル名（10桁以上の数字、または13桁のミリ秒タイムスタンプ等）は除外
    base_name = os.path.splitext(file_name)[0]
    if base_name.isdigit() and len(base_name) >= 8:
        return None
    if re.match(r'^(16|17|18)\d{8,11}', base_name):
        return None
        
    # 2. 日付タイムスタンプ形式 (YYYYMMDD_HHMMSS など) も除外
    if re.match(r'^\d{8}[_\-]\d{6}', base_name) or re.match(r'^\d{8}$', base_name):
        return None
        
    # 3. 純粋なカメラ連番パターンの抽出 (IMG_0001, DSC_1234, Image0123 など)
    match = re.search(r'(?:img_|image_|dsc_|dsc|p|mvi_)?(\d{2,7})$', fn_lower)
    if not match:
        match = re.search(r'(?:img_|image_|dsc_|dsc|p|mvi_)(\d{2,7})', fn_lower)
        
    if match:
        try:
            val = int(match.group(1))
            if val < 100000:
                return val
        except ValueError:
            pass
            
    return None

def get_file_hash(file_path):
    """ファイルのMD5ハッシュ値を計算"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_active_immich_credentials():
    """settings.jsonから現在アクティブなアカウントのURLとAPIキーを取得"""
    from db import get_app_settings
    settings = get_app_settings()
    active_id = settings.get("active_account", "")
    for acc in settings.get("accounts", []):
        if acc["id"] == active_id:
            return acc.get("url"), acc.get("api_key")
    # 設定がない場合は環境変数からフォールバック
    import os
    return os.getenv("IMMICH_URL"), os.getenv("IMMICH_API_KEY")

# ----------------- バックグラウンドタスク -----------------

def run_async_scan():
    """Immichから全アセット情報を取得してSQLiteキャッシュを構築する非同期タスク"""
    global scan_status
    with status_lock:
        if scan_status["running"]:
            return
        scan_status["running"] = True
        scan_status["message"] = "Connecting to Immich..."
        scan_status["progress"] = 0
        scan_status["total"] = 0

    try:
        url, api_key = get_active_immich_credentials()
        api = ImmichAPI(url, api_key)
        connected, _ = api.test_connection()
        if not connected:
            with status_lock:
                scan_status["running"] = False
                scan_status["message"] = "Failed to connect to Immich. Check API Key/URL."
            return

        # 1. 全アセットの概要をフェッチ
        def set_msg(msg):
            with status_lock:
                scan_status["message"] = msg
        
        summaries = api.fetch_all_asset_summaries(progress_callback=set_msg)
        total_assets = len(summaries)
        
        with status_lock:
            scan_status["total"] = total_assets
            scan_status["message"] = f"Fetched {total_assets} asset summaries. Downloading detailed EXIF..."

        # 2. アセット詳細 (exifInfo) を並列フェッチ
        # 一度に大量にフェッチすると重いため、1000件ずつのバッチで処理してDBに保存していく
        batch_size = 1000
        api_details = []
        
        for i in range(0, total_assets, batch_size):
            batch = summaries[i:i+batch_size]
            set_msg(f"Fetching EXIF details batch {i//batch_size + 1}/{((total_assets-1)//batch_size)+1}...")
            batch_details = api.fetch_asset_details_parallel(batch, max_workers=30)
            api_details.extend(batch_details)
            
            # 中間キャッシュ保存
            save_assets_to_db(batch_details)
            
            with status_lock:
                scan_status["progress"] = min(i + batch_size, total_assets)

        set_msg("EXIF details fetched. Rebuilding clustering groups...")
        
        # 3. グループ（ダブルシーケンス・クラスタリング）の構築
        rebuild_all_groups()

        with status_lock:
            scan_status["running"] = False
            scan_status["message"] = f"Scan complete! Cached {total_assets} assets."
            scan_status["progress"] = total_assets
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        with status_lock:
            scan_status["running"] = False
            scan_status["message"] = f"Error during scan: {str(e)}"

def get_device_mappings():
    """設定からデバイスマッピング(機種名->所有者)を取得"""
    import json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'device_mappings'")
    row = cursor.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row["value"])
        except Exception:
            return {}
    return {}

def save_assets_to_db(details_list):
    """取得した詳細データをSQLiteへ保存"""
    mappings = get_device_mappings()
    conn = get_db()
    cursor = conn.cursor()
    for d in details_list:
        asset_id = d.get("id")
        file_name = d.get("originalFileName", "")
        # 日付優先: exifInfo.dateTimeOriginal -> localDateTime -> fileCreatedAt
        exif = d.get("exifInfo", {})
        created_at = exif.get("dateTimeOriginal") or d.get("localDateTime") or d.get("fileCreatedAt")
        file_created_at = d.get("fileCreatedAt", "")
        make = exif.get("make", "")
        model = exif.get("model", "")
        has_metadata = 1 if d.get("hasMetadata", False) else 0
        file_size = exif.get("fileSizeInByte") or d.get("fileSizeInByte") or 0
        duration = d.get("duration", "")
        seq_num = parse_sequence_num(file_name)
        
        # マッピングがある場合は自動で初期所有者とステータスを割り当てる
        assigned_owner = None
        status = 'unselected'
        if model in mappings:
            assigned_owner = mappings[model]
            status = 'download'
            
        device_id = d.get("deviceId") or ""
        original_path = d.get("originalPath") or ""
        
        # 元のファイルパス内のフォルダ名等に所有者のキーワードが含まれている場合は優先自動割り当て
        path_lower = original_path.lower()
        if any(k in path_lower for k in ["父", "ちち", "papa", "father", "daddy", "おやじ"]):
            assigned_owner = 'papa'
            status = 'download'
        elif any(k in path_lower for k in ["母", "はは", "mama", "mother", "mommy", "おかん"]):
            assigned_owner = 'mama'
            status = 'download'
        elif any(k in path_lower for k in ["子", "こども", "child", "kid", "son", "daughter"]):
            assigned_owner = 'child'
            status = 'download'
        
        cursor.execute("""
        INSERT INTO assets (id, file_name, created_at, file_created_at, make, model, has_metadata, file_size, duration, sequence_num, status, assigned_owner, device_id, original_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            file_name=excluded.file_name,
            created_at=excluded.created_at,
            file_created_at=excluded.file_created_at,
            make=excluded.make,
            model=excluded.model,
            has_metadata=excluded.has_metadata,
            file_size=excluded.file_size,
            duration=excluded.duration,
            sequence_num=excluded.sequence_num,
            device_id=excluded.device_id,
            original_path=excluded.original_path
        """, (asset_id, file_name, created_at, file_created_at, make, model, has_metadata, file_size, duration, seq_num, status, assigned_owner, device_id, original_path))
    conn.commit()
    conn.close()

def is_sequence_compatible(assets_a, assets_b):
    """2つのアセット群の連番が、同一のカメラ個体として矛盾がないか判定する"""
    seqs_a = [a["sequence_num"] for a in assets_a if a["sequence_num"] is not None]
    seqs_b = [a["sequence_num"] for a in assets_b if a["sequence_num"] is not None]
    
    # どちらか一方でも連番が抽出できていない場合は、連番での矛盾判定はできないので互換ありとみなす
    if not seqs_a or not seqs_b:
        return True
        
    min_a, max_a = min(seqs_a), max(seqs_a)
    min_b, max_b = min(seqs_b), max(seqs_b)
    
    # 1. 連番範囲が重なっている場合（例：Aが100-200、Bが150-250）は、同一カメラで同じ連番はあり得ないので絶対に別個体
    if not (max_a < min_b or max_b < min_a):
        return False
        
    # 2. 時間的な前後関係と連番の順序・ジャンプ幅の整合性をチェック
    times_a = [a["created_at"] for a in assets_a if a["created_at"]]
    times_b = [a["created_at"] for a in assets_b if a["created_at"]]
    
    if times_a and times_b:
        max_time_a = max(times_a)
        min_time_b = min(times_b)
        
        # Aのほうが古い場合
        if max_time_a <= min_time_b:
            # 時間が経過しているのに、連番が減っている（逆行）場合は別個体
            if max_a > min_b:
                return False
            # 同じ月内でのマージなのに、連番が200以上も離れている（ジャンプ）場合は別個体
            if min_b - max_a > 200:
                return False
        # Bのほうが古い場合
        else:
            if max_b > min_a:
                return False
            if min_a - max_b > 200:
                return False
                
    return True

def rebuild_all_groups():
    """
    全アセットに対して、連番の単調増加ライン（スレッド）を自動抽出して個体を分離し、
    「撮影月（YYYY-MM）」の単位でタイムライングループを構築する。
    これによって、グループ数の爆発を防ぎつつ、同一機種内の個体別の自動仕分けを容易にする。
    """
    conn = get_db()
    cursor = conn.cursor()
    
    # 既存のグループ情報をリセット
    cursor.execute("DELETE FROM groups")
    cursor.execute("UPDATE assets SET group_id = NULL")
    
    # 過去の不整合データ (status と owner の値が逆転してしまったアセット) を自動修復
    cursor.execute("""
        UPDATE assets 
        SET assigned_owner = status, status = 'download' 
        WHERE status IN ('papa', 'mama', 'child')
    """)
    cursor.execute("""
        UPDATE assets 
        SET assigned_owner = NULL 
        WHERE assigned_owner = 'download'
    """)
    
    # 混在が懸念される「同一カメラモデル ＋ 物理デバイス」のペア一覧を取得 (iPhone 7 x デバイスA, iPhone 7 x デバイスB 等)
    cursor.execute("""
        SELECT DISTINCT model, COALESCE(device_id, '') as device_id 
        FROM assets 
        WHERE model IS NOT NULL AND model != ''
    """)
    models_devices = [dict(r) for r in cursor.fetchall()]
    
    for md in models_devices:
        model = md["model"]
        device_id = md["device_id"]
        
        # 1. 連番があるアセットを「連番の昇順」で取得する
        cursor.execute("""
            SELECT id, file_name, created_at, sequence_num 
            FROM assets 
            WHERE model = ? AND COALESCE(device_id, '') = ? AND sequence_num IS NOT NULL AND created_at IS NOT NULL AND created_at != ''
            ORDER BY sequence_num ASC
        """, (model, device_id))
        seq_assets = [dict(r) for r in cursor.fetchall()]
        
        # 2. 連番がないアセットを取得
        cursor.execute("""
            SELECT id, file_name, created_at, sequence_num 
            FROM assets 
            WHERE model = ? AND COALESCE(device_id, '') = ? AND (sequence_num IS NULL OR created_at IS NULL OR created_at = '')
        """, (model, device_id))
        no_seq_assets = [dict(r) for r in cursor.fetchall()]
        
        threads = [] # 各要素は { "last_seq": int, "last_time": str, "asset_ids": [assets...] }
        
        # 連番順に走査し、撮影日時の逆行（巻き戻り）がある箇所で切る
        current_thread = []
        last_time = None
        
        for a in seq_assets:
            a_time_str = a["created_at"]
            a_time = None
            if a_time_str:
                try:
                    a_time = datetime.fromisoformat(a_time_str.replace("Z", "+00:00"))
                except Exception:
                    pass
            
            # 日時の逆行チェック
            is_backward = False
            if last_time and a_time:
                # 連番が増えているのに、日時が巻き戻っている場合は別個体（逆行）
                if a_time < last_time:
                    is_backward = True
                    
            if is_backward and current_thread:
                # 逆行箇所でスレッドを切断して新規開始
                threads.append({
                    "last_seq": current_thread[-1]["sequence_num"],
                    "last_time": current_thread[-1]["created_at"],
                    "asset_ids": current_thread
                })
                current_thread = []
                
            current_thread.append(a)
            if a_time:
                last_time = a_time
                
        if current_thread:
            threads.append({
                "last_seq": current_thread[-1]["sequence_num"],
                "last_time": current_thread[-1]["created_at"],
                "asset_ids": current_thread
            })
            
        # 連番なしのアセットは、直近3分以内のスレッドがあればマージ、なければ独立スレッドに
        for a in no_seq_assets:
            created_at_str = a["created_at"]
            a_time = None
            if created_at_str:
                try:
                    a_time = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                except Exception:
                    pass
            
            best_thread_idx = -1
            min_time_diff = 180 # 3分
            if a_time:
                for idx, t in enumerate(threads):
                    if not t["last_time"]:
                        continue
                    try:
                        t_time = datetime.fromisoformat(t["last_time"].replace("Z", "+00:00"))
                        time_diff = abs((a_time - t_time).total_seconds())
                        if time_diff < min_time_diff:
                            min_time_diff = time_diff
                            best_thread_idx = idx
                    except Exception:
                        pass
                        
            if best_thread_idx != -1:
                threads[best_thread_idx]["asset_ids"].append(a)
                if a_time:
                    threads[best_thread_idx]["last_time"] = created_at_str
            else:
                threads.append({
                    "last_seq": None,
                    "last_time": created_at_str,
                    "asset_ids": [a]
                })

        # 各スレッドのアセットを「撮影年月 (YYYY-MM)」ごとに集計してグループ化
        from collections import defaultdict
        month_groups = defaultdict(list)
        
        for thread_idx, t in enumerate(threads):
            t_month_assets = defaultdict(list)
            for a in t["asset_ids"]:
                month = "日時不明"
                if a["created_at"] and len(a["created_at"]) >= 7:
                    month = a["created_at"][:7] # "YYYY-MM"
                t_month_assets[month].append(a)
                
            for month, assets_in_month in t_month_assets.items():
                month_groups[month].append({
                    "thread_idx": thread_idx,
                    "assets": assets_in_month
                })
                
        # 各月ごとにグループとしてDB登録
        for month, groups_in_month in sorted(month_groups.items()):
            misc_month_assets = []
            
            for idx, g in enumerate(groups_in_month):
                g_assets = g["assets"]
                has_seq = any(a["sequence_num"] is not None for a in g_assets)
                if has_seq:
                    group_name = f"{model} - {month.replace('-', '年')}月 (個体スレッド #{g['thread_idx']})"
                    commit_group(model, g_assets, cursor, name=group_name)
                else:
                    misc_month_assets.extend(g_assets)
                    
            if misc_month_assets:
                misc_month_assets.sort(key=lambda x: x["created_at"] or "")
                group_name = f"{model} - {month.replace('-', '年')}月 (スクリーンショット・その他)"
                commit_group(model, misc_month_assets, cursor, is_misc=True, name=group_name)

    # 3. EXIFなし（スクリーンショット等）のアセットをファイル名パターンに分類して仮想モデルに登録
    cursor.execute("""
        SELECT id, file_name, created_at, sequence_num 
        FROM assets 
        WHERE model IS NULL OR model = ''
    """)
    exif_less_assets = [dict(r) for r in cursor.fetchall()]
    if exif_less_assets:
        categories = {
            "📱 スクリーンショット (EXIFなし)": [],
            "💬 LINE保存画像 (EXIFなし)": [],
            "🌐 SNS・Web保存画像 (EXIFなし)": [],
            "❓ その他・分類不能 (EXIFなし)": []
        }
        
        for a in exif_less_assets:
            fn = a["file_name"] or ""
            # 1. スクショ判定 (ファイル名に screenshot/capture を含むか、png拡張子など)
            if "screenshot" in fn.lower() or "capture" in fn.lower() or fn.lower().endswith(".png"):
                categories["📱 スクリーンショット (EXIFなし)"].append(a)
            # 2. LINE保存判定 (13桁のミリ秒秒数など。16,17,18から始まる数字10桁以上など)
            elif re.match(r'^(16|17|18)\d{8,11}\.', fn) or re.match(r'^(16|17|18)\d{8,11}_', fn) or (fn.split('.')[0].isdigit() and len(fn.split('.')[0]) >= 10):
                categories["💬 LINE保存画像 (EXIFなし)"].append(a)
            # 3. SNS・Web保存判定 (10桁以上の英数字記号)
            elif re.match(r'^[a-zA-Z0-9_\-]{10,}\.[a-zA-Z]+$', fn):
                categories["🌐 SNS・Web保存画像 (EXIFなし)"].append(a)
            else:
                categories["❓ その他・分類不能 (EXIFなし)"].append(a)
                
        # 各カテゴリーについて、「日付（YYYY-MM-DD）ごと」かつ「最大100枚」に細分化してグループとして登録
        for cat_model, cat_assets in categories.items():
            if not cat_assets:
                continue
                
            # 1. 日付ごとにアセットを分類
            daily_assets = {}
            for a in cat_assets:
                date_key = a["created_at"][:10] if a["created_at"] and len(a["created_at"]) >= 10 else "日付不明"
                if date_key not in daily_assets:
                    daily_assets[date_key] = []
                daily_assets[date_key].append(a)
                
            # 2. 各日付内で、最大100枚ずつにスライスして登録 (1つのカードが大きくなりすぎるのを防止)
            for date_key, date_assets in sorted(daily_assets.items()):
                date_assets.sort(key=lambda x: x["created_at"] or "")
                chunk_size = 100
                for i in range(0, len(date_assets), chunk_size):
                    chunk = date_assets[i : i + chunk_size]
                    commit_group(cat_model, chunk, cursor, is_misc=True)
                
    conn.commit()
    conn.close()

def commit_group(model, group_assets, cursor, is_misc=False, name=None):
    """グループ情報をSQLiteに保存し、所属アセットのgroup_idを更新"""
    if not group_assets:
        return
        
    start_time = group_assets[0]["created_at"]
    end_time = group_assets[-1]["created_at"]
    
    seqs = [a["sequence_num"] for a in group_assets if a["sequence_num"] is not None]
    seq_start = min(seqs) if seqs and not is_misc else None
    seq_end = max(seqs) if seqs and not is_misc else None
    
    # 再スキャン時に、このグループに属するアセットの中にすでに仕分け済みのものがあるか確認
    assigned_owner = None
    status = 'unselected'
    
    asset_ids = [a["id"] for a in group_assets]
    placeholders = ",".join("?" for _ in asset_ids)
    cursor.execute(f"SELECT status, assigned_owner FROM assets WHERE id IN ({placeholders}) AND status != 'unselected' LIMIT 1", asset_ids)
    row = cursor.fetchone()
    if row:
        status = row["status"]
        assigned_owner = row["assigned_owner"]
    
    cursor.execute("""
        INSERT INTO groups (model, name, start_time, end_time, seq_start, seq_end, asset_count, assigned_owner, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (model, name or model, start_time, end_time, seq_start, seq_end, len(group_assets), assigned_owner, status))
    
    group_id = cursor.lastrowid
    
    # 各アセットにgroup_idを適用。もしグループが仕分け済みなら、全アセットをその仕分けに同期する
    for a in group_assets:
        if status != 'unselected':
            cursor.execute("UPDATE assets SET group_id = ?, status = ?, assigned_owner = ? WHERE id = ?", (group_id, status, assigned_owner, a["id"]))
        else:
            cursor.execute("UPDATE assets SET group_id = ? WHERE id = ?", (group_id, a["id"]))

def run_async_sync():
    """選択された仕分けアクションを実行する非同期タスク"""
    global sync_status
    with status_lock:
        if sync_status["running"]:
            return
        sync_status["running"] = True
        sync_status["message"] = "Preparing download tasks..."
        sync_status["progress"] = 0
        sync_status["total"] = 0
    try:
        url, api_key = get_active_immich_credentials()
        api = ImmichAPI(url, api_key)
        conn = get_db()
        cursor = conn.cursor()
        
        # 1. 各グループの割り当て設定をアセット個別レコードに反映
        # (グループが papa/mama/child 等に割り当てられている場合、その中のアセットに反映する)
        cursor.execute("""
            SELECT id, assigned_owner, status FROM groups 
            WHERE status != 'unselected'
        """)
        active_groups = cursor.fetchall()
        for g in active_groups:
            cursor.execute("""
                UPDATE assets 
                SET status = ?, assigned_owner = ? 
                WHERE group_id = ? AND status = 'unselected'
            """, (g["status"], g["assigned_owner"], g["id"]))
            
        conn.commit()
        
        # 2. ダウンロード対象のアセットを抽出
        cursor.execute("""
            SELECT id, file_name, created_at, model, assigned_owner 
            FROM assets 
            WHERE status = 'download'
        """)
        download_assets = [dict(r) for r in cursor.fetchall()]
        total_dl = len(download_assets)
        
        with status_lock:
            sync_status["total"] = total_dl
            sync_status["message"] = f"Starting download of {total_dl} assets..."
            
        success_count = 0
        
        for idx, asset in enumerate(download_assets):
            asset_id = asset["id"]
            file_name = asset["file_name"]
            model = asset["model"] or "UnknownModel"
            owner = asset["assigned_owner"] or "unknown"
            
            # 年・月フォルダの作成 (例: M:\Photo_seiri\iPhone 15 (papa)\2026-06\)
            date_dir = "unknown-date"
            if asset["created_at"]:
                try:
                    date_dir = asset["created_at"][:7] # YYYY-MM
                except Exception:
                    pass
                    
            # 最終保存先パス
            # 例: M:\Photo_seiri\iPhone 15 (papa)\2026-06\IMG_1234.HEIC
            sub_folder = f"{model} ({owner})" if owner != "unknown" else model
            # EXIFなしアセット（スクリーンショット等）は、個別仕分け時にownerフォルダに入れる
            if not asset["model"]:
                sub_folder = f"Screenshot ({owner})" if owner != "unknown" else "Screenshot"
                
            dest_path = os.path.join(DOWNLOAD_DIR, sub_folder, date_dir, file_name)
            
            with status_lock:
                sync_status["message"] = f"Downloading [{idx+1}/{total_dl}]: {file_name}..."
                
            # A. ダウンロード実行
            if api.download_original(asset_id, dest_path):
                # B. 安全ガード: サイズのチェック
                # ImmichのDB側サイズを取得
                cursor.execute("SELECT file_size FROM assets WHERE id = ?", (asset_id,))
                row = cursor.fetchone()
                db_size = row["file_size"] if row else 0
                
                local_size = os.path.getsize(dest_path)
                
                size_ok = True
                if db_size > 0 and abs(local_size - db_size) > 1024:  # 1KB以上のズレがあればエラー判定
                    size_ok = False
                    
                if size_ok:
                    # C. Immichから安全に削除 (ゴミ箱へ)
                    if api.delete_asset(asset_id):
                        # DBキャッシュのステータスを完了に更新
                        cursor.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
                        success_count += 1
                    else:
                        print(f"Downloaded but failed to delete from Immich: {file_name}")
                else:
                    print(f"File size mismatch for {file_name}. Immich: {db_size}, Local: {local_size}")
                    # 不整合ファイルは削除
                    try:
                        os.remove(dest_path)
                    except:
                        pass
            
            with status_lock:
                sync_status["progress"] = idx + 1
                
            # 進捗を10件ごとにコミット
            if idx % 10 == 0:
                conn.commit()
                
        # 残す（keep）に設定されたアセットは削除せずデータベースに保持し続ける（再スキャン時の引継ぎのため）
        conn.commit()
        conn.close()
        
        # グループ情報を再構築 (削除されたアセット分を反映するため)
        rebuild_all_groups()
        
        with status_lock:
            sync_status["running"] = False
            sync_status["message"] = f"Finished! Successfully sorted and cleaned up {success_count} assets."
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        with status_lock:
            sync_status["running"] = False
            sync_status["message"] = f"Sync failed: {str(e)}"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/accounts")
def list_accounts():
    from db import get_app_settings
    return jsonify(get_app_settings())

@app.route("/api/accounts", methods=["POST"])
def add_or_update_account():
    from db import get_app_settings, save_app_settings, init_db
    data = request.json
    acc_id = data.get("id")
    name = data.get("name")
    url = data.get("url")
    api_key = data.get("api_key")
    
    if not acc_id or not name or not url or not api_key:
        return jsonify({"status": "error", "message": "ID, 名前, URL, APIキーは必須です。"}), 400
        
    settings = get_app_settings()
    accounts = settings.get("accounts", [])
    
    # 既存アカウントの更新、または新規追加
    updated = False
    for acc in accounts:
        if acc["id"] == acc_id:
            acc["name"] = name
            acc["url"] = url
            acc["api_key"] = api_key
            updated = True
            break
            
    if not updated:
        accounts.append({
            "id": acc_id,
            "name": name,
            "url": url,
            "api_key": api_key
        })
        
    settings["accounts"] = accounts
    
    # 初めてのアカウントならアクティブに設定
    if not settings.get("active_account"):
        settings["active_account"] = acc_id
        
    save_app_settings(settings)
    
    # 新しいDBの初期化
    init_db()
    
    return jsonify({"status": "success"})

@app.route("/api/accounts/switch", methods=["POST"])
def switch_account():
    from db import get_app_settings, save_app_settings, init_db
    data = request.json
    acc_id = data.get("id")
    
    settings = get_app_settings()
    found = False
    for acc in settings.get("accounts", []):
        if acc["id"] == acc_id:
            found = True
            break
            
    if not found:
        return jsonify({"status": "error", "message": "指定されたアカウントが見つかりません。"}), 400
        
    settings["active_account"] = acc_id
    save_app_settings(settings)
    
    # 切り替え先DBの初期化
    init_db()
    
    return jsonify({"status": "success"})

@app.route("/api/accounts/<acc_id>", methods=["DELETE"])
def delete_account(acc_id):
    from db import get_app_settings, save_app_settings
    settings = get_app_settings()
    accounts = settings.get("accounts", [])
    
    new_accounts = [acc for acc in accounts if acc["id"] != acc_id]
    settings["accounts"] = new_accounts
    
    if settings.get("active_account") == acc_id:
        settings["active_account"] = new_accounts[0]["id"] if new_accounts else ""
        
    save_app_settings(settings)
    return jsonify({"status": "success"})

@app.route("/api/status")
def get_status():
    """スキャンおよび同期実行のステータスを取得"""
    return jsonify({
        "scan": scan_status,
        "sync": sync_status
    })

@app.route("/api/scan", methods=["POST"])
def trigger_scan():
    """非同期スキャンの実行トリガー"""
    global scan_status
    with status_lock:
        if scan_status["running"]:
            return jsonify({"status": "already_running"}), 400
            
    thread = threading.Thread(target=run_async_scan)
    thread.daemon = True
    thread.start()
    return jsonify({"status": "started"})

@app.route("/api/rebuild-groups", methods=["POST"])
def trigger_rebuild_groups():
    """すでにキャッシュされているアセット情報を元に、グループ分けのみを高速で再構築（1秒）"""
    global scan_status
    with status_lock:
        if scan_status["running"]:
            return jsonify({"status": "error", "message": "スキャンが実行中です。"}), 400
        scan_status["running"] = True
        scan_status["message"] = "グループを再構築中 (1秒)..."
        
    try:
        rebuild_all_groups()
        with status_lock:
            scan_status["running"] = False
            scan_status["message"] = "グループ再構築完了！"
        return jsonify({"status": "success"})
    except Exception as e:
        with status_lock:
            scan_status["running"] = False
            scan_status["message"] = f"再構築エラー: {str(e)}"
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/sync", methods=["POST"])
def trigger_sync():
    """仕分け実行（ダウンロード＆削除）のトリガー"""
    global sync_status
    with status_lock:
        if sync_status["running"]:
            return jsonify({"status": "already_running"}), 400
            
    thread = threading.Thread(target=run_async_sync)
    thread.daemon = True
    thread.start()
    return jsonify({"status": "started"})

@app.route("/api/devices")
def get_devices():
    """スキャンされたカメラ機種（デバイス）およびグループベースのモデル一覧を取得"""
    conn = get_db()
    cursor = conn.cursor()
    
    # グループとアセットを結合して、グループ側のモデル名(ジャンル含む)ごとにアセット数・未整理数を集計
    cursor.execute("""
        SELECT 
            g.model as model, 
            COUNT(a.id) as total_count,
            SUM(CASE WHEN a.status = 'unselected' THEN 1 ELSE 0 END) as unselected_count,
            COUNT(DISTINCT a.group_id) as group_count
        FROM assets a
        JOIN groups g ON a.group_id = g.id
        GROUP BY g.model
    """)
    rows = cursor.fetchall()
    
    devices = []
    for r in rows:
        model = r["model"]
        total_count = r["total_count"]
        unselected_count = r["unselected_count"]
        grp_count = r["group_count"]
        
        devices.append({
            "model": model,
            "count": total_count,
            "unselected_count": unselected_count,
            "group_count": grp_count,
            "requires_split": True if "iPhone" in model or "EXIFなし" in model else False
        })
        
    conn.close()
    return jsonify(devices)

@app.route("/api/groups/<path:model>")
def get_model_groups(model):
    """指定されたモデルのタイムライングループ一覧を取得"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, model, start_time, end_time, seq_start, seq_end, asset_count, assigned_owner, status 
        FROM groups 
        WHERE model = ? 
        ORDER BY datetime(start_time) ASC
    """, (model,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(rows)

@app.route("/api/groups/<int:group_id>/split", methods=["POST"])
def split_group(group_id):
    data = request.json
    split_asset_id = data.get("asset_id")
    if not split_asset_id:
        return jsonify({"status": "error", "message": "分割基準アセットIDが必要です"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    
    # 元グループ情報を取得
    cursor.execute("SELECT model, name, start_time, end_time, seq_start, seq_end, status, assigned_owner FROM groups WHERE id = ?", (group_id,))
    g_info = cursor.fetchone()
    if not g_info:
        conn.close()
        return jsonify({"status": "error", "message": "グループが見つかりません"}), 404
        
    # グループ内のアセットを連番順に取得
    cursor.execute("SELECT id, created_at, sequence_num FROM assets WHERE group_id = ? ORDER BY sequence_num ASC, datetime(created_at) ASC", (group_id,))
    assets = [dict(r) for r in cursor.fetchall()]
    
    # 分割基準アセットのインデックスを見つける
    split_idx = -1
    for idx, a in enumerate(assets):
        if a["id"] == split_asset_id:
            split_idx = idx
            break
            
    if split_idx <= 0:
        conn.close()
        return jsonify({"status": "error", "message": "指定された写真での分割はできません (先頭または存在しないアセット)"}), 400
        
    # 分割後の2つのアセット群
    assets_old = assets[:split_idx]
    assets_new = assets[split_idx:]
    
    # 1. 新しいグループを作成
    new_name = g_info["name"] + " (分割後)"
    cursor.execute("""
        INSERT INTO groups (model, name, status, assigned_owner)
        VALUES (?, ?, ?, ?)
    """, (g_info["model"], new_name, g_info["status"], g_info["assigned_owner"]))
    new_group_id = cursor.lastrowid
    
    # 2. 新グループへアセットのgroup_idを更新
    new_asset_ids = [a["id"] for a in assets_new]
    placeholders = ",".join("?" for _ in new_asset_ids)
    cursor.execute(f"UPDATE assets SET group_id = ? WHERE id IN ({placeholders})", [new_group_id] + new_asset_ids)
    
    # 3. 両グループのメタ統計情報を更新するヘルパー関数
    def update_group_stats(gid):
        cursor.execute("SELECT created_at, sequence_num FROM assets WHERE group_id = ? ORDER BY sequence_num ASC, datetime(created_at) ASC", (gid,))
        g_assets = cursor.fetchall()
        if not g_assets:
            cursor.execute("DELETE FROM groups WHERE id = ?", (gid,))
            return
        st = g_assets[0]["created_at"]
        et = g_assets[-1]["created_at"]
        seqs = [a["sequence_num"] for a in g_assets if a["sequence_num"] is not None]
        seq_s = min(seqs) if seqs else None
        seq_e = max(seqs) if seqs else None
        cursor.execute("""
            UPDATE groups 
            SET start_time = ?, end_time = ?, seq_start = ?, seq_end = ?, asset_count = ?
            WHERE id = ?
        """, (st, et, seq_s, seq_e, len(g_assets), gid))
        
    update_group_stats(group_id)
    update_group_stats(new_group_id)
    
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route("/api/groups/<int:group_id>/merge-next", methods=["POST"])
def merge_next_group(group_id):
    conn = get_db()
    cursor = conn.cursor()
    
    # 元グループ情報を取得
    cursor.execute("SELECT model, start_time FROM groups WHERE id = ?", (group_id,))
    g_info = cursor.fetchone()
    if not g_info:
        conn.close()
        return jsonify({"status": "error", "message": "グループが見つかりません"}), 404
        
    model = g_info["model"]
    start_time = g_info["start_time"]
    
    # 同じモデル内で、このグループの次に古いグループ（時系列上の次のグループ）を1件取得
    cursor.execute("""
        SELECT id FROM groups 
        WHERE model = ? AND start_time > ? AND id != ?
        ORDER BY start_time ASC LIMIT 1
    """, (model, start_time, group_id))
    next_g = cursor.fetchone()
    
    if not next_g:
        conn.close()
        return jsonify({"status": "error", "message": "結合対象となる次のグループが見つかりません"}), 400
        
    next_group_id = next_g["id"]
    
    # 次のグループのアセットをすべて元グループへ統合
    cursor.execute("UPDATE assets SET group_id = ? WHERE group_id = ?", (group_id, next_group_id))
    
    # 次のグループを削除
    cursor.execute("DELETE FROM groups WHERE id = ?", (next_group_id,))
    
    # 元グループの統計情報を再計算して更新
    cursor.execute("SELECT created_at, sequence_num FROM assets WHERE group_id = ? ORDER BY sequence_num ASC, datetime(created_at) ASC", (group_id,))
    g_assets = cursor.fetchall()
    
    if g_assets:
        st = g_assets[0]["created_at"]
        et = g_assets[-1]["created_at"]
        seqs = [a["sequence_num"] for a in g_assets if a["sequence_num"] is not None]
        seq_s = min(seqs) if seqs else None
        seq_e = max(seqs) if seqs else None
        cursor.execute("""
            UPDATE groups 
            SET start_time = ?, end_time = ?, seq_start = ?, seq_end = ?, asset_count = ?
            WHERE id = ?
        """, (st, et, seq_s, seq_e, len(g_assets), group_id))
        
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route("/api/groups/<int:group_id>/samples")
def get_group_samples(group_id):
    """指定グループ内の代表写真(デフォルト40枚)のIDを取得。limit=0で全件取得。"""
    limit_val = request.args.get("limit", 40, type=int)
    conn = get_db()
    cursor = conn.cursor()
    if limit_val > 0:
        cursor.execute("""
            SELECT id, file_name, created_at, sequence_num 
            FROM assets WHERE group_id = ? ORDER BY sequence_num ASC, datetime(created_at) ASC LIMIT ?
        """, (group_id, limit_val))
    else:
        cursor.execute("""
            SELECT id, file_name, created_at, sequence_num 
            FROM assets WHERE group_id = ? ORDER BY sequence_num ASC, datetime(created_at) ASC
        """, (group_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    samples = []
    last_time = None
    for r in rows:
        a_time_str = r["created_at"]
        a_time = None
        is_backward = False
        if a_time_str:
            try:
                a_time = datetime.fromisoformat(a_time_str.replace("Z", "+00:00"))
                if last_time and a_time < last_time:
                    is_backward = True
                last_time = a_time
            except Exception:
                pass
                
        samples.append({
            "id": r["id"],
            "url": f"/api/proxy/thumbnail/{r['id']}",
            "file_name": r["file_name"],
            "created_at": r["created_at"],
            "sequence_num": r["sequence_num"],
            "is_backward": is_backward
        })
    return jsonify(samples)

@app.route("/api/proxy/thumbnail/<asset_id>")
def proxy_thumbnail(asset_id):
    url, api_key = get_active_immich_credentials()
    api = ImmichAPI(url, api_key)
    # Immichはsize=thumbnailまたはsize=previewパラメータを要求することがあるため、両方試す
    url_thumb = f"{api.url}/api/assets/{asset_id}/thumbnail?size=thumbnail"
    try:
        res = requests.get(url_thumb, headers=api.headers, stream=True, timeout=10)
        if res.status_code == 200:
            return Response(res.iter_content(chunk_size=4096), content_type=res.headers.get("content-type", "image/jpeg"))
        
        # サムネイル取得失敗時はプレビューサイズでリトライ
        url_preview = f"{api.url}/api/assets/{asset_id}/thumbnail?size=preview"
        res_preview = requests.get(url_preview, headers=api.headers, stream=True, timeout=10)
        if res_preview.status_code == 200:
            return Response(res_preview.iter_content(chunk_size=4096), content_type=res_preview.headers.get("content-type", "image/jpeg"))
            
        print(f"Failed proxying thumbnail {asset_id}. Thumbnail Status: {res.status_code}, Preview Status: {res_preview.status_code}")
    except Exception as e:
        print(f"Error proxying thumbnail {asset_id}: {e}")
    return "Not Found", 404

def propagate_group_action(group_id, owner, status, cursor):
    """
    時間と連番の順序矛盾がない同じ個体のスレッドグループに、選択されたアクションを自動伝搬（仮仕分け）する。
    """
    if status == 'unselected':
        return
        
    # 選択されたグループの情報を取得
    cursor.execute("SELECT model, start_time, seq_start, seq_end FROM groups WHERE id = ?", (group_id,))
    target = cursor.fetchone()
    if not target or not target["model"] or target["seq_start"] is None:
        return
        
    model = target["model"]
    t_time = target["start_time"]
    t_seq = target["seq_start"]
    
    # 同じモデルのすべてのグループを取得（時間順）
    cursor.execute("""
        SELECT id, start_time, seq_start, seq_end, status, assigned_owner 
        FROM groups 
        WHERE model = ? AND seq_start IS NOT NULL
        ORDER BY datetime(start_time) ASC
    """, (model,))
    all_groups = [dict(r) for r in cursor.fetchall()]
    
    # ターゲットグループのインデックスを見つける
    target_idx = -1
    for idx, g in enumerate(all_groups):
        if g["id"] == group_id:
            target_idx = idx
            break
            
    if target_idx == -1:
        return
        
    to_update = []
    
    # 1. 未来方向のグループを走査
    last_seq = t_seq
    for i in range(target_idx + 1, len(all_groups)):
        g = all_groups[i]
        # 連番が逆戻りしておらず、かつ1グループあたり急激に離れていないこと(3000番以内)
        if g["seq_start"] >= last_seq and g["seq_start"] - last_seq < 3000:
            if g["status"] == 'unselected':
                to_update.append(g["id"])
                last_seq = g["seq_start"]
        else:
            break
            
    # 2. 過去方向のグループを走査
    last_seq = t_seq
    for i in range(target_idx - 1, -1, -1):
        g = all_groups[i]
        if g["seq_start"] <= last_seq and last_seq - g["seq_start"] < 3000:
            if g["status"] == 'unselected':
                to_update.append(g["id"])
                last_seq = g["seq_start"]
        else:
            break
            
    # 伝搬実行
    if to_update:
        placeholders = ",".join("?" for _ in to_update)
        # groupsテーブルの更新
        cursor.execute(f"""
            UPDATE groups 
            SET assigned_owner = ?, status = ? 
            WHERE id IN ({placeholders})
        """, (owner, status, *to_update))
        
        # assetsテーブルの更新
        cursor.execute(f"""
            UPDATE assets 
            SET status = ?, assigned_owner = ? 
            WHERE group_id IN ({placeholders}) AND status = 'unselected'
        """, (status, owner, *to_update))
        
    return to_update

@app.route("/api/groups/action", methods=["POST"])
def set_group_action():
    """グループの仕分けアクション（お父さん/お母さん/残す等）を設定"""
    data = request.json
    owner = data.get("owner")     # 'papa', 'mama', 'child', None
    status = data.get("status")   # 'unselected', 'download', 'keep'
    group_id = data.get("group_id")
    
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. ターゲットグループ自体を更新
    cursor.execute("""
        UPDATE groups 
        SET assigned_owner = ?, status = ? 
        WHERE id = ?
    """, (owner, status, group_id))
    
    # 2. 同一グループ内の未仕分けアセットも同期
    cursor.execute("""
        UPDATE assets 
        SET status = ?, assigned_owner = ? 
        WHERE group_id = ? AND status = 'unselected'
    """, (status, owner, group_id))
    
    # 3. iPhone等の混在機種の場合、連番ラインに沿って前後のグループに自動伝搬
    updated_ids = propagate_group_action(group_id, owner, status, cursor)
    
    conn.commit()
    conn.close()
    return jsonify({
        "status": "success",
        "updated_groups": updated_ids
    })

@app.route("/api/settings/device-mapping", methods=["GET", "POST"])
def device_mapping_endpoint():
    import json
    if request.method == "POST":
        data = request.json
        model = data.get("model")
        owner = data.get("owner") # 'papa', 'mama', 'child', None
        
        # 既存のマッピングを取得して更新
        mappings = get_device_mappings()
        if owner:
            mappings[model] = owner
        else:
            if model in mappings:
                del mappings[model]
                
        # データベースにマッピングを保存
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value)
            VALUES ('device_mappings', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (json.dumps(mappings),))
        
        # この設定に基づいて、未整理(unselected)の既存アセットを一括で自動割り当てする
        status = 'download' if owner else 'unselected'
        if model == "EXIFなし (スクリーンショット等)":
            cursor.execute("""
                UPDATE assets 
                SET assigned_owner = ?, status = ? 
                WHERE (model IS NULL OR model = '') AND status = 'unselected'
            """, (owner, status))
        else:
            cursor.execute("""
                UPDATE assets 
                SET assigned_owner = ?, status = ? 
                WHERE model = ? AND status = 'unselected'
            """, (owner, status, model))
            
            # グループ側の未整理のものも一括で更新
            cursor.execute("""
                UPDATE groups 
                SET assigned_owner = ?, status = ? 
                WHERE model = ? AND status = 'unselected'
            """, (owner, status, model))
            
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "mappings": mappings})
    else:
        return jsonify(get_device_mappings())

@app.route("/api/devices/action", methods=["POST"])
def set_device_action():
    """機種（デバイス）単位で一括仕分けアクションを設定"""
    data = request.json
    model = data.get("model")
    owner = data.get("owner")     # 'papa', 'mama', 'child', None
    status = data.get("status")   # 'unselected', 'download', 'keep'
    
    conn = get_db()
    cursor = conn.cursor()
    
    if model == "EXIFなし (スクリーンショット等)":
        cursor.execute("""
            UPDATE assets 
            SET assigned_owner = ?, status = ? 
            WHERE model IS NULL OR model = ''
        """, (owner, status))
    else:
        # assetsテーブルを一括更新
        cursor.execute("""
            UPDATE assets 
            SET assigned_owner = ?, status = ? 
            WHERE model = ?
        """, (owner, status, model))
        # groupsテーブルも一括更新
        cursor.execute("""
            UPDATE groups 
            SET assigned_owner = ?, status = ? 
            WHERE model = ?
        """, (owner, status, model))
        
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# --- EXIFなし（スクリーンショット）個別仕分け用API ---

@app.route("/api/screenshots")
def get_screenshots():
    """EXIFなし写真の一覧（未処理のもの）を最大100件取得"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, file_name, created_at, status, assigned_owner 
        FROM assets 
        WHERE (model IS NULL OR model = '') AND status = 'unselected'
        ORDER BY datetime(created_at) ASC 
        LIMIT 100
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    screenshots = []
    for r in rows:
        screenshots.append({
            "id": r["id"],
            "file_name": r["file_name"],
            "created_at": r["created_at"],
            "url": f"/api/proxy/thumbnail/{r['id']}"
        })
    return jsonify(screenshots)

@app.route("/api/screenshots/<asset_id>/action", methods=["POST"])
def set_screenshot_action():
    """個別写真（スクリーンショット）の仕分けアクションを設定"""
    data = request.json
    owner = data.get("owner")
    status = data.get("status") # 'download', 'keep'
    asset_id = data.get("asset_id")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE assets 
        SET assigned_owner = ?, status = ? 
        WHERE id = ?
    """, (owner, status, asset_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
