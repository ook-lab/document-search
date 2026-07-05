use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{self, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
#[cfg(windows)]
use std::os::windows::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};
use tokio::sync::{mpsc, Semaphore};
use tokio::task::JoinSet;

const PARALLEL_TASKS: usize = 16;
use serde::{Serialize, Deserialize};
use sha2::{Sha256, Digest};
use notify::{Watcher, RecursiveMode, Event, RecommendedWatcher};
use tauri::{AppHandle, Manager, Emitter};
use glob;


// --- Scan Event ---

pub enum ScanEvent {
    Full,
    Paths(Vec<PathBuf>),
}


// --- Config and State Structs ---

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct SyncFolder {
    pub folder_id: i32,
    pub local_path: String,
    pub virtual_name: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Config {
    pub nas_url: String,
    pub api_token: String,
    pub device_id: i32,
    pub sync_folders: Vec<SyncFolder>,
    pub exclude_patterns: Vec<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct SyncStatus {
    pub is_syncing: bool,
    pub current_file: String,
    pub pending_tasks: i32,
    pub message: String,
}

pub struct AppState {
    pub config: Mutex<Config>,
    pub status: Mutex<SyncStatus>,
    pub scan_tx: mpsc::Sender<ScanEvent>,
    pub watcher: Mutex<Option<RecommendedWatcher>>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "snake_case")]
pub struct SyncQueueItem {
    pub file_name: String,
    pub relative_path: String,
    pub direction: String,
    pub status: String,
    pub progress: f32,
}


// --- Helper: SHA-256 Hash ---

pub fn calculate_hash(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0; 8192];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 { break; }
        hasher.update(&buffer[..count]);
    }
    Ok(hex::encode(hasher.finalize()))
}

pub fn get_file_unique_id(metadata: &fs::Metadata) -> u64 {
    #[cfg(unix)]
    { metadata.ino() }
    #[cfg(windows)]
    { metadata.creation_time() }
    #[cfg(not(any(unix, windows)))]
    { 0 }
}


// --- Helper: Hash Cache ---

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct CacheEntry {
    pub mtime_ns: i64,
    pub size: i64,
    pub hash: String,
}

pub fn get_cache_path(app: &AppHandle) -> PathBuf {
    let mut path = app.path().app_config_dir().unwrap_or_else(|_| PathBuf::from("."));
    let _ = fs::create_dir_all(&path);
    path.push("hash_cache.json");
    path
}

pub fn load_hash_cache(app: &AppHandle) -> HashMap<String, CacheEntry> {
    let path = get_cache_path(app);
    if let Ok(mut file) = File::open(path) {
        let mut content = String::new();
        if file.read_to_string(&mut content).is_ok() {
            if let Ok(cache) = serde_json::from_str::<HashMap<String, CacheEntry>>(&content) {
                return cache;
            }
        }
    }
    HashMap::new()
}

pub fn save_hash_cache(app: &AppHandle, cache: &HashMap<String, CacheEntry>) {
    let path = get_cache_path(app);
    if let Ok(content) = serde_json::to_string(cache) {
        if let Ok(mut file) = File::create(path) {
            let _ = file.write_all(content.as_bytes());
        }
    }
}


// --- Helper: Config IO ---

pub fn get_config_path(app: &AppHandle) -> PathBuf {
    let mut path = app.path().app_config_dir().unwrap_or_else(|_| PathBuf::from("."));
    let _ = fs::create_dir_all(&path);
    path.push("config.json");
    path
}

pub fn load_config_file(app: &AppHandle) -> Config {
    let path = get_config_path(app);
    if let Ok(mut file) = File::open(path) {
        let mut content = String::new();
        if file.read_to_string(&mut content).is_ok() {
            if let Ok(mut config) = serde_json::from_str::<Config>(&content) {
                if config.exclude_patterns.is_empty() {
                    config.exclude_patterns = default_exclude_patterns();
                }
                return config;
            }
        }
    }
    Config {
        nas_url: "http://100.82.85.101:8080".to_string(),
        exclude_patterns: default_exclude_patterns(),
        ..Config::default()
    }
}

fn default_exclude_patterns() -> Vec<String> {
    vec![
        "node_modules".to_string(),
        ".git".to_string(),
        "*.tmp".to_string(),
        "~*".to_string(),
        "*.log".to_string(),
        ".DS_Store".to_string(),
        "._*".to_string(),
    ]
}

pub fn save_config_file(app: &AppHandle, config: &Config) -> io::Result<()> {
    let path = get_config_path(app);
    let content = serde_json::to_string_pretty(config)?;
    fs::write(path, content)
}


// --- API Client Types ---

#[derive(Serialize, Deserialize, Clone, Debug)]
struct RegisterDeviceRequest {
    device_name: String,
    os_type: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct RegisterDeviceResponse {
    device_id: i32,
    token: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct RegisterFolderRequest {
    local_path: String,
    virtual_name: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct RegisterFolderResponse {
    folder_id: i32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct FileMetadata {
    relative_path: String,
    file_name: String,
    is_directory: bool,
    file_size: i64,
    file_hash: String,
    last_modified_at: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct ScanRequest {
    folder_id: i32,
    files: Vec<FileMetadata>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "snake_case")]
pub struct SyncTask {
    pub task_id: i32,
    pub relative_path: String,
    #[serde(default)]
    pub to_path: String,
    pub action_type: String,
    pub file_size: i64,
    pub file_hash: String,
    #[serde(default)]
    pub last_modified_by: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct ScanResponse {
    tasks: Vec<SyncTask>,
    #[serde(default)]
    total_pending: i32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct MoveRequest {
    folder_id: i32,
    from_path: String,
    to_path: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct DeleteRequest {
    folder_id: i32,
    relative_path: String,
}


// --- Status / Queue Helpers ---

fn update_status(app: &AppHandle, is_syncing: bool, current_file: String, pending_tasks: i32, message: String) {
    let state = app.state::<Arc<AppState>>();
    let mut status = state.status.lock().unwrap();
    status.is_syncing = is_syncing;
    status.current_file = current_file;
    if pending_tasks >= 0 { status.pending_tasks = pending_tasks; }
    status.message = message;
    let _ = app.emit("sync-status", status.clone());
}

fn emit_queue_update(app: &AppHandle, relative_path: &str, status: &str, progress: f32) {
    #[derive(serde::Serialize, Clone)]
    #[serde(rename_all = "snake_case")]
    struct QueueUpdate { relative_path: String, status: String, progress: f32 }
    let _ = app.emit("sync-queue-update", QueueUpdate {
        relative_path: relative_path.to_string(),
        status: status.to_string(),
        progress,
    });
}

// --- Local File Scanner ---

fn is_excluded(name: &str, relative_path: &str, patterns: &[String]) -> bool {
    for pat in patterns {
        if let Ok(pattern) = glob::Pattern::new(pat) {
            if pattern.matches(name) { return true; }
            if pattern.matches(relative_path) { return true; }
            for part in relative_path.split('/') {
                if pattern.matches(part) { return true; }
            }
        }
    }
    false
}

fn scan_local_directory(_app: &AppHandle, dir: &Path, exclude_patterns: &[String], cache: &mut HashMap<String, CacheEntry>) -> io::Result<Vec<FileMetadata>> {
    let mut files = Vec::new();
    scan_recursive(dir, dir, &mut files, exclude_patterns, cache)?;
    Ok(files)
}

fn scan_recursive(base_dir: &Path, current_dir: &Path, list: &mut Vec<FileMetadata>, exclude_patterns: &[String], cache: &mut HashMap<String, CacheEntry>) -> io::Result<()> {
    if !current_dir.is_dir() { return Ok(()); }
    for entry in fs::read_dir(current_dir)? {
        let entry = entry?;
        let path = entry.path();
        let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
        if name.starts_with('.') || name == "$RECYCLE.BIN" { continue; }

        let relative_path = path.strip_prefix(base_dir)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?
            .to_string_lossy()
            .replace('\\', "/");

        if is_excluded(&name, &relative_path, exclude_patterns) { continue; }

        if path.is_dir() {
            let metadata = entry.metadata()?;
            let modified_time = metadata.modified().unwrap_or(SystemTime::now());
            let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();
            list.push(FileMetadata {
                relative_path: relative_path.clone(),
                file_name: name.clone(),
                is_directory: true,
                file_size: 0,
                file_hash: "".to_string(),
                last_modified_at: datetime.to_rfc3339(),
            });
            scan_recursive(base_dir, &path, list, exclude_patterns, cache)?;
        } else {
            let metadata = entry.metadata()?;
            let size = metadata.len() as i64;
            let modified_time = metadata.modified().unwrap_or(SystemTime::now());
            let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();
            let mtime_ns = match modified_time.duration_since(SystemTime::UNIX_EPOCH) {
                Ok(d) => d.as_nanos() as i64,
                Err(_) => 0,
            };
            let unique_id = get_file_unique_id(&metadata);
            let cache_key = unique_id.to_string();
            let mut hash = String::new();
            let mut use_cache = false;
            if unique_id != 0 {
                if let Some(c_entry) = cache.get(&cache_key) {
                    if c_entry.mtime_ns == mtime_ns && c_entry.size == size {
                        hash = c_entry.hash.clone();
                        use_cache = true;
                    }
                }
            }
            if !use_cache {
                hash = calculate_hash(&path).unwrap_or_default();
                if unique_id != 0 {
                    cache.insert(cache_key, CacheEntry { mtime_ns, size, hash: hash.clone() });
                }
            }
            list.push(FileMetadata {
                relative_path,
                file_name: name,
                is_directory: false,
                file_size: size,
                file_hash: hash,
                last_modified_at: datetime.to_rfc3339(),
            });
        }
    }
    Ok(())
}


// --- Scan Loop: Full Scan ---

async fn do_full_scan_all(app: &AppHandle) {
    let config = app.state::<Arc<AppState>>().config.lock().unwrap().clone();
    if config.nas_url.is_empty() || config.api_token.is_empty() || config.sync_folders.is_empty() {
        return;
    }

    let client = reqwest::Client::new();
    let mut cache = load_hash_cache(app);

    for folder in &config.sync_folders {
        let local_dir = Path::new(&folder.local_path);
        if !local_dir.exists() { continue; }

        update_status(app, true, "".to_string(), -1, format!("{} をスキャン中...", folder.virtual_name));

        let local_files = match scan_local_directory(app, local_dir, &config.exclude_patterns, &mut cache) {
            Ok(files) => files,
            Err(e) => {
                update_status(app, false, "".to_string(), -1, format!("ローカルスキャンエラー({}): {}", folder.virtual_name, e));
                continue;
            }
        };

        let scan_url = format!("{}/api/sync/scan", config.nas_url);
        update_status(app, true, "".to_string(), -1, "サーバーと差分を比較中...".to_string());

        match client.post(&scan_url)
            .header("Authorization", format!("Bearer {}", config.api_token))
            .json(&ScanRequest { folder_id: folder.folder_id, files: local_files })
            .send()
            .await
        {
            Ok(res) if !res.status().is_success() => {
                let status = res.status();
                let body = res.text().await.unwrap_or_default();
                update_status(app, false, "".to_string(), -1,
                    format!("スキャンエラー HTTP {}: {}", status, body.chars().take(100).collect::<String>()));
            }
            Err(e) => {
                update_status(app, false, "".to_string(), -1, format!("サーバー接続エラー: {}", e));
            }
            _ => {}
        }
    }

    save_hash_cache(app, &cache);
}


// --- Scan Loop: Watcher-Triggered Targeted Handling ---

async fn handle_changed_paths(app: &AppHandle, paths: &[PathBuf]) {
    let config = app.state::<Arc<AppState>>().config.lock().unwrap().clone();
    if config.nas_url.is_empty() || config.api_token.is_empty() { return; }

    let client = reqwest::Client::new();

    for folder in &config.sync_folders {
        let folder_path = Path::new(&folder.local_path);

        for path in paths {
            if !path.starts_with(folder_path) { continue; }

            let name = path.file_name().unwrap_or_default().to_string_lossy();
            if name.starts_with('.') || name.ends_with('~') || name.ends_with(".tmp") { continue; }

            let rel_path = match path.strip_prefix(folder_path) {
                Ok(p) => p.to_string_lossy().replace('\\', "/"),
                Err(_) => continue,
            };
            if rel_path.is_empty() { continue; }

            if is_excluded(&name, &rel_path, &config.exclude_patterns) { continue; }

            if path.exists() && path.is_file() {
                upload_file_direct(app, &client, &config, folder, path, &rel_path).await;
            } else if !path.exists() {
                delete_remote_direct(&client, &config, folder, &rel_path).await;
            }
            // Directories and renames: handled by next periodic full scan
        }
    }
}

async fn upload_file_direct(
    app: &AppHandle,
    client: &reqwest::Client,
    config: &Config,
    folder: &SyncFolder,
    path: &Path,
    rel_path: &str,
) {
    let bytes = match fs::read(path) { Ok(b) => b, Err(_) => return };
    let metadata = match fs::metadata(path) { Ok(m) => m, Err(_) => return };
    let hash = calculate_hash(path).unwrap_or_default();
    let size = bytes.len() as i64;
    let modified_time = metadata.modified().unwrap_or(SystemTime::now());
    let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();
    let file_name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();

    let form = reqwest::multipart::Form::new()
        .text("folder_id", folder.folder_id.to_string())
        .text("relative_path", rel_path.to_string())
        .text("file_size", size.to_string())
        .text("file_hash", hash)
        .text("last_modified_at", datetime.to_rfc3339())
        .part("file", reqwest::multipart::Part::bytes(bytes).file_name(file_name));

    let upload_url = format!("{}/api/sync/upload", config.nas_url);
    match client.post(&upload_url)
        .header("Authorization", format!("Bearer {}", config.api_token))
        .multipart(form)
        .send()
        .await
    {
        Ok(res) if res.status().is_success() => {
            update_status(app, true, rel_path.to_string(), -1, format!("アップロード: {}", rel_path));
        }
        Ok(res) => {
            let status = res.status();
            let body = res.text().await.unwrap_or_default();
            update_status(app, false, rel_path.to_string(), -1,
                format!("アップロードエラー {}: {}", status, body.chars().take(100).collect::<String>()));
        }
        Err(e) => {
            update_status(app, false, rel_path.to_string(), -1, format!("アップロード接続エラー: {}", e));
        }
    }
}

async fn delete_remote_direct(
    client: &reqwest::Client,
    config: &Config,
    folder: &SyncFolder,
    rel_path: &str,
) {
    let delete_url = format!("{}/api/sync/delete", config.nas_url);
    let _ = client.delete(&delete_url)
        .header("Authorization", format!("Bearer {}", config.api_token))
        .json(&DeleteRequest { folder_id: folder.folder_id, relative_path: rel_path.to_string() })
        .send()
        .await;
}

// --- Scan Loop (runs forever, parallel to execute loop) ---

pub async fn run_scan_loop(app: AppHandle, mut rx: mpsc::Receiver<ScanEvent>) {
    tokio::time::sleep(Duration::from_secs(3)).await;

    // Initial full scan
    do_full_scan_all(&app).await;

    let mut periodic = tokio::time::interval(Duration::from_secs(300)); // 5 minutes
    periodic.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    periodic.tick().await; // skip first immediate tick

    loop {
        tokio::select! {
            event = rx.recv() => {
                match event {
                    Some(ScanEvent::Full) => {
                        do_full_scan_all(&app).await;
                        periodic.reset(); // restart 5-min timer after forced full scan
                    }
                    Some(ScanEvent::Paths(paths)) => {
                        // Debounce: accumulate events for 1 second
                        tokio::time::sleep(Duration::from_millis(1000)).await;
                        let mut all_paths = paths;
                        let mut need_full = false;
                        while let Ok(more) = rx.try_recv() {
                            match more {
                                ScanEvent::Paths(p) => all_paths.extend(p),
                                ScanEvent::Full => { need_full = true; break; }
                            }
                        }
                        if need_full {
                            do_full_scan_all(&app).await;
                            periodic.reset();
                        } else {
                            handle_changed_paths(&app, &all_paths).await;
                        }
                    }
                    None => break,
                }
            }
            _ = periodic.tick() => {
                do_full_scan_all(&app).await;
            }
        }
    }
}


// --- Execute Loop (runs forever, parallel to scan loop) ---

pub async fn run_execute_loop(app: AppHandle) {
    let client = reqwest::Client::new();

    loop {
        let config = app.state::<Arc<AppState>>().config.lock().unwrap().clone();

        if config.nas_url.is_empty() || config.api_token.is_empty() || config.sync_folders.is_empty() {
            tokio::time::sleep(Duration::from_secs(5)).await;
            continue;
        }

        let mut total_executed: usize = 0;
        let mut got_full_batch = false;

        for folder in &config.sync_folders {
            let local_dir = Path::new(&folder.local_path);
            if !local_dir.exists() { continue; }

            let tasks_url = format!("{}/api/sync/tasks?folder_id={}", config.nas_url, folder.folder_id);

            let res = match client.get(&tasks_url)
                .header("Authorization", format!("Bearer {}", config.api_token))
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => r,
                _ => continue,
            };

            let response = match res.json::<ScanResponse>().await {
                Ok(r) => r,
                Err(_) => continue,
            };
            let tasks = response.tasks;
            let total_pending = response.total_pending;

            if tasks.is_empty() { continue; }
            if tasks.len() >= 200 { got_full_batch = true; }

            total_executed += tasks.len();
            let _task_count = tasks.len();

            // Emit initial queue to UI
            let queue: Vec<SyncQueueItem> = tasks.iter().map(|t| SyncQueueItem {
                file_name: if t.action_type == "move" {
                    format!("{} ➔ {}",
                        Path::new(&t.relative_path).file_name().unwrap_or_default().to_string_lossy(),
                        Path::new(&t.to_path).file_name().unwrap_or_default().to_string_lossy())
                } else {
                    Path::new(&t.relative_path).file_name().unwrap_or_default().to_string_lossy().into_owned()
                },
                relative_path: if t.action_type == "move" { t.to_path.clone() } else { t.relative_path.clone() },
                direction: t.action_type.clone(),
                status: "pending".to_string(),
                progress: 0.0,
            }).collect();

            #[derive(serde::Serialize, Clone)]
            #[serde(rename_all = "snake_case")]
            struct QueueInitPayload { items: Vec<SyncQueueItem> }
            let _ = app.emit("sync-queue-init", QueueInitPayload { items: queue });

            let semaphore = Arc::new(Semaphore::new(PARALLEL_TASKS));
            let mut join_set = JoinSet::new();

            for (i, task) in tasks.iter().enumerate() {
                let target_path = if task.action_type == "move" { task.to_path.clone() } else { task.relative_path.clone() };
                emit_queue_update(&app, &target_path, "processing", 0.0);

                let remaining = (total_pending as usize).saturating_sub(i) as i32;
                let status_msg = match task.action_type.as_str() {
                    "upload_new"       => "新規アップロード中...",
                    "upload_overwrite" => "上書きアップロード中...",
                    "download_new"     => "新規ダウンロード中...",
                    "download_overwrite" => "上書きダウンロード中...",
                    "delete_local"     => "ローカルファイルを削除中...",
                    "move"             => "ファイルを移動中...",
                    _                  => "同期処理中...",
                };
                update_status(&app, true, target_path.clone(), remaining, status_msg.to_string());

                let permit = semaphore.clone().acquire_owned().await.unwrap();
                let app_c = app.clone();
                let client_c = client.clone();
                let config_c = config.clone();
                let folder_c = folder.clone();
                let task_c = task.clone();
                let local_dir_c = local_dir.to_path_buf();

                join_set.spawn(async move {
                    let _permit = permit;
                    let success = execute_single_task(&app_c, &client_c, &config_c, &folder_c, &task_c, &local_dir_c).await;
                    let t = if task_c.action_type == "move" { &task_c.to_path } else { &task_c.relative_path };
                    emit_queue_update(&app_c, t, if success { "completed" } else { "failed" }, if success { 1.0 } else { 0.0 });
                });
            }

            while join_set.join_next().await.is_some() {}
        }

        if total_executed == 0 {
            update_status(&app, false, "".to_string(), 0, "同期完了".to_string());
            tokio::time::sleep(Duration::from_secs(3)).await;
        } else if got_full_batch {
            // Immediately loop to fetch next 200 tasks (no sleep)
        } else {
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }
}


// --- Execute Single Task ---

async fn execute_single_task(
    app: &AppHandle,
    client: &reqwest::Client,
    config: &Config,
    folder: &SyncFolder,
    task: &SyncTask,
    local_dir: &Path,
) -> bool {
    let mut success = false;

    match task.action_type.as_str() {
        "delete_local" => {
            let local_path = local_dir.join(&task.relative_path);
            if local_path.exists() {
                let rm_res = if local_path.is_dir() {
                    fs::remove_dir_all(&local_path)
                } else {
                    fs::remove_file(&local_path)
                };
                success = rm_res.is_ok();
            } else {
                success = true;
            }
            if success {
                let delete_url = format!("{}/api/sync/delete", config.nas_url);
                match client.delete(&delete_url)
                    .header("Authorization", format!("Bearer {}", config.api_token))
                    .json(&DeleteRequest { folder_id: folder.folder_id, relative_path: task.relative_path.clone() })
                    .send().await
                {
                    Ok(res) => success = res.status().is_success(),
                    Err(_) => success = false,
                }
            }
        }

        "move" => {
            let local_src = local_dir.join(&task.relative_path);
            let local_dst = local_dir.join(&task.to_path);
            if local_dst.exists() {
                success = true;
            } else if local_src.exists() {
                if let Some(parent) = local_dst.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                success = fs::rename(&local_src, &local_dst).is_ok();
            }
            if success {
                let move_url = format!("{}/api/sync/move", config.nas_url);
                match client.post(&move_url)
                    .header("Authorization", format!("Bearer {}", config.api_token))
                    .json(&MoveRequest { folder_id: folder.folder_id, from_path: task.relative_path.clone(), to_path: task.to_path.clone() })
                    .send().await
                {
                    Ok(res) => success = res.status().is_success(),
                    Err(_) => success = false,
                }
            }
        }

        "download_new" | "download_overwrite" => {
            let download_url = format!("{}/api/sync/download", config.nas_url);
            match client.get(&download_url)
                .header("Authorization", format!("Bearer {}", config.api_token))
                .query(&[("folder_id", folder.folder_id.to_string()), ("relative_path", task.relative_path.clone())])
                .send().await
            {
                Ok(res) if res.status().is_success() => {
                    let local_path = local_dir.join(&task.relative_path);
                    if let Some(parent) = local_path.parent() {
                        let _ = fs::create_dir_all(parent);
                    }
                    if let Ok(bytes) = res.bytes().await {
                        if bytes.as_ref() == b"Directory synced" {
                            let _ = fs::create_dir_all(&local_path);
                            success = true;
                        } else {
                            // The client makes no decisions about conflicts at all -- that
                            // logic lives entirely server-side, in master_mapping. Just
                            // write; if the file is locked, this fails and the task stays
                            // pending for retry next cycle.
                            success = fs::write(&local_path, &bytes).is_ok();
                        }
                    }
                }
                Ok(res) => {
                    let status = res.status();
                    let body = res.text().await.unwrap_or_default();
                    update_status(app, false, task.relative_path.clone(), -1,
                        format!("ダウンロードエラー HTTP {}: {}", status, body.chars().take(150).collect::<String>()));
                }
                Err(e) => {
                    update_status(app, false, task.relative_path.clone(), -1, format!("ダウンロード接続エラー: {}", e));
                }
            }
        }

        "upload_new" | "upload_overwrite" => {
            let local_path = local_dir.join(&task.relative_path);
            if local_path.exists() {
                if local_path.is_dir() {
                    success = true;
                } else if let Ok(bytes) = fs::read(&local_path) {
                    let metadata = fs::metadata(&local_path).unwrap();
                    let modified_time = metadata.modified().unwrap_or(SystemTime::now());
                    let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();

                    let form = reqwest::multipart::Form::new()
                        .text("folder_id", folder.folder_id.to_string())
                        .text("relative_path", task.relative_path.clone())
                        .text("file_size", task.file_size.to_string())
                        .text("file_hash", task.file_hash.clone())
                        .text("last_modified_at", datetime.to_rfc3339())
                        .part("file", reqwest::multipart::Part::bytes(bytes).file_name(task.relative_path.clone()));

                    let upload_url = format!("{}/api/sync/upload", config.nas_url);
                    match client.post(&upload_url)
                        .header("Authorization", format!("Bearer {}", config.api_token))
                        .multipart(form)
                        .send().await
                    {
                        Ok(res) => success = res.status().is_success(),
                        Err(_) => success = false,
                    }
                }
            }
        }

        _ => {}
    }

    if success {
        let complete_url = format!("{}/api/sync/tasks/complete?task_id={}", config.nas_url, task.task_id);
        let _ = client.post(&complete_url)
            .header("Authorization", format!("Bearer {}", config.api_token))
            .send()
            .await;
    }

    success
}


// --- Watcher ---

pub fn start_watcher(paths: Vec<String>, scan_tx: mpsc::Sender<ScanEvent>) -> Result<RecommendedWatcher, String> {
    let mut watcher = notify::recommended_watcher(move |res: Result<Event, notify::Error>| {
        if let Ok(event) = res {
            let changed: Vec<PathBuf> = event.paths.into_iter()
                .filter(|path| {
                    let name = path.file_name().unwrap_or_default().to_string_lossy();
                    !name.starts_with('.') && name != "node_modules" && !name.ends_with('~') && !name.ends_with(".tmp")
                })
                .collect();
            if !changed.is_empty() {
                let _ = scan_tx.blocking_send(ScanEvent::Paths(changed));
            }
        }
    }).map_err(|e| e.to_string())?;

    for path_str in paths {
        let path = Path::new(&path_str);
        if path.exists() {
            let _ = watcher.watch(path, RecursiveMode::Recursive);
        }
    }

    Ok(watcher)
}


// --- API Helpers ---

pub async fn api_register_device(nas_url: &str, device_name: &str) -> Result<(i32, String), String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/devices/register", nas_url);
    let req = RegisterDeviceRequest {
        device_name: device_name.to_string(),
        os_type: std::env::consts::OS.to_string(),
    };
    let res = client.post(&url).json(&req).send().await
        .map_err(|e| format!("ネットワーク接続エラー: {}", e))?;
    if !res.status().is_success() {
        return Err(format!("サーバーエラー: ステータス {}", res.status()));
    }
    let data: RegisterDeviceResponse = res.json().await
        .map_err(|e| format!("レスポンスの解析に失敗しました: {}", e))?;
    Ok((data.device_id, data.token))
}

pub async fn api_register_folder(nas_url: &str, token: &str, local_path: &str, virtual_name: &str) -> Result<i32, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/sync/folders", nas_url);
    let req = RegisterFolderRequest {
        local_path: local_path.to_string(),
        virtual_name: virtual_name.to_string(),
    };
    let res = client.post(&url)
        .header("Authorization", format!("Bearer {}", token))
        .json(&req)
        .send().await
        .map_err(|e| format!("ネットワーク接続エラー: {}", e))?;
    if !res.status().is_success() {
        return Err(format!("フォルダーの登録に失敗しました: ステータス {}", res.status()));
    }
    let data: RegisterFolderResponse = res.json().await
        .map_err(|e| format!("レスポンスの解析に失敗しました: {}", e))?;
    Ok(data.folder_id)
}
