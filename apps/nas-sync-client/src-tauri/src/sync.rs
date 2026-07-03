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
use tokio::sync::mpsc;
use serde::{Serialize, Deserialize};
use sha2::{Sha256, Digest};
use notify::{Watcher, RecursiveMode, Event, RecommendedWatcher};
use tauri::{AppHandle, Manager, Emitter};
use glob;


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
	pub progress: f32, // 0.0 to 1.0
	pub message: String,
}

pub struct AppState {
	pub config: Mutex<Config>,
	pub status: Mutex<SyncStatus>,
	pub sync_trigger: mpsc::Sender<()>,
	pub watcher: Mutex<Option<RecommendedWatcher>>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "snake_case")]
pub struct SyncQueueItem {
	pub file_name: String,
	pub relative_path: String,
	pub direction: String, // "upload" | "download" | "delete_local" | "delete_remote"
	pub status: String,    // "pending" | "processing" | "completed" | "failed"
	pub progress: f32,     // 0.0 to 1.0
}



// --- Helper: SHA-256 Hash ---

pub fn calculate_hash(path: &Path) -> io::Result<String> {
	let mut file = File::open(path)?;
	let mut hasher = Sha256::new();
	let mut buffer = [0; 8192];
	loop {
		let count = file.read(&mut buffer)?;
		if count == 0 {
			break;
		}
		hasher.update(&buffer[..count]);
	}
	Ok(hex::encode(hasher.finalize()))
}

// --- Helper: OS File Unique ID (inode for Unix, creation_time for Windows) ---
pub fn get_file_unique_id(metadata: &fs::Metadata) -> u64 {
	#[cfg(unix)]
	{
		metadata.ino()
	}
	#[cfg(windows)]
	{
		metadata.creation_time()
	}
	#[cfg(not(any(unix, windows)))]
	{
		0
	}
}

// --- Helper: Hash Cache (Mapping storage to skip redundant reads) ---

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
	// Tauri v2 API: app.path().app_config_dir()
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
					config.exclude_patterns = vec![
						"node_modules".to_string(),
						".git".to_string(),
						"*.tmp".to_string(),
						"~*".to_string(),
						"*.log".to_string(),
						".DS_Store".to_string(),
						"._*".to_string(),
					];
				}
				return config;
			}
		}
	}
	Config {
		nas_url: "http://100.82.85.101:8080".to_string(),
		exclude_patterns: vec![
			"node_modules".to_string(),
			".git".to_string(),
			"*.tmp".to_string(),
			"~*".to_string(),
			"*.log".to_string(),
			".DS_Store".to_string(),
			"._*".to_string(),
		],
		..Config::default()
	}
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
	last_modified_at: String, // RFC3339
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


// --- Core Sync Worker Logic ---

pub async fn run_sync(app: AppHandle) -> Result<(), String> {
	// Wait 3 seconds for the WebView JS to load and register listeners completely
	tokio::time::sleep(std::time::Duration::from_secs(3)).await;

	// 1. Set status to Syncing
	update_status(&app, true, "".to_string(), 0.0, "同期スキャンを開始中...".to_string());

	let state = app.state::<Arc<AppState>>();
	let config = state.config.lock().unwrap().clone();

	if config.nas_url.is_empty() || config.api_token.is_empty() {
		update_status(&app, false, "".to_string(), 0.0, "NAS接続設定が未完了です。".to_string());
		return Err("NAS URL or API token is empty".to_string());
	}

	let client = reqwest::Client::new();

	for folder in &config.sync_folders {
		let local_dir = Path::new(&folder.local_path);
		if !local_dir.exists() {
			eprintln!("Sync folder path does not exist: {}", folder.local_path);
			continue;
		}

		// 2. Scan Local Files and Calculate hashes
		update_status(&app, true, "".to_string(), 0.0, format!("ローカルフォルダ {} をスキャン中...", folder.virtual_name));
		
		let mut cache = load_hash_cache(&app);
		let local_files = match scan_local_directory(&app, local_dir, &config.exclude_patterns, &mut cache) {
			Ok(files) => {
				save_hash_cache(&app, &cache);
				files
			}
			Err(e) => {
				eprintln!("Failed to scan local directory: {}", e);
				continue;
			}
		};

		// 3. Send Metadata to NAS API
		let scan_url = format!("{}/api/sync/scan", config.nas_url);
		let scan_req = ScanRequest {
			folder_id: folder.folder_id,
			files: local_files.clone(),
		};

		update_status(&app, true, "".to_string(), 0.1, "サーバーと差分を比較中...".to_string());

		let response = client.post(&scan_url)
			.header("Authorization", format!("Bearer {}", config.api_token))
			.json(&scan_req)
			.send()
			.await;

		let scan_res: ScanResponse = match response {
			Ok(res) => {
				if res.status().is_success() {
					match res.json().await {
						Ok(json) => json,
						Err(e) => {
							update_status(&app, false, "".to_string(), 0.0, format!("スキャン結果の解析に失敗しました: {}", e));
							tokio::time::sleep(std::time::Duration::from_secs(3)).await;
							continue;
						}
					}
				} else {
					let status_err = format!("Server returned error: {}", res.status());
					update_status(&app, false, "".to_string(), 0.0, status_err.clone());
					tokio::time::sleep(std::time::Duration::from_secs(3)).await;
					continue;
				}
			}
			Err(e) => {
				update_status(&app, false, "".to_string(), 0.0, format!("サーバー接続エラー: {}", e));
				tokio::time::sleep(std::time::Duration::from_secs(3)).await;
				continue;
			}
		};

		let total_tasks = scan_res.tasks.len();
		if total_tasks == 0 {
			continue; // This folder is up to date
		}

		// --- Build and emit initial pending queue ---
		let mut queue = Vec::new();
		for task in &scan_res.tasks {
			let direction = match task.action_type.as_str() {
				"upload_new" => "upload_new",
				"upload_overwrite" => "upload_overwrite",
				"download_new" => "download_new",
				"download_overwrite" => "download_overwrite",
				"delete_local" => "delete_local",
				"move" => "move",
				_ => "unknown",
			};
			queue.push(SyncQueueItem {
				file_name: if task.action_type == "move" {
					format!("{} ➔ {}", Path::new(&task.relative_path).file_name().unwrap_or_default().to_string_lossy(), Path::new(&task.to_path).file_name().unwrap_or_default().to_string_lossy())
				} else {
					Path::new(&task.relative_path).file_name().unwrap_or_default().to_string_lossy().into_owned()
				},
				relative_path: if task.action_type == "move" { task.to_path.clone() } else { task.relative_path.clone() },
				direction: direction.to_string(),
				status: "pending".to_string(),
				progress: 0.0,
			});
		}
		#[derive(serde::Serialize, Clone)]
		#[serde(rename_all = "snake_case")]
		struct QueueInitPayload {
			items: Vec<SyncQueueItem>,
		}
		if let Err(e) = app.emit("sync-queue-init", QueueInitPayload { items: queue }) {
			eprintln!("Failed to emit sync-queue-init: {:?}", e);
		}

		let mut completed_tasks = 0;
		let get_progress = |completed: usize, total: usize| -> f32 {
			if total == 0 { 1.0 } else { 0.1 + 0.9 * (completed as f32 / total as f32) }
		};

		// --- Run Tasks sequentially ---
		for task in &scan_res.tasks {
			let target_path = if task.action_type == "move" { &task.to_path } else { &task.relative_path };
			emit_queue_update(&app, target_path, "processing", 0.0);
			
			let status_msg = match task.action_type.as_str() {
				"upload_new" => "新規アップロード中...",
				"upload_overwrite" => "上書きアップロード中...",
				"download_new" => "新規ダウンロード中...",
				"download_overwrite" => "上書きダウンロード中...",
				"delete_local" => "ローカルファイルを削除中...",
				"move" => "ファイルを移動中...",
				_ => "同期処理中...",
			};
			update_status(&app, true, target_path.clone(), get_progress(completed_tasks, total_tasks), status_msg.to_string());

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
						if rm_res.is_ok() {
							success = true;
						}
					} else {
						success = true; // Already missing local
					}
					if success {
						// Notify server to delete task and commit device_mapping
						let delete_url = format!("{}/api/sync/delete", config.nas_url);
						let del_req = DeleteRequest {
							folder_id: folder.folder_id,
							relative_path: task.relative_path.clone(),
						};
						let del_res = client.delete(&delete_url)
							.header("Authorization", format!("Bearer {}", config.api_token))
							.json(&del_req)
							.send()
							.await;
						if let Ok(res) = del_res {
							if !res.status().is_success() {
								success = false;
							}
						} else {
							success = false;
						}
					}
				}
				"move" => {
					let local_src = local_dir.join(&task.relative_path);
					let local_dst = local_dir.join(&task.to_path);
					if local_src.exists() && !local_dst.exists() {
						if let Some(parent) = local_dst.parent() {
							let _ = fs::create_dir_all(parent);
						}
						if fs::rename(&local_src, &local_dst).is_ok() {
							success = true;
						}
					} else if local_dst.exists() {
						success = true;
					}
					if success {
						// Call /api/sync/move to commit
						let move_url = format!("{}/api/sync/move", config.nas_url);
						let move_req = MoveRequest {
							folder_id: folder.folder_id,
							from_path: task.relative_path.clone(),
							to_path: task.to_path.clone(),
						};
						let move_res = client.post(&move_url)
							.header("Authorization", format!("Bearer {}", config.api_token))
							.json(&move_req)
							.send()
							.await;
						if let Ok(res) = move_res {
							if !res.status().is_success() {
								success = false;
							}
						} else {
							success = false;
						}
					}
				}
				"download_new" | "download_overwrite" => {
					let download_url = format!("{}/api/sync/download", config.nas_url);
					let file_res = client.get(&download_url)
						.header("Authorization", format!("Bearer {}", config.api_token))
						.query(&[("folder_id", folder.folder_id.to_string()), ("relative_path", task.relative_path.clone())])
						.send()
						.await;

					if let Ok(res) = file_res {
						if res.status().is_success() {
							let local_path = local_dir.join(&task.relative_path);
							if let Some(parent) = local_path.parent() {
								let _ = fs::create_dir_all(parent);
							}
							if let Ok(bytes) = res.bytes().await {
								if bytes.as_ref() == b"Directory synced" {
									let _ = fs::create_dir_all(&local_path);
									success = true;
								} else {
									// Conflict Check Trigger 1: Local file has un-synced edits
									let is_conflict = if local_path.exists() && local_path.is_file() {
										let current_hash = calculate_hash(&local_path).unwrap_or_default();
										current_hash != task.file_hash
									} else {
										false
									};

									if is_conflict {
										let conflict_path = generate_conflict_path(&local_path, &task.last_modified_by);
										if fs::write(&conflict_path, &bytes).is_ok() {
											success = true;
											println!("[CONFLICT] File edited on other device, saved conflict copy to {:?}", conflict_path);
										}
									} else {
										// Normal write, fallback to conflict copy if locked (Trigger 2)
										match fs::write(&local_path, &bytes) {
											Ok(_) => {
												success = true;
											}
											Err(e) => {
												eprintln!("[WRITE ERROR] Failed to write file {:?}: {:?}. Saving as conflict copy...", local_path, e);
												let conflict_path = generate_conflict_path(&local_path, &task.last_modified_by);
												if fs::write(&conflict_path, &bytes).is_ok() {
													success = true;
												}
											}
										}
									}
								}
							}
						}
					}
				}
				"upload_new" | "upload_overwrite" => {
					let upload_url = format!("{}/api/sync/upload", config.nas_url);
					let local_path = local_dir.join(&task.relative_path);
					if local_path.exists() {
						if local_path.is_dir() {
							success = true; // Directory metadata synced on scan
						} else {
							// File upload
							if let Ok(bytes) = fs::read(&local_path) {
								let metadata = fs::metadata(&local_path).unwrap();
								let modified_time = metadata.modified().unwrap_or(SystemTime::now());
								let datetime: chrono::DateTime<chrono::Utc> = modified_time.into();
								let last_modified_rfc3339 = datetime.to_rfc3339();

								let form = reqwest::multipart::Form::new()
									.text("folder_id", folder.folder_id.to_string())
									.text("relative_path", task.relative_path.clone())
									.text("file_size", task.file_size.to_string())
									.text("file_hash", task.file_hash.clone())
									.text("last_modified_at", last_modified_rfc3339)
									.part("file", reqwest::multipart::Part::bytes(bytes).file_name(task.relative_path.clone()));
								
								let upload_res = client.post(&upload_url)
									.header("Authorization", format!("Bearer {}", config.api_token))
									.multipart(form)
									.send()
									.await;
								if let Ok(res) = upload_res {
									if res.status().is_success() {
										success = true;
									}
								}
							}
						}
					}
				}
				_ => {}
			}

			completed_tasks += 1;
			if success {
				emit_queue_update(&app, target_path, "completed", 1.0);
			} else {
				emit_queue_update(&app, target_path, "failed", 0.0);
			}
		}
	}

	// 8. Completed
	update_status(&app, false, "".to_string(), 1.0, "同期完了".to_string());
	Ok(())
}

fn update_status(app: &AppHandle, is_syncing: bool, current_file: String, progress: f32, message: String) {
	let state = app.state::<Arc<AppState>>();
	let mut status = state.status.lock().unwrap();
	status.is_syncing = is_syncing;
	status.current_file = current_file;
	status.progress = progress;
	status.message = message;

	// Emit status event to frontend
	let _ = app.emit("sync-status", status.clone());
}

fn is_excluded(name: &str, relative_path: &str, patterns: &[String]) -> bool {
	for pat in patterns {
		if let Ok(pattern) = glob::Pattern::new(pat) {
			if pattern.matches(name) {
				return true;
			}
			if pattern.matches(relative_path) {
				return true;
			}
			for part in relative_path.split('/') {
				if pattern.matches(part) {
					return true;
				}
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
	if current_dir.is_dir() {
		for entry in fs::read_dir(current_dir)? {
			let entry = entry?;
			let path = entry.path();
			
			let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
			if name.starts_with('.') || name == "$RECYCLE.BIN" {
				continue;
			}

			let relative_path = path.strip_prefix(base_dir)
				.map_err(|e| io::Error::new(io::ErrorKind::Other, e))?
				.to_string_lossy()
				.replace('\\', "/"); // standard clean paths

			if is_excluded(&name, &relative_path, exclude_patterns) {
				continue;
			}

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
						cache.insert(cache_key, CacheEntry {
							mtime_ns,
							size,
							hash: hash.clone(),
						});
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
	}
	Ok(())
}


// --- Watcher Orchestration ---

pub fn start_watcher(app: AppHandle, paths: Vec<String>) -> Result<RecommendedWatcher, String> {
	let trigger = app.state::<Arc<AppState>>().sync_trigger.clone();
	
	let mut watcher = notify::recommended_watcher(move |res: Result<Event, notify::Error>| {
		match res {
			Ok(event) => {
				// Ignore temp files / git folders / lock files
				let should_trigger = event.paths.iter().any(|path| {
					let name = path.file_name().unwrap_or_default().to_string_lossy();
					!name.starts_with('.') && name != "node_modules" && !name.ends_with('~') && !name.ends_with(".tmp")
				});

				if should_trigger {
					let _ = trigger.blocking_send(());
				}
			}
			Err(e) => eprintln!("Watch error: {:?}", e),
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

// --- API Helpers (Register Device & Add Folder) ---

pub async fn api_register_device(nas_url: &str, device_name: &str) -> Result<(i32, String), String> {
	let client = reqwest::Client::new();
	let url = format!("{}/api/devices/register", nas_url);
	let req = RegisterDeviceRequest {
		device_name: device_name.to_string(),
		os_type: std::env::consts::OS.to_string(),
	};

	let res = client.post(&url)
		.json(&req)
		.send()
		.await
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
		.send()
		.await
		.map_err(|e| format!("ネットワーク接続エラー: {}", e))?;

	if !res.status().is_success() {
		return Err(format!("フォルダーの登録に失敗しました: ステータス {}", res.status()));
	}

	let data: RegisterFolderResponse = res.json().await
		.map_err(|e| format!("レスポンスの解析に失敗しました: {}", e))?;

	Ok(data.folder_id)
}

fn emit_queue_update(app: &AppHandle, relative_path: &str, status: &str, progress: f32) {
	#[derive(serde::Serialize, Clone)]
	#[serde(rename_all = "snake_case")]
	struct QueueUpdate {
		relative_path: String,
		status: String,
		progress: f32,
	}
	let _ = app.emit("sync-queue-update", QueueUpdate {
		relative_path: relative_path.to_string(),
		status: status.to_string(),
		progress,
	});
}

fn generate_conflict_path(original_path: &Path, last_modified_by: &str) -> PathBuf {
	let parent = original_path.parent().unwrap_or_else(|| Path::new(""));
	let stem = original_path.file_stem().unwrap_or_default().to_string_lossy();
	let ext = original_path.extension().unwrap_or_default().to_string_lossy();

	let suffix = if last_modified_by.is_empty() {
		"Conflict".to_string()
	} else {
		format!("from {}", last_modified_by)
	};

	let mut counter = 0;
	loop {
		let new_name = if counter == 0 {
			if ext.is_empty() {
				format!("{} ({})", stem, suffix)
			} else {
				format!("{} ({}).{}", stem, suffix, ext)
			}
		} else {
			if ext.is_empty() {
				format!("{} ({}) ({})", stem, suffix, counter)
			} else {
				format!("{} ({}) ({}).{}", stem, suffix, counter, ext)
			}
		};

		let candidate = parent.join(new_name);
		if !candidate.exists() {
			return candidate;
		}
		counter += 1;
	}
}

