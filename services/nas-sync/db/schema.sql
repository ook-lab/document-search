-- ==========================================
-- 1. デバイス（端末）管理テーブル
-- ==========================================
CREATE TABLE IF NOT EXISTS devices (
    device_id SERIAL PRIMARY KEY,
    device_name VARCHAR(100) NOT NULL,
    os_type VARCHAR(20) NOT NULL,
    last_connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 2. 簡易認証用トークン管理テーブル（追加）
-- ==========================================
CREATE TABLE IF NOT EXISTS api_tokens (
    token_id SERIAL PRIMARY KEY,
    device_id INT REFERENCES devices(device_id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE, -- トークンのSHA-256ハッシュ
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 3. 同期フォルダ（マスター）テーブル
-- ==========================================
CREATE TABLE IF NOT EXISTS sync_folders (
    folder_id SERIAL PRIMARY KEY,
    device_id INT REFERENCES devices(device_id) ON DELETE CASCADE,
    local_path VARCHAR(512) NOT NULL,
    virtual_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 4. ファイルメタデータテーブル
-- ==========================================
CREATE TABLE IF NOT EXISTS files (
    file_id SERIAL PRIMARY KEY,
    folder_id INT REFERENCES sync_folders(folder_id) ON DELETE CASCADE,
    relative_path VARCHAR(1024) NOT NULL, -- フォルダ内相対パス
    file_name VARCHAR(255) NOT NULL,
    file_size BIGINT NOT NULL,
    file_hash VARCHAR(64) NOT NULL,       -- PC側で計算したSHA-256
    last_modified_at TIMESTAMP NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- 同一フォルダ内での相対パスの重複を防ぐ（改善提案のユニーク制約）
    CONSTRAINT unique_folder_relative_path UNIQUE (folder_id, relative_path)
);

-- ==========================================
-- 4.5 デバイスごとの同期ステータス追跡テーブル（ゾンビ復活防止用）
-- ==========================================
CREATE TABLE IF NOT EXISTS file_sync_status (
    device_id INT REFERENCES devices(device_id) ON DELETE CASCADE,
    file_id INT REFERENCES files(file_id) ON DELETE CASCADE,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (device_id, file_id)
);

-- ==========================================
-- 4.6 マスターマッピング（共有マスター、最終更新時間管理）
-- ==========================================
CREATE TABLE IF NOT EXISTS master_mapping (
    file_id SERIAL PRIMARY KEY,
    virtual_name VARCHAR(100) NOT NULL,
    relative_path VARCHAR(1024) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    is_directory BOOLEAN NOT NULL DEFAULT FALSE,
    file_size BIGINT NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    last_modified_at TIMESTAMP NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    is_uploaded BOOLEAN NOT NULL DEFAULT FALSE,
    last_modified_by_device_id INT REFERENCES devices(device_id) ON DELETE SET NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_master_path UNIQUE (virtual_name, relative_path)
);

-- ==========================================
-- 4.7 デバイス別同期マッピング（PC個別の前回同期状態）
-- ==========================================
CREATE TABLE IF NOT EXISTS device_mapping (
    device_id INT REFERENCES devices(device_id) ON DELETE CASCADE,
    file_id INT REFERENCES master_mapping(file_id) ON DELETE CASCADE,
    last_synced_mtime TIMESTAMP NOT NULL,
    last_synced_size BIGINT NOT NULL,
    last_synced_hash VARCHAR(64) NOT NULL,
    PRIMARY KEY (device_id, file_id)
);

CREATE INDEX IF NOT EXISTS idx_master_mapping_lookup ON master_mapping(virtual_name, is_deleted);

-- パフォーマンス向上のためのインデックス設定


CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash);
CREATE INDEX IF NOT EXISTS idx_files_lookup ON files(folder_id, is_deleted);

-- ==========================================
-- 5. アクティビティ履歴（同期ログ）テーブル
-- ==========================================
CREATE TABLE IF NOT EXISTS activity_logs (
    log_id SERIAL PRIMARY KEY,
    device_id INT REFERENCES devices(device_id) ON DELETE SET NULL,
    action_type VARCHAR(20) NOT NULL, -- 'CREATE', 'UPDATE', 'DELETE', 'CONFLICT'
    file_path VARCHAR(1024) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 6. 同期タスクリスト（ズレ解消キュー、デバイス個別管理）
-- ==========================================
CREATE TABLE IF NOT EXISTS sync_tasks (
    task_id SERIAL PRIMARY KEY,
    device_id INT REFERENCES devices(device_id) ON DELETE CASCADE,
    folder_id INT REFERENCES sync_folders(folder_id) ON DELETE CASCADE,
    relative_path VARCHAR(1024) NOT NULL,
    to_path VARCHAR(1024) DEFAULT '',
    action_type VARCHAR(20) NOT NULL, -- 'upload', 'download', 'delete_local', 'move'
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    file_size BIGINT NOT NULL DEFAULT 0,
    file_hash VARCHAR(64) NOT NULL DEFAULT '',
    last_modified_by VARCHAR(100) DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sync_tasks_device ON sync_tasks(device_id, status);
