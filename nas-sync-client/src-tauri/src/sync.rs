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
use tauri::{AppHandle, Manager, Emitter, Runtime};
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
    pub debug_emit_error_count: i32,
}

pub struct AppState {
    pub config: Mutex<Config>,
    pub status: Mutex<SyncStatus>,
    pub scan_tx: mpsc::Sender<ScanEvent>,
    pub watcher: Mutex<Option<RecommendedWatcher>>,
    // Mirrors the last "sync-queue-init" payload pushed to the frontend. The
    // frontend's listen("sync-queue-init") registration races the backend's
    // first emit at startup (Tauri drops events emitted before a listener is
    // attached), so the frontend also pulls this directly once on init
    // instead of relying solely on having caught the push.
    pub current_queue: Mutex<Vec<SyncQueueItem>>,
    // Content hash of the last watcher-triggered direct upload that the server
    // accepted, keyed by "folder_id:relative_path". The OS file-watcher can
    pub hash_cache: Mutex<HashMap<String, CacheEntry>>,
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


// --- Helper: Windows extended-length path (\\?\) ---
//
// Win32's traditional path length limit is 260 characters (MAX_PATH). Nested
// folders with Japanese file/folder names routinely exceed this once joined
// with the sync root, causing fs:: calls to fail outright. The `\\?\` (or
// `\\?\UNC\` for network shares) prefix switches the same underlying Win32
// calls into "extended-length path" mode, raising the limit to ~32,767
// characters, honored by std::fs with no OS-wide configuration change needed.
// Applied only right before a filesystem syscall -- comparisons, hash cache
// keys, and the relative paths sent to the server all keep using plain paths.
#[cfg(windows)]
pub fn to_extended_path<P: AsRef<Path>>(path: P) -> PathBuf {
    let path = path.as_ref();
    if !path.is_absolute() {
        return path.to_path_buf();
    }
    // Verbatim (\\?\) paths are passed to the kernel nearly as-is, so unlike
    // a normal Windows path, `/` is not accepted as a separator -- relative
    // paths arriving from the server use `/` and must be normalized first.
    let normalized = path.to_string_lossy().replace('/', "\\");
    if normalized.starts_with(r"\\?\") {
        return PathBuf::from(normalized);
    }
    if let Some(rest) = normalized.strip_prefix(r"\\") {
        PathBuf::from(format!(r"\\?\UNC\{}", rest))
    } else {
        PathBuf::from(format!(r"\\?\{}", normalized))
    }
}

#[cfg(not(windows))]
pub fn to_extended_path<P: AsRef<Path>>(path: P) -> PathBuf {
    path.as_ref().to_path_buf()
}

// --- Helper: Streamed Upload Body ---
//
// Builds a multipart Part that reads the file from disk as it's sent, instead
// of buffering the whole thing into a Vec<u8> first. With PARALLEL_TASKS
// concurrent uploads, buffering full file contents multiplies peak memory by
// the concurrency count -- this was observed causing OOM-driven connection
// resets ("unexpected EOF") for large files.
async fn stream_file_part(path: &Path, file_name: String) -> io::Result<reqwest::multipart::Part> {
    let file = tokio::fs::File::open(path).await?;
    let len = file.metadata().await?.len();
    let stream = tokio_util::codec::FramedRead::new(file, tokio_util::codec::BytesCodec::new());
    let body = reqwest::Body::wrap_stream(stream);
    Ok(reqwest::multipart::Part::stream_with_length(body, len).file_name(file_name))
}

// --- Helper: Local Quarantine (soft delete) ---
//
// Moves a local file or directory into a per-deletion, timestamped folder
// under <local_dir>/.sugarsync-trash instead of permanently removing it, so a
// local deletion -- whether genuinely intended or triggered by a bug in the
// sync logic (as happened once already in this system, destroying tens of
// thousands of files with no way back) -- stays recoverable instead of
// vanishing instantly. ".sugarsync-trash" is in default_exclude_patterns so
// it's never itself picked up as sync content.
fn quarantine_local_path(local_dir: &Path, relative_path: &str, source: &Path) -> io::Result<()> {
    let nanos = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let trash_path = to_extended_path(
        local_dir.join(".sugarsync-trash").join(nanos.to_string()).join(relative_path)
    );
    if let Some(parent) = trash_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::rename(source, &trash_path)
}

// --- Helper: SHA-256 Hash ---

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex::encode(hasher.finalize())
}

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
    #[serde(default)]
    pub file_id: Option<i64>,
    #[serde(default)]
    pub last_known_path: Option<String>,
    #[serde(default)]
    pub version_num: Option<i32>,
}

pub fn get_cache_path<R: Runtime>(app: &AppHandle<R>) -> PathBuf {
    let mut path = app.path().app_config_dir().unwrap_or_else(|_| PathBuf::from("."));
    let _ = fs::create_dir_all(&path);
    path.push("hash_cache.json");
    path
}

pub fn load_hash_cache<R: Runtime>(app: &AppHandle<R>) -> HashMap<String, CacheEntry> {
    let path = get_cache_path(app);
    match File::open(&path) {
        Ok(mut file) => {
            let mut content = String::new();
            match file.read_to_string(&mut content) {
                Ok(_) => match serde_json::from_str::<HashMap<String, CacheEntry>>(&content) {
                    Ok(cache) => return cache,
                    Err(e) => eprintln!("Failed to parse hash cache at {:?}: {}", path, e),
                },
                Err(e) => eprintln!("Failed to read hash cache at {:?}: {}", path, e),
            }
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => {}
        Err(e) => eprintln!("Failed to open hash cache at {:?}: {}", path, e),
    }
    HashMap::new()
}

pub fn save_hash_cache<R: Runtime>(app: &AppHandle<R>, cache: &HashMap<String, CacheEntry>) {
    // This cache is purely a hashing-cost optimization -- if it fails to
    // persist, correctness is unaffected (every file's hash just gets
    // recomputed from scratch next scan instead of read from cache), so this
    // deliberately doesn't propagate the error to callers. Logged so a
    // persistently-failing cache write (which would mean every scan pays full
    // rehashing cost) is at least diagnosable instead of invisible.
    let path = get_cache_path(app);
    match serde_json::to_string(cache) {
        Ok(content) => match File::create(&path) {
            Ok(mut file) => {
                if let Err(e) = file.write_all(content.as_bytes()) {
                    eprintln!("Failed to write hash cache to {:?}: {}", path, e);
                }
            }
            Err(e) => eprintln!("Failed to create hash cache file {:?}: {}", path, e),
        },
        Err(e) => eprintln!("Failed to serialize hash cache: {}", e),
    }
}

// --- Helper: 4-Quadrant Local Change Detection ---
//
// Resolves or migrates file identity (file_id, version_num) based on os_file_id (unique_id) and relative path:
// (1) os_file_id unchanged, path unchanged: normal update using existing cached identity.
// (2) os_file_id unchanged, path changed: rename (handled via MoveRequest elsewhere).
// (3) os_file_id changed, same path, same device: atomic save (safe save).
//     Migrates cached file_id and version_num from old os_file_id entry to new os_file_id.
// (4) os_file_id changed, path changed: unconfirmed, treated as a new file (file_id: None).
fn resolve_or_migrate_file_identity(
    cache_guard: &mut HashMap<String, CacheEntry>,
    cache_key: &str,
    relative_path: &str,
) -> (Option<i64>, i32) {
    if let Some(c_entry) = cache_guard.get(cache_key) {
        // Quadrant (1) or (2): os_file_id unchanged
        (c_entry.file_id, c_entry.version_num.unwrap_or(0))
    } else {
        // os_file_id changed
        // Check Quadrant (3): Atomic save (safe save) on the same path & same device
        let matching_old_keys: Vec<String> = cache_guard
            .iter()
            .filter_map(|(k, v)| {
                if k != cache_key && v.last_known_path.as_deref() == Some(relative_path) {
                    Some(k.clone())
                } else {
                    None
                }
            })
            .collect();

        if matching_old_keys.len() > 1 {
            eprintln!(
                "[WARN] Multiple cache entries found matching relative_path '{}' during identity migration (cache_key='{}', count={})",
                relative_path, cache_key, matching_old_keys.len()
            );
        }

        let old_key = matching_old_keys.into_iter().next();

        if let Some(old_k) = old_key {
            // Quadrant (3): Atomic save. Remove old cache entry and inherit file_id & version_num for new os_file_id.
            if let Some(old_entry) = cache_guard.remove(&old_k) {
                (old_entry.file_id, old_entry.version_num.unwrap_or(0))
            } else {
                (None, 0)
            }
        } else {
            // Quadrant (4): os_file_id changed & path changed -> no match, treat as new file
            (None, 0)
        }
    }
}


// --- Helper: Config IO ---

pub fn get_config_path<R: Runtime>(app: &AppHandle<R>) -> PathBuf {
    let mut path = app.path().app_config_dir().unwrap_or_else(|_| PathBuf::from("."));
    let _ = fs::create_dir_all(&path);
    path.push("config.json");
    path
}

pub fn load_config_file<R: Runtime>(app: &AppHandle<R>) -> Config {
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
        nas_url: "http://100.82.85.101:47291".to_string(),
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
        "*.app".to_string(),
    ]
}

pub fn save_config_file<R: Runtime>(app: &AppHandle<R>, config: &Config) -> io::Result<()> {
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
    #[serde(default)]
    file_id: Option<i64>,
    #[serde(default)]
    known_version: i32,
    #[serde(default)]
    os_file_id: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct DeletedPath {
    relative_path: String,
    file_id: i64,
    known_version: i32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct ScanRequest {
    folder_id: i32,
    files: Vec<FileMetadata>,
    #[serde(default)]
    deleted_paths: Vec<DeletedPath>,
    #[serde(default)]
    is_partial_scan: bool,
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
    #[serde(default)]
    pub file_id: i64,
    #[serde(default)]
    pub version: Option<i32>,
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
    file_id: i64,
    expected_version: i32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct MoveResponse {
    #[serde(default)]
    version: Option<i32>,
}



// --- Status / Queue Helpers ---

fn update_status<R: Runtime>(app: &AppHandle<R>, is_syncing: bool, current_file: String, pending_tasks: i32, message: String) {
    let state = app.state::<Arc<AppState>>();
    let mut status = state.status.lock().unwrap();
    status.is_syncing = is_syncing;
    status.current_file = current_file;
    if pending_tasks >= 0 { status.pending_tasks = pending_tasks; }
    status.message = message;
    let _ = app.emit("sync-status", status.clone());
}

// Persistent counter (survives across status messages being overwritten) for
// diagnosing whether sync-queue-init ever fails to emit.
fn record_emit_error<R: Runtime>(app: &AppHandle<R>) {
    let state = app.state::<Arc<AppState>>();
    let mut status = state.status.lock().unwrap();
    status.debug_emit_error_count += 1;
    let _ = app.emit("sync-status", status.clone());
}

fn emit_queue_update<R: Runtime>(app: &AppHandle<R>, relative_path: &str, status: &str, progress: f32) {
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

fn scan_local_directory<R: Runtime>(_app: &AppHandle<R>, dir: &Path, exclude_patterns: &[String], cache: &Mutex<HashMap<String, CacheEntry>>) -> io::Result<Vec<FileMetadata>> {
    let mut files = Vec::new();
    // Extend once at the root: every path produced by walking down from here
    // (via Path::join) keeps the \\?\ prefix, so nested Japanese folder names
    // that push the accumulated length past MAX_PATH still resolve. base_dir
    // and current_dir stay consistently extended together, so strip_prefix
    // below keeps producing the same plain relative paths as before.
    let dir = to_extended_path(dir);
    // The sync root itself must be positively confirmed as an accessible
    // directory before walking it -- Path::is_dir() swallows any error from
    // fs::metadata (locked, transient AV/OneDrive interference, some quirk of
    // the \\?\-prefixed path) and returns `false` indistinguishably from
    // "genuinely not a directory". That silently produced an empty file list
    // that looked like a normal, successful scan of zero files: the server took
    // it at face value and marked everything previously known for this device
    // as locally deleted, propagating that deletion to every other synced
    // device. An error here instead lands in do_full_scan_all's existing
    // "skip this cycle" path, which sends nothing rather than a false report.
    match fs::metadata(&dir) {
        Ok(m) if m.is_dir() => {}
        Ok(_) => return Err(io::Error::other("sync root exists but is not a directory")),
        Err(e) => return Err(e),
    }
    scan_recursive(&dir, &dir, &mut files, exclude_patterns, cache)?;
    Ok(files)
}

fn scan_recursive(base_dir: &Path, current_dir: &Path, list: &mut Vec<FileMetadata>, exclude_patterns: &[String], cache: &Mutex<HashMap<String, CacheEntry>>) -> io::Result<()> {
    // Subdirectories encountered mid-walk are different from the sync root
    // above: we already saw this one via the parent's read_dir, so by the time
    // we get here it either still exists (normal) or was legitimately removed
    // concurrently (a narrow, safe "nothing to do here" -- unlike the root,
    // skipping one already-enumerated subdirectory can't fabricate a false
    // "everything is gone" report for the whole scan).
    match fs::metadata(current_dir) {
        Ok(m) if m.is_dir() => {}
        Ok(_) => return Ok(()),
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e),
    }
    for entry in fs::read_dir(current_dir)? {
        let entry = entry?;
        let path = entry.path();
        let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
        if name.starts_with('.') || name == "$RECYCLE.BIN" { continue; }

        let relative_path = path.strip_prefix(base_dir)
            .map_err(io::Error::other)?
            .to_string_lossy()
            .replace('\\', "/");

        if is_excluded(&name, &relative_path, exclude_patterns) { continue; }

        if path.is_dir() {
            let metadata = entry.metadata()?;
            // If the real mtime can't be read, don't substitute "now" -- that
            // fabricates a fact about this entry (used in conflict-resolution
            // comparisons server-side) instead of reporting what's actually known.
            // Skip it this cycle; the next scan retries once it's readable.
            let modified_time = match metadata.modified() {
                Ok(t) => t,
                Err(_) => continue,
            };
            let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();
            // Directories have no mtime/size staleness check available (CacheEntry
            // mtime_ns/size are only ever meaningfully compared for files), so a cached
            // file_id there cannot be confirmed to still refer to the same directory
            // rather than an OS-recycled unique id from an unrelated, since-deleted directory.
            let cached_file_id = None;
            let dir_unique_id = get_file_unique_id(&metadata);
            let os_file_id = if dir_unique_id != 0 { dir_unique_id.to_string() } else { String::new() };
            // Directories have known_version = 0 (exempted from version check)
            list.push(FileMetadata {
                relative_path: relative_path.clone(),
                file_name: name.clone(),
                is_directory: true,
                file_size: 0,
                file_hash: "".to_string(),
                last_modified_at: datetime.to_rfc3339(),
                file_id: cached_file_id,
                known_version: 0,
                os_file_id,
            });
            scan_recursive(base_dir, &path, list, exclude_patterns, cache)?;
        } else {
            let metadata = entry.metadata()?;
            let size = metadata.len() as i64;
            // Same reasoning as the directory case above: a fabricated "now" mtime
            // could make stale content look like the newest version in a
            // hash-mismatch conflict, silently letting stale bytes win over
            // genuinely newer content from another device. Skip rather than guess.
            let modified_time = match metadata.modified() {
                Ok(t) => t,
                Err(_) => continue,
            };
            let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();
            let mtime_ns = match modified_time.duration_since(SystemTime::UNIX_EPOCH) {
                Ok(d) => d.as_nanos() as i64,
                Err(_) => continue,
            };
            let unique_id = get_file_unique_id(&metadata);
            let cache_key = unique_id.to_string();
            let mut hash = String::new();
            let mut use_cache = false;
            let mut cached_file_id = None;
            let mut cached_known_version = 0;
            if unique_id != 0 {
                let mut cache_guard = cache.lock().unwrap();
                let (fid, ver) = resolve_or_migrate_file_identity(&mut cache_guard, &cache_key, &relative_path);
                cached_file_id = fid;
                cached_known_version = ver;

                if let Some(c_entry) = cache_guard.get_mut(&cache_key) {
                    if c_entry.mtime_ns == mtime_ns && c_entry.size == size {
                        hash = c_entry.hash.clone();
                        use_cache = true;
                        c_entry.last_known_path = Some(relative_path.clone());
                    }
                }
            }
            if !use_cache {
                match calculate_hash(&path) {
                    Ok(h) => {
                        hash = h;
                        if unique_id != 0 {
                            let mut cache_guard = cache.lock().unwrap();
                            let version_to_store = if cached_known_version > 0 { Some(cached_known_version) } else { None };
                            cache_guard.insert(cache_key, CacheEntry {
                                mtime_ns,
                                size,
                                hash: hash.clone(),
                                file_id: cached_file_id,
                                last_known_path: Some(relative_path.clone()),
                                version_num: version_to_store,
                            });
                        }
                    }
                    Err(_) => {
                        // Could not read this file right now (locked, permission error,
                        // in-use, etc.). Reporting a fabricated hash would assert a false
                        // fact about this file's content to the server, and caching it
                        // would keep asserting that false fact on every future scan since
                        // mtime/size alone can't tell it apart from a real reading. Skip
                        // this file for this cycle entirely; it will be retried on the
                        // next scan once it's readable again.
                        continue;
                    }
                }
            }
            let os_file_id = if unique_id != 0 { unique_id.to_string() } else { String::new() };
            list.push(FileMetadata {
                relative_path,
                file_name: name,
                is_directory: false,
                file_size: size,
                file_hash: hash,
                last_modified_at: datetime.to_rfc3339(),
                file_id: cached_file_id,
                known_version: cached_known_version,
                os_file_id,
            });
        }
    }
    Ok(())
}


// --- Scan Loop: Full Scan ---

async fn do_full_scan_all<R: Runtime>(app: &AppHandle<R>) {
    let config = app.state::<Arc<AppState>>().config.lock().unwrap().clone();
    if config.nas_url.is_empty() || config.api_token.is_empty() || config.sync_folders.is_empty() {
        return;
    }

    let client = reqwest::Client::new();
    let state = app.state::<Arc<AppState>>();
    let hash_cache = &state.hash_cache;

    for folder in &config.sync_folders {
        let local_dir = Path::new(&folder.local_path);
        if !local_dir.exists() { continue; }

        update_status(app, true, "".to_string(), -1, format!("{} をスキャン中...", folder.virtual_name));

        let local_files = match scan_local_directory(app, local_dir, &config.exclude_patterns, hash_cache) {
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
            .json(&ScanRequest { folder_id: folder.folder_id, files: local_files, deleted_paths: vec![], is_partial_scan: false })
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

    let current_cache = hash_cache.lock().unwrap().clone();
    save_hash_cache(app, &current_cache);
}


// --- Scan Loop: Watcher-Triggered Targeted Handling ---

// --- Scan Loop: Watcher-Triggered Targeted Handling ---

async fn handle_changed_paths<R: Runtime>(app: &AppHandle<R>, paths: &[PathBuf]) {
    let config = app.state::<Arc<AppState>>().config.lock().unwrap().clone();
    if config.nas_url.is_empty() || config.api_token.is_empty() { return; }

    let client = reqwest::Client::new();
    let state = app.state::<Arc<AppState>>();

    for folder in &config.sync_folders {
        let folder_path = Path::new(&folder.local_path);

        let mut folder_changed_paths = Vec::new();
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

            folder_changed_paths.push((path.clone(), rel_path));
        }

        if folder_changed_paths.is_empty() { continue; }

        let mut appeared: Vec<(PathBuf, String, fs::Metadata, u64)> = Vec::new();
        let mut disappeared: Vec<(PathBuf, String)> = Vec::new();

        for (path, rel_path) in folder_changed_paths {
            let path_ext = to_extended_path(&path);
            match fs::metadata(&path_ext) {
                Ok(meta) => {
                    let uid = get_file_unique_id(&meta);
                    appeared.push((path, rel_path, meta, uid));
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => {
                    tokio::time::sleep(Duration::from_millis(750)).await;
                    if fs::metadata(&path_ext).is_err() {
                        disappeared.push((path, rel_path));
                    }
                }
                _ => {}
            }
        }

        let mut handled_appeared = vec![false; appeared.len()];
        let mut handled_disappeared = vec![false; disappeared.len()];

        // 1. Local Rename Detection: match appeared files with disappeared cache entries via unique ID
        for (a_idx, (_a_path, a_rel_path, _a_meta, a_uid)) in appeared.iter().enumerate() {
            if *a_uid == 0 { continue; }

            let cache_info = {
                let guard = state.hash_cache.lock().unwrap();
                guard.get(&a_uid.to_string()).map(|c| (c.file_id, c.last_known_path.clone(), c.version_num))
            };

            if let Some((Some(fid), Some(old_rel_path), opt_ver)) = cache_info {
                if old_rel_path != *a_rel_path {
                    let d_match = disappeared.iter().position(|(_, d_rel)| d_rel == &old_rel_path);
                    let old_exists = fs::metadata(to_extended_path(folder_path.join(&old_rel_path))).is_ok();

                    if d_match.is_some() || !old_exists {
                        let expected_ver = opt_ver.unwrap_or(0);
                        let move_url = format!("{}/api/sync/move", config.nas_url);
                        let move_req = MoveRequest {
                            folder_id: folder.folder_id,
                            from_path: old_rel_path.clone(),
                            to_path: a_rel_path.clone(),
                            file_id: fid,
                            expected_version: expected_ver,
                        };

                        match client.post(&move_url)
                            .header("Authorization", format!("Bearer {}", config.api_token))
                            .json(&move_req)
                            .send()
                            .await
                        {
                            Ok(res) if res.status().is_success() => {
                                let opt_version = match res.json::<MoveResponse>().await {
                                    Ok(m_resp) => m_resp.version,
                                    Err(e) => {
                                        eprintln!("Failed to parse MoveResponse for move {} -> {}: {}", old_rel_path, a_rel_path, e);
                                        None
                                    }
                                };
                                {
                                    let mut guard = state.hash_cache.lock().unwrap();
                                    if let Some(c) = guard.get_mut(&a_uid.to_string()) {
                                        c.last_known_path = Some(a_rel_path.clone());
                                        if let Some(v) = opt_version {
                                            c.version_num = Some(v);
                                        }
                                    }
                                }
                                update_status(app, true, a_rel_path.clone(), -1, format!("moved: {} -> {}", old_rel_path, a_rel_path));
                                handled_appeared[a_idx] = true;
                                if let Some(d_i) = d_match {
                                    handled_disappeared[d_i] = true;
                                }
                            }
                            Ok(res) => {
                                eprintln!("Move detection request failed for {} -> {}: HTTP {}", old_rel_path, a_rel_path, res.status());
                            }
                            Err(e) => {
                                eprintln!("Move detection request error for {} -> {}: {}", old_rel_path, a_rel_path, e);
                            }
                        }
                    }
                }
            }
        }

        // 2. Assemble CAS-bearing partial scan payload
        let mut scan_files = Vec::new();
        let mut deleted_paths = Vec::new();

        for (d_idx, (_d_path, d_rel_path)) in disappeared.iter().enumerate() {
            if handled_disappeared[d_idx] { continue; }

            let (file_id, known_version) = {
                let guard = state.hash_cache.lock().unwrap();
                guard.values()
                    .find(|c| c.last_known_path.as_deref() == Some(d_rel_path))
                    .map(|c| (c.file_id.unwrap_or(0), c.version_num.unwrap_or(0)))
                    .unwrap_or((0, 0))
            };

            deleted_paths.push(DeletedPath {
                relative_path: d_rel_path.clone(),
                file_id,
                known_version,
            });
        }

        for (a_idx, (a_path, a_rel_path, a_meta, a_uid)) in appeared.iter().enumerate() {
            if handled_appeared[a_idx] { continue; }

            let is_dir = a_meta.is_dir();
            let name = a_path.file_name().unwrap_or_default().to_string_lossy().into_owned();
            let size = if is_dir { 0 } else { a_meta.len() as i64 };
            let modified_time = match a_meta.modified() {
                Ok(t) => t,
                Err(_) => continue,
            };
            let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();
            let mtime_ns = match modified_time.duration_since(SystemTime::UNIX_EPOCH) {
                Ok(d) => d.as_nanos() as i64,
                Err(_) => continue,
            };

            let mut hash = String::new();
            let mut cached_file_id = None;
            let mut cached_known_version = 0;
            let mut use_cache = false;

            if !is_dir && *a_uid != 0 {
                let cache_key = a_uid.to_string();
                let mut guard = state.hash_cache.lock().unwrap();
                let (fid, ver) = resolve_or_migrate_file_identity(&mut guard, &cache_key, a_rel_path);
                cached_file_id = fid;
                cached_known_version = ver;

                if let Some(c_entry) = guard.get_mut(&cache_key) {
                    if c_entry.mtime_ns == mtime_ns && c_entry.size == size {
                        hash = c_entry.hash.clone();
                        use_cache = true;
                        c_entry.last_known_path = Some(a_rel_path.clone());
                    }
                }
            }

            if !is_dir && !use_cache {
                let path_ext = to_extended_path(a_path);
                if let Ok(h) = calculate_hash(&path_ext) {
                    hash = h;
                    if *a_uid != 0 {
                        let cache_key = a_uid.to_string();
                        let mut guard = state.hash_cache.lock().unwrap();
                        let version_to_store = if cached_known_version > 0 { Some(cached_known_version) } else { None };
                        guard.insert(cache_key, CacheEntry {
                            mtime_ns,
                            size,
                            hash: hash.clone(),
                            file_id: cached_file_id,
                            last_known_path: Some(a_rel_path.clone()),
                            version_num: version_to_store,
                        });
                    }
                } else {
                    continue;
                }
            }

            let os_file_id = if *a_uid != 0 { a_uid.to_string() } else { String::new() };
            scan_files.push(FileMetadata {
                relative_path: a_rel_path.clone(),
                file_name: name,
                is_directory: is_dir,
                file_size: size,
                file_hash: hash,
                last_modified_at: datetime.to_rfc3339(),
                file_id: cached_file_id,
                known_version: cached_known_version,
                os_file_id,
            });
        }

        if scan_files.is_empty() && deleted_paths.is_empty() { continue; }

        let scan_url = format!("{}/api/sync/scan", config.nas_url);
        let scan_req = ScanRequest {
            folder_id: folder.folder_id,
            files: scan_files,
            deleted_paths,
            is_partial_scan: true,
        };

        match client.post(&scan_url)
            .header("Authorization", format!("Bearer {}", config.api_token))
            .json(&scan_req)
            .send()
            .await
        {
            Ok(res) if res.status().as_u16() == 409 => {
                do_full_scan_all(app).await;
            }
            Ok(res) if !res.status().is_success() => {
                let status = res.status();
                let body = res.text().await.unwrap_or_default();
                update_status(app, false, "".to_string(), -1, format!("部分スキャンエラー HTTP {}: {}", status, body.chars().take(100).collect::<String>()));
            }
            Err(e) => {
                update_status(app, false, "".to_string(), -1, format!("サーバー接続エラー: {}", e));
            }
            _ => {}
        }
    }

    let current_cache = state.hash_cache.lock().unwrap().clone();
    save_hash_cache(app, &current_cache);
}

pub async fn run_scan_loop<R: Runtime>(app: AppHandle<R>, mut rx: mpsc::Receiver<ScanEvent>) {
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


// --- Event Listener (runs forever, parallel to scan/execute loops) ---
//
// Subscribes to the server's push notification stream (GET /api/sync/events,
// Server-Sent Events). Any other device's action that changes shared sync
// state makes the server emit a line here; receiving anything at all -- the
// content doesn't matter -- means "something changed, scan now" instead of
// waiting for the next periodic timer. The periodic scan (every 5 minutes)
// remains as the correctness fallback if this connection is ever down.
pub async fn run_event_listener<R: Runtime>(app: AppHandle<R>, scan_tx: mpsc::Sender<ScanEvent>) {
    loop {
        let config = app.state::<Arc<AppState>>().config.lock().unwrap().clone();
        if config.nas_url.is_empty() || config.api_token.is_empty() {
            tokio::time::sleep(Duration::from_secs(5)).await;
            continue;
        }

        let url = format!("{}/api/sync/events", config.nas_url);
        let client = reqwest::Client::new();
        let res = client.get(&url)
            .header("Authorization", format!("Bearer {}", config.api_token))
            .send()
            .await;

        let mut res = match res {
            Ok(r) if r.status().is_success() => r,
            _ => {
                tokio::time::sleep(Duration::from_secs(5)).await;
                continue;
            }
        };

        loop {
            match res.chunk().await {
                Ok(Some(bytes)) => {
                    let text = String::from_utf8_lossy(&bytes);
                    if text.contains("data:") {
                        let _ = scan_tx.send(ScanEvent::Full).await;
                    }
                }
                Ok(None) => break, // stream ended -- reconnect
                Err(_) => break,
            }
        }

        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

// --- Execute Loop (runs forever, parallel to scan loop) ---

pub async fn run_execute_loop<R: Runtime>(app: AppHandle<R>) {
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

            *app.state::<Arc<AppState>>().current_queue.lock().unwrap() = queue.clone();

            #[derive(serde::Serialize, Clone)]
            #[serde(rename_all = "snake_case")]
            struct QueueInitPayload { items: Vec<SyncQueueItem> }
            if app.emit("sync-queue-init", QueueInitPayload { items: queue }).is_err() {
                record_emit_error(&app);
            }

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

            let current_cache = app.state::<Arc<AppState>>().hash_cache.lock().unwrap().clone();
            save_hash_cache(&app, &current_cache);
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

async fn execute_single_task<R: Runtime>(
    app: &AppHandle<R>,
    client: &reqwest::Client,
    config: &Config,
    folder: &SyncFolder,
    task: &SyncTask,
    local_dir: &Path,
) -> bool {
    let mut success = false;

    match task.action_type.as_str() {
        "delete_local" => {
            let local_path = to_extended_path(local_dir.join(&task.relative_path));
            // exists() swallows any error into `false`, indistinguishable from
            // "genuinely gone" -- treating "couldn't confirm" as "already deleted"
            // here would report a successful local deletion to the server, which
            // then physically deletes NAS's own copy and propagates the deletion
            match fs::metadata(&local_path) {
                Ok(_) => {
                    success = quarantine_local_path(local_dir, &task.relative_path, &local_path).is_ok();
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => {
                    success = true;
                }
                Err(_) => {
                    success = false;
                }
            }
            // delete_local is only ever generated by the server after the server's
            // own master_mapping ALREADY reflects this file as deleted/moved-away.
            // The deletion is already a done deal server-side; no additional
            // /api/sync/delete call is needed or correct here. The shared
            // /api/sync/tasks/complete call at the bottom of execute_single_task
            // handles the task acknowledgement.
        }

        "move" => {
            let local_src = to_extended_path(local_dir.join(&task.relative_path));
            let local_dst = to_extended_path(local_dir.join(&task.to_path));
            let dst_already_correct = !task.file_hash.is_empty()
                && local_dst.is_file()
                && calculate_hash(&local_dst).map(|h| h == task.file_hash).unwrap_or(false);
            if dst_already_correct {
                success = true;
            } else if local_src.exists() {
                if let Some(parent) = local_dst.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                success = fs::rename(&local_src, &local_dst).is_ok();
            }
            if success {
                // A "move" task is only generated when the server's master_mapping ALREADY
                // has this file_id at task.to_path (another device moved it, or server state
                // already advanced). Calling /api/sync/move again would CAS-mismatch (409)
                // because the server already incremented version_num. Only update the local
                // cache to reflect the file's new canonical path.
                if let Ok(meta) = fs::metadata(&local_dst) {
                    let uid = get_file_unique_id(&meta);
                    if uid != 0 {
                        let state = app.state::<Arc<AppState>>();
                        let mut guard = state.hash_cache.lock().unwrap();
                        let entry = guard.entry(uid.to_string()).or_insert_with(|| CacheEntry {
                            mtime_ns: 0,
                            size: 0,
                            hash: String::new(),
                            file_id: None,
                            last_known_path: None,
                            version_num: None,
                        });
                        if task.file_id != 0 {
                            entry.file_id = Some(task.file_id);
                        }
                        entry.last_known_path = Some(task.to_path.clone());
                        // Server now supplies Version for move tasks; update version_num like other task types.
                        if task.version.is_some() {
                            entry.version_num = task.version;
                        } else {
                            eprintln!(
                                "[WARN] task.version is None for action_type='{}', file_id={}, relative_path='{}'",
                                task.action_type, task.file_id, task.relative_path
                            );
                        }
                    }
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
                    let local_path = to_extended_path(local_dir.join(&task.relative_path));
                    if let Some(parent) = local_path.parent() {
                        let _ = fs::create_dir_all(parent);
                    }
                    if let Ok(bytes) = res.bytes().await {
                        if bytes.as_ref() == b"Directory synced" {
                            let _ = fs::create_dir_all(&local_path);
                            success = true;
                        } else {
                            let content_hash = sha256_hex(&bytes);
                            if !task.file_hash.is_empty() && content_hash != task.file_hash {
                                update_status(app, false, task.relative_path.clone(), -1,
                                    format!("ダウンロード内容がハッシュと一致しません: {}", task.relative_path));
                            } else {
                                success = fs::write(&local_path, &bytes).is_ok();
                                if success {
                                    if let Ok(meta) = fs::metadata(&local_path) {
                                        let uid = get_file_unique_id(&meta);
                                        if uid != 0 {
                                            let state = app.state::<Arc<AppState>>();
                                            let mut guard = state.hash_cache.lock().unwrap();
                                            let mtime_ns = meta.modified().ok()
                                                .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                                                .map(|d| d.as_nanos() as i64)
                                                .unwrap_or(0);
                                            let size = meta.len() as i64;
                                            let entry = guard.entry(uid.to_string()).or_insert_with(|| CacheEntry { mtime_ns: 0, size: 0, hash: String::new(), file_id: None, last_known_path: None, version_num: None });
                                            entry.mtime_ns = mtime_ns;
                                            entry.size = size;
                                            entry.hash = content_hash.clone();
                                            if task.version.is_some() {
                                                entry.version_num = task.version;
                                            } else {
                                                eprintln!(
                                                    "[WARN] task.version is None for action_type='{}', file_id={}, relative_path='{}'",
                                                    task.action_type, task.file_id, task.relative_path
                                                );
                                            }
                                            if task.file_id != 0 {
                                                entry.file_id = Some(task.file_id);
                                                entry.last_known_path = Some(task.relative_path.clone());
                                            }
                                        }
                                    }
                                }
                            }
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
            let local_path = to_extended_path(local_dir.join(&task.relative_path));
            match fs::metadata(&local_path) {
                Ok(metadata) => {
                    if metadata.is_dir() {
                        // This task was generated when master_mapping still thought this
                        // path was a file; it's since become a directory locally. Silently
                        // claiming success here would report "uploaded" to the server while
                        // nothing was actually sent, leaving master_mapping's stale
                        // file-type/hash uncorrected. Leave it pending instead -- the next
                        // scan (periodic, or watcher-triggered) reports the real current
                        // type and prunes this now-obsolete task on its own.
                        success = false;
                    } else {
                        // Don't substitute "now" if the real mtime can't be read -- that
                        // fabricates a fact used in server-side conflict resolution instead
                        // of reporting what's actually known. Leave the task pending;
                        // the next cycle retries it.
                        let modified_time = match metadata.modified() {
                            Ok(t) => t,
                            Err(_) => return false,
                        };
                        let mtime_secs = modified_time.duration_since(SystemTime::UNIX_EPOCH).ok().map(|d| d.as_secs() as i64).unwrap_or(0);

                        // Compute this upload's own size/hash from the local file right now,
                        // rather than trusting task.file_size/task.file_hash (the server's own
                        // recorded values from when the task was created). Echoing those back
                        // asserts nothing about what these bytes actually are -- the server's
                        // "identical content re-uploaded" dedup check then always trivially
                        // matches itself, so a genuinely different or since-changed local file
                        // never gets a real integrity check at all.
                        let size = metadata.len() as i64;
                        let hash = match calculate_hash(&local_path) {
                            Ok(h) => h,
                            Err(_) => return false,
                        };

                        // Look up cached expected_version via get_file_unique_id + hash_cache lock
                        let expected_version = {
                            let uid = get_file_unique_id(&metadata);
                            if uid != 0 {
                                let state = app.state::<Arc<AppState>>();
                                let guard = state.hash_cache.lock().unwrap();
                                guard.get(&uid.to_string()).and_then(|c| c.version_num).unwrap_or(0)
                            } else {
                                0
                            }
                        };

                        // [SEC 3 / Step 2] Handle ToPath-bearing upload_new tasks synthesized during conflict forks.
                        // Read from task.relative_path, but submit form with relative_path = task.to_path & file_id.
                        let is_to_path_fork_upload = !task.to_path.is_empty() && task.to_path != task.relative_path;
                        let upload_target_rel_path = if is_to_path_fork_upload { task.to_path.clone() } else { task.relative_path.clone() };

                        let part = match stream_file_part(&local_path, upload_target_rel_path.clone()).await {
                            Ok(p) => p,
                            Err(_) => return false,
                        };

                        let form = reqwest::multipart::Form::new()
                            .text("folder_id", folder.folder_id.to_string())
                            .text("relative_path", upload_target_rel_path.clone())
                            .text("file_size", size.to_string())
                            .text("file_hash", hash.clone())
                            .text("last_modified_at", mtime_secs.to_string())
                            .text("file_id", task.file_id.to_string())
                            .text("expected_version", expected_version.to_string())
                            .part("file", part);

                        let upload_url = format!("{}/api/sync/upload", config.nas_url);
                        match client.post(&upload_url)
                            .header("Authorization", format!("Bearer {}", config.api_token))
                            .multipart(form)
                            .send().await
                        {
                            Ok(res) => {
                                success = res.status().is_success();
                                if success {
                                    #[derive(serde::Deserialize)]
                                    struct UploadResponse {
                                        #[serde(default)]
                                        file_id: Option<i64>,
                                        #[serde(default)]
                                        version: Option<i32>,
                                        #[serde(default)]
                                        forked: Option<bool>,
                                        #[serde(default)]
                                        new_file_id: Option<i64>,
                                        #[serde(default)]
                                        new_relative_path: Option<String>,
                                    }
                                    match res.json::<UploadResponse>().await {
                                        Ok(resp) => {
                                            let is_response_forked = resp.forked == Some(true);
                                            let final_rel_path = if is_response_forked {
                                                resp.new_relative_path.clone().unwrap_or_else(|| upload_target_rel_path.clone())
                                            } else {
                                                upload_target_rel_path.clone()
                                            };
                                            let final_file_id = if is_response_forked {
                                                resp.new_file_id.or(resp.file_id)
                                            } else {
                                                resp.file_id.or(if task.file_id != 0 { Some(task.file_id) } else { None })
                                            };
                                            let final_version = resp.version;

                                            // Perform local file rename if final relative path differs from current local path
                                            let current_local_path = to_extended_path(local_dir.join(&task.relative_path));
                                            let target_local_path = to_extended_path(local_dir.join(&final_rel_path));

                                            let mut actual_local_path = current_local_path.clone();
                                            let mut rename_succeeded = true;

                                            if current_local_path != target_local_path {
                                                let parent_ok = if let Some(parent) = target_local_path.parent() {
                                                    match fs::create_dir_all(parent) {
                                                        Ok(_) => true,
                                                        Err(e) => {
                                                            eprintln!("Failed to create parent directory {:?} for rename: {}", parent, e);
                                                            update_status(
                                                                app,
                                                                false,
                                                                task.relative_path.clone(),
                                                                -1,
                                                                format!("アップロード後のフォルダ作成エラー: {}", e),
                                                            );
                                                            false
                                                        }
                                                    }
                                                } else {
                                                    true
                                                };

                                                if parent_ok {
                                                    match fs::rename(&current_local_path, &target_local_path) {
                                                        Ok(_) => {
                                                            actual_local_path = target_local_path;
                                                        }
                                                        Err(e) => {
                                                            eprintln!(
                                                                "Failed to rename local file from {:?} to {:?}: {}",
                                                                current_local_path, target_local_path, e
                                                            );
                                                            update_status(
                                                                app,
                                                                false,
                                                                task.relative_path.clone(),
                                                                -1,
                                                                format!("アップロード後のファイルリネームエラー: {}", e),
                                                            );
                                                            rename_succeeded = false;
                                                        }
                                                    }
                                                } else {
                                                    rename_succeeded = false;
                                                }
                                            }

                                            if rename_succeeded {
                                                if let Ok(meta) = fs::metadata(&actual_local_path) {
                                                    let uid = get_file_unique_id(&meta);
                                                    if uid != 0 {
                                                        let state = app.state::<Arc<AppState>>();
                                                        let mut guard = state.hash_cache.lock().unwrap();
                                                        let mtime_ns = meta.modified().ok()
                                                            .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                                                            .map(|d| d.as_nanos() as i64)
                                                            .unwrap_or(0);
                                                        let size = meta.len() as i64;
                                                        let entry = guard.entry(uid.to_string()).or_insert_with(|| CacheEntry {
                                                            mtime_ns: 0,
                                                            size: 0,
                                                            hash: String::new(),
                                                            file_id: None,
                                                            last_known_path: None,
                                                            version_num: None,
                                                        });
                                                        entry.mtime_ns = mtime_ns;
                                                        entry.size = size;
                                                        entry.hash = hash.clone();
                                                        entry.file_id = final_file_id;
                                                        if final_version.is_some() {
                                                            entry.version_num = final_version;
                                                        } else {
                                                            eprintln!(
                                                                "[WARN] final_version is None for action_type='{}', file_id={:?}, relative_path='{}'",
                                                                task.action_type, final_file_id, final_rel_path
                                                            );
                                                        }
                                                        entry.last_known_path = Some(final_rel_path);
                                                    }
                                                }
                                            } else {
                                                success = false;
                                            }
                                        }
                                        Err(e) => {
                                            eprintln!("Failed to parse upload response JSON: {}", e);
                                            update_status(
                                                app,
                                                false,
                                                task.relative_path.clone(),
                                                -1,
                                                format!("アップロード応答解析エラー: {}", e),
                                            );
                                            success = false;
                                        }
                                    }
                                }
                            }
                            Err(e) => {
                                eprintln!("Failed to send upload request: {}", e);
                                update_status(
                                    app,
                                    false,
                                    task.relative_path.clone(),
                                    -1,
                                    format!("アップロード接続エラー: {}", e),
                                );
                                success = false;
                            }
                        }
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => {
                    // exists() swallows any error into `false`, indistinguishable from
                    // "genuinely gone" -- treating "couldn't confirm" as "source deleted"
                    // here would drop the upload task permanently on a transient error like
                    // a permission glitch, missing an upload that should have happened.
                    // Only a confirmed NotFound counts as "upload source no longer exists".
                    // Upload skipped because source file no longer exists locally;
                    // treating as resolved (success = true) so the stale task clears
                    // and the server can reconcile the file's true state via the next scan.
                    success = true;
                }
                Err(_) => {
                    success = false;
                }
            }
        }

        _ => {}
    }

    if success {
        let complete_url = format!("{}/api/sync/tasks/complete?task_id={}", config.nas_url, task.task_id);
        match client.post(&complete_url)
            .header("Authorization", format!("Bearer {}", config.api_token))
            .send()
            .await
        {
            Ok(res) if res.status().is_success() => {}
            Ok(res) => {
                eprintln!(
                    "Task complete notification failed (task_id: {}, path: {}): HTTP {}",
                    task.task_id, task.relative_path, res.status()
                );
            }
            Err(e) => {
                eprintln!(
                    "Task complete notification request error (task_id: {}, path: {}): {}",
                    task.task_id, task.relative_path, e
                );
            }
        }
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
                // A failed send here almost always means the scan loop's receiver
                // is gone (that whole background task has died), which silently
                // stops ALL future real-time sync -- not just this one event. The
                // periodic full scan is the correctness fallback for individually
                // missed events, but if the scan loop itself is dead, that
                // fallback is dead too. Surface it rather than losing it silently.
                if let Err(e) = scan_tx.blocking_send(ScanEvent::Paths(changed)) {
                    eprintln!("Failed to forward watcher event to scan loop (scan loop may have died): {}", e);
                }
            }
        }
    }).map_err(|e| e.to_string())?;

    // A path that fails to register isn't covered by the "no real-time
    // watching, periodic scan is the fallback" reasoning elsewhere in this
    // file with the same margin: the user gets zero indication that this
    // specific folder silently degraded to 5-minute-latency sync instead of
    // real-time. Collect failures and report them so this is diagnosable.
    let mut failed_paths = Vec::new();
    for path_str in paths {
        let path = Path::new(&path_str);
        if path.exists() {
            if let Err(e) = watcher.watch(path, RecursiveMode::Recursive) {
                eprintln!("Failed to watch path {:?}: {}", path, e);
                failed_paths.push(path_str);
            }
        }
    }
    if !failed_paths.is_empty() {
        eprintln!(
            "Watcher registration failed for {} path(s); these folders will only sync via the periodic full scan (up to 5 min latency), not in real time: {:?}",
            failed_paths.len(), failed_paths
        );
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

#[derive(Serialize, Deserialize, Clone, Debug)]
struct UpdateFolderPathRequest {
    folder_id: i32,
    local_path: String,
}

pub async fn api_update_folder_path(nas_url: &str, token: &str, folder_id: i32, local_path: &str) -> Result<(), String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/sync/folders", nas_url);
    let req = UpdateFolderPathRequest {
        folder_id,
        local_path: local_path.to_string(),
    };
    let res = client.put(&url)
        .header("Authorization", format!("Bearer {}", token))
        .json(&req)
        .send().await
        .map_err(|e| format!("ネットワーク接続エラー: {}", e))?;
    if !res.status().is_success() {
        return Err(format!("フォルダーパスの更新に失敗しました: ステータス {}", res.status()));
    }
    Ok(())
}
