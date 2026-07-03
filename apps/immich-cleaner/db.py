import sqlite3
import os
import json

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "immich_cache.db")

def get_app_settings():
    """settings.jsonからアプリ設定を取得"""
    if not os.path.exists(SETTINGS_PATH):
        # デフォルト設定を作成
        default_settings = {
            "active_account": "",
            "accounts": []
        }
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(default_settings, f, indent=4, ensure_ascii=False)
        return default_settings
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_account": "", "accounts": []}

def save_app_settings(settings):
    """settings.jsonにアプリ設定を保存"""
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def get_db_path():
    """現在アクティブなアカウントに応じたDBファイルのパスを取得"""
    settings = get_app_settings()
    active = settings.get("active_account", "")
    if active:
        # アカウント固有のDBファイル
        return os.path.join(os.path.dirname(__file__), f"immich_cache_{active}.db")
    return DEFAULT_DB_PATH

def get_db():
    """動的に選択されたDBに接続"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """接続先のDBに必要なテーブルを作成・マイグレーション"""
    conn = get_db()
    cursor = conn.cursor()
    
    # アセットキャッシュテーブル
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assets (
        id TEXT PRIMARY KEY,
        file_name TEXT,
        created_at TEXT,
        file_created_at TEXT,
        make TEXT,
        model TEXT,
        has_metadata INTEGER,
        file_size INTEGER,
        duration TEXT,
        status TEXT DEFAULT 'unselected', -- 'unselected', 'download', 'keep'
        assigned_owner TEXT,             -- 'papa', 'mama', 'child', None
        group_id INTEGER,
        sequence_num INTEGER,            -- IMG_XXXXの連番数値部分
        device_id TEXT,                  -- アップロード元の物理端末ID
        original_path TEXT               -- アップロード元のオリジナルファイルパス
    )
    """)
    
    # マイグレーション処理 (device_id, original_path カラム追加)
    try:
        cursor.execute("ALTER TABLE assets ADD COLUMN device_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE assets ADD COLUMN original_path TEXT")
    except sqlite3.OperationalError:
        pass
    
    # タイムライングループテーブル (同時撮影・連番でまとめる単位)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model TEXT,
        name TEXT,                       -- グループ表示用タイトル (月別スレッド名など)
        start_time TEXT,
        end_time TEXT,
        seq_start INTEGER,
        seq_end INTEGER,
        asset_count INTEGER,
        assigned_owner TEXT,             -- 'papa', 'mama', 'child', None
        status TEXT DEFAULT 'unselected'  -- 'unselected', 'download', 'keep'
    )
    """)
    
    # groupsテーブルへのnameカラム追加マイグレーション
    try:
        cursor.execute("ALTER TABLE groups ADD COLUMN name TEXT")
    except sqlite3.OperationalError:
        pass
    
    # 設定テーブル (マッピング情報など)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    conn.commit()
    conn.close()

# 起動時に一度初期化を試みる
try:
    init_db()
except Exception as e:
    print("Database auto-init failed:", e)
