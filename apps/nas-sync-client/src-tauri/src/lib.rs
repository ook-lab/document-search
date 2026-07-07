mod sync;

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::fs;
use std::path::Path;
use std::io;
use tauri::{AppHandle, Manager, State};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{TrayIconBuilder, TrayIconEvent, MouseButton, MouseButtonState};
use tokio::sync::mpsc;



// --- Tauri Commands ---

#[tauri::command]
fn get_config(state: State<'_, Arc<sync::AppState>>) -> sync::Config {
	state.config.lock().unwrap().clone()
}

#[tauri::command]
fn get_status(state: State<'_, Arc<sync::AppState>>) -> sync::SyncStatus {
	state.status.lock().unwrap().clone()
}

// Pulled once by the frontend right after it registers its "sync-queue-init"
// listener, so the initial transfer list is correct even when the backend's
// first push happened before that listener was attached (see AppState::current_queue).
#[tauri::command]
fn get_current_queue(state: State<'_, Arc<sync::AppState>>) -> Vec<sync::SyncQueueItem> {
	state.current_queue.lock().unwrap().clone()
}

#[tauri::command]
async fn save_settings(
	state: State<'_, Arc<sync::AppState>>,
	app: AppHandle,
	_nas_url: String,
	device_name: String,
) -> Result<sync::Config, String> {
	let mut config = state.config.lock().unwrap().clone();
	config.nas_url = "http://100.82.85.101:47291".to_string();

	if config.device_id == 0 {
		let (device_id, token) = sync::api_register_device(&config.nas_url, &device_name).await?;
		config.device_id = device_id;
		config.api_token = token;
	}

	*state.config.lock().unwrap() = config.clone();
	sync::save_config_file(&app, &config).map_err(|e| e.to_string())?;

	let _ = state.scan_tx.send(sync::ScanEvent::Full).await;

	Ok(config)
}


#[tauri::command]
async fn add_folder(
	state: State<'_, Arc<sync::AppState>>,
	app: AppHandle,
	local_path: String,
	virtual_name: String,
) -> Result<sync::Config, String> {
	let mut config = state.config.lock().unwrap().clone();

	if config.device_id == 0 || config.api_token.is_empty() {
		return Err("先にNAS接続設定を保存してください。".to_string());
	}

	if config.sync_folders.iter().any(|f| f.local_path == local_path) {
		return Err("このフォルダは既に同期対象に設定されています。".to_string());
	}

	let folder_id = sync::api_register_folder(
		&config.nas_url,
		&config.api_token,
		&local_path,
		&virtual_name,
	).await?;

	let new_folder = sync::SyncFolder { folder_id, local_path, virtual_name };
	config.sync_folders.push(new_folder);

	*state.config.lock().unwrap() = config.clone();
	sync::save_config_file(&app, &config).map_err(|e| e.to_string())?;

	restart_watcher_service(&config, &state)?;

	let _ = state.scan_tx.send(sync::ScanEvent::Full).await;

	Ok(config)
}

#[tauri::command]
async fn force_sync(state: State<'_, Arc<sync::AppState>>) -> Result<(), String> {
	state.scan_tx.send(sync::ScanEvent::Full).await.map_err(|e| e.to_string())
}

#[tauri::command]
fn select_folder(initial_dir: Option<String>) -> Option<String> {
	// Without an explicit starting directory, Windows opens this dialog at
	// whatever folder some app last left it at via the shared Common Item
	// Dialog MRU -- unrelated to what this modal's path field shows or to
	// Explorer's own "Downloads" shortcut. Always pin the start location to
	// something we actually know is correct instead of letting Windows guess.
	let mut dialog = rfd::FileDialog::new();
	let hint = initial_dir.filter(|d| !d.is_empty() && Path::new(d).is_dir());
	let start_dir = hint.or_else(|| dirs::home_dir().map(|p| p.to_string_lossy().to_string()));
	if let Some(dir) = start_dir {
		dialog = dialog.set_directory(dir);
	}
	let folder = dialog.pick_folder();
	folder.map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
async fn update_folder_path(
	state: State<'_, Arc<sync::AppState>>,
	app: AppHandle,
	folder_id: i32,
	local_path: String,
) -> Result<sync::Config, String> {
	let mut config = state.config.lock().unwrap().clone();

	if config.sync_folders.iter().any(|f| f.folder_id != folder_id && f.local_path == local_path) {
		return Err("このフォルダは既に同期対象に設定されています。".to_string());
	}

	sync::api_update_folder_path(&config.nas_url, &config.api_token, folder_id, &local_path).await?;

	let target = config.sync_folders.iter_mut().find(|f| f.folder_id == folder_id)
		.ok_or_else(|| "対象のフォルダが見つかりません。".to_string())?;
	target.local_path = local_path;

	*state.config.lock().unwrap() = config.clone();
	sync::save_config_file(&app, &config).map_err(|e| e.to_string())?;

	restart_watcher_service(&config, &state)?;

	let _ = state.scan_tx.send(sync::ScanEvent::Full).await;

	Ok(config)
}

#[tauri::command]
fn open_in_explorer(path: String) -> Result<(), String> {
	#[cfg(target_os = "windows")]
	{
		std::process::Command::new("explorer")
			.arg(&path)
			.spawn()
			.map_err(|e| e.to_string())?;
	}
	#[cfg(target_os = "macos")]
	{
		std::process::Command::new("open")
			.arg(&path)
			.spawn()
			.map_err(|e| e.to_string())?;
	}
	Ok(())
}


#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
struct LocalFileInfo {
	name: String,
	virtual_name: String,
	date: String,
}

#[tauri::command]
fn get_all_synced_files(state: State<'_, Arc<sync::AppState>>) -> Vec<LocalFileInfo> {
	let config = state.config.lock().unwrap().clone();
	let mut files = Vec::new();

	for folder in config.sync_folders {
		let path = Path::new(&folder.local_path);
		if !path.exists() { continue; }
		scan_dir_for_files(path, &folder.virtual_name, &mut files, 0);
	}
	files
}

fn scan_dir_for_files(dir: &Path, virtual_name: &str, files: &mut Vec<LocalFileInfo>, depth: usize) {
	if depth > 4 { return; }
	if let Ok(entries) = fs::read_dir(dir) {
		for entry in entries.flatten() {
			let path = entry.path();
			if path.is_file() {
				let name = path.file_name()
					.map(|n| n.to_string_lossy().to_string())
					.unwrap_or_default();
				let metadata = entry.metadata().ok();
				let date_str = metadata.and_then(|m| m.modified().ok())
					.map(|time| {
						let datetime: chrono::DateTime<chrono::Local> = time.into();
						datetime.format("%Y-%m-%d %H:%M").to_string()
					})
					.unwrap_or_else(|| "不明".to_string());
				files.push(LocalFileInfo { name, virtual_name: virtual_name.to_string(), date: date_str });
			} else if path.is_dir() {
				scan_dir_for_files(&path, virtual_name, files, depth + 1);
			}
		}
	}
}


#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
struct DeviceInfo {
	device_id: i32,
	device_name: String,
	os_type: String,
}

#[tauri::command]
async fn get_nas_devices(
	state: State<'_, Arc<sync::AppState>>,
) -> Result<Vec<DeviceInfo>, String> {
	let config = state.config.lock().unwrap().clone();
	if config.nas_url.is_empty() { return Ok(Vec::new()); }
	let client = reqwest::Client::new();
	let url = format!("{}/api/web/devices", config.nas_url);
	let res = client.get(&url).send().await
		.map_err(|e| format!("デバイス一覧取得エラー: {}", e))?;
	if !res.status().is_success() {
		return Err(format!("NASサーバーがエラーを返しました: HTTP {}", res.status()));
	}
	let devices: Vec<DeviceInfo> = res.json().await
		.map_err(|e| format!("デバイスデータ解析エラー: {}", e))?;
	Ok(devices)
}

#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
struct WebFolderInfo {
	folder_id: i32,
	device_id: i32,
	device_name: String,
	virtual_name: String,
	local_path: String,
}

#[tauri::command]
async fn get_nas_folders(
	state: State<'_, Arc<sync::AppState>>,
) -> Result<Vec<WebFolderInfo>, String> {
	let config = state.config.lock().unwrap().clone();
	if config.nas_url.is_empty() { return Ok(Vec::new()); }
	let client = reqwest::Client::new();
	let url = format!("{}/api/web/folders", config.nas_url);
	let res = client.get(&url).send().await
		.map_err(|e| format!("同期フォルダ一覧取得エラー: {}", e))?;
	if !res.status().is_success() {
		return Err(format!("NASサーバーがエラーを返しました: HTTP {}", res.status()));
	}
	let folders: Vec<WebFolderInfo> = res.json().await
		.map_err(|e| format!("フォルダデータ解析エラー: {}", e))?;
	Ok(folders)
}

#[tauri::command]
async fn update_exclude_patterns(
	state: State<'_, Arc<sync::AppState>>,
	app: AppHandle,
	patterns: Vec<String>,
) -> Result<sync::Config, String> {
	let mut config = state.config.lock().unwrap().clone();
	config.exclude_patterns = patterns;
	*state.config.lock().unwrap() = config.clone();
	sync::save_config_file(&app, &config).map_err(|e| e.to_string())?;
	Ok(config)
}

#[tauri::command]
fn get_local_subdirs(local_path: String) -> Result<Vec<String>, String> {
	let base = Path::new(&local_path);
	if !base.exists() {
		return Err("フォルダが存在しません。".to_string());
	}
	let mut subdirs = Vec::new();

	fn scan_dirs(base: &Path, current: &Path, list: &mut Vec<String>) -> io::Result<()> {
		if current.is_dir() {
			for entry in fs::read_dir(current)? {
				let entry = entry?;
				let path = entry.path();
				if path.is_dir() {
					let name = path.file_name().unwrap_or_default().to_string_lossy();
					if name.starts_with('.') || name == "$RECYCLE.BIN" { continue; }
					let rel = path.strip_prefix(base)
						.map_err(|e| io::Error::new(io::ErrorKind::Other, e))?
						.to_string_lossy()
						.replace('\\', "/");
					list.push(rel.clone());
					let _ = scan_dirs(base, &path, list);
				}
			}
		}
		Ok(())
	}

	let _ = scan_dirs(base, base, &mut subdirs);
	subdirs.sort();
	Ok(subdirs)
}


// --- Watcher Helper ---

fn restart_watcher_service(
	config: &sync::Config,
	state: &State<'_, Arc<sync::AppState>>,
) -> Result<(), String> {
	let paths: Vec<String> = config.sync_folders.iter().map(|f| f.local_path.clone()).collect();
	let mut old_watcher = state.watcher.lock().unwrap();
	*old_watcher = None;
	if !paths.is_empty() {
		let watcher = sync::start_watcher(paths, state.scan_tx.clone())?;
		*old_watcher = Some(watcher);
	}
	Ok(())
}


// --- App Entrypoint ---

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
	let (scan_tx, scan_rx) = mpsc::channel::<sync::ScanEvent>(256);

	tauri::Builder::default()
		.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
			// A second launch was attempted while an instance is already running
			// (e.g. hidden in the tray) -> surface the existing window instead of
			// starting a second process that would race the first one for sync.
			if let Some(window) = app.get_webview_window("main") {
				let _ = window.show();
				let _ = window.set_focus();
			}
		}))
		.plugin(tauri_plugin_opener::init())
		.setup(move |app| {
			let app_handle = app.handle().clone();
			let config = sync::load_config_file(&app_handle);

			let state = Arc::new(sync::AppState {
				config: Mutex::new(config.clone()),
				status: Mutex::new(sync::SyncStatus {
					is_syncing: false,
					current_file: "".to_string(),
					pending_tasks: 0,
					message: "待機中".to_string(),
					debug_emit_error_count: 0,
				}),
				scan_tx,
				watcher: Mutex::new(None),
				current_queue: Mutex::new(Vec::new()),
				last_direct_upload: Mutex::new(HashMap::new()),
			});
			app.manage(state.clone());

			// Start filesystem watcher
			let paths: Vec<String> = config.sync_folders.iter().map(|f| f.local_path.clone()).collect();
			if !paths.is_empty() {
				if let Ok(watcher) = sync::start_watcher(paths, state.scan_tx.clone()) {
					*state.watcher.lock().unwrap() = Some(watcher);
				}
			}

			// Spawn scan loop (handles full scans + watcher events)
			let scan_app = app_handle.clone();
			tauri::async_runtime::spawn(sync::run_scan_loop(scan_app, scan_rx));

			// Spawn execute loop (continuously fetches and executes tasks)
			let exec_app = app_handle.clone();
			tauri::async_runtime::spawn(sync::run_execute_loop(exec_app));

			// Spawn event listener (push notifications from the server so other
			// devices' changes trigger an immediate scan instead of waiting for
			// the periodic timer)
			let events_app = app_handle.clone();
			let events_tx = state.scan_tx.clone();
			tauri::async_runtime::spawn(sync::run_event_listener(events_app, events_tx));

			// System Tray
			let quit_i = MenuItem::with_id(app, "quit", "終了", true, None::<&str>)?;
			let show_i = MenuItem::with_id(app, "show", "設定を開く", true, None::<&str>)?;
			let menu = Menu::with_items(app, &[&show_i, &quit_i])?;
			let _tray = TrayIconBuilder::new()
				.icon(app.default_window_icon().cloned().expect("Missing default window icon"))
				.menu(&menu)
				.on_menu_event(|app, event| match event.id.as_ref() {
					"quit" => { app.exit(0); }
					"show" => {
						if let Some(window) = app.get_webview_window("main") {
							let _ = window.show();
							let _ = window.set_focus();
						}
					}
					_ => {}
				})
				.on_tray_icon_event(|tray, event| {
					if let TrayIconEvent::Click {
						button: MouseButton::Left,
						button_state: MouseButtonState::Up,
						..
					} = event {
						let app = tray.app_handle();
						if let Some(window) = app.get_webview_window("main") {
							let _ = window.show();
							let _ = window.set_focus();
						}
					}
				})
				.build(app)?;

			Ok(())
		})
		.on_window_event(|window, event| {
			if let tauri::WindowEvent::CloseRequested { api, .. } = event {
				window.hide().unwrap();
				api.prevent_close();
			}
		})
		.invoke_handler(tauri::generate_handler![
			get_config,
			get_status,
			get_current_queue,
			save_settings,
			add_folder,
			update_folder_path,
			force_sync,
			get_nas_devices,
			get_nas_folders,
			update_exclude_patterns,
			get_local_subdirs,
			select_folder,
			open_in_explorer,
			get_all_synced_files
		])
		.run(tauri::generate_context!())
		.expect("error while running tauri application");
}
