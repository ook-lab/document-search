use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use sha2::{Digest, Sha256};
use tokio::sync::mpsc;

use nas_sync_client_lib::sync::{
    self, AppState, Config, ScanEvent, SyncFolder, SyncStatus,
};

fn hash_sha256(input: impl AsRef<[u8]>) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input);
    hex::encode(hasher.finalize())
}

async fn register_test_device_and_folder(
    db: &tokio_postgres::Client,
    device_name: &str,
    virtual_name: &str,
    local_path: &str,
) -> (i32, i32, String) {
    let row = db
        .query_one(
            "INSERT INTO devices (device_name, os_type) VALUES ($1, 'test') RETURNING device_id",
            &[&device_name],
        )
        .await
        .expect("failed to register device in DB");
    let device_id: i32 = row.get(0);

    let row = db
        .query_one(
            "INSERT INTO sync_folders (device_id, local_path, virtual_name) VALUES ($1, $2, $3) RETURNING folder_id",
            &[&device_id, &local_path, &virtual_name],
        )
        .await
        .expect("failed to register sync_folder in DB");
    let folder_id: i32 = row.get(0);

    let raw_token = hash_sha256(format!("e2e-token-seed-{}", device_id));
    let token_hash = hash_sha256(&raw_token);

    db.execute(
        "INSERT INTO api_tokens (device_id, token_hash) VALUES ($1, $2)",
        &[&device_id, &token_hash],
    )
    .await
    .expect("failed to insert api_token in DB");

    (device_id, folder_id, raw_token)
}

fn setup_mock_app(
    local_dir: &Path,
    nas_url: &str,
    api_token: &str,
    folder_id: i32,
    device_id: i32,
) -> (
    tauri::AppHandle<tauri::test::MockRuntime>,
    Arc<AppState>,
    mpsc::Sender<ScanEvent>,
    mpsc::Receiver<ScanEvent>,
) {
    let (scan_tx, scan_rx) = mpsc::channel::<ScanEvent>(256);
    let config = Config {
        nas_url: nas_url.to_string(),
        api_token: api_token.to_string(),
        device_id,
        sync_folders: vec![SyncFolder {
            folder_id,
            local_path: local_dir.to_string_lossy().to_string(),
            virtual_name: "e2e_folder".to_string(),
        }],
        exclude_patterns: vec![],
    };

    let state = Arc::new(AppState {
        config: Mutex::new(config),
        status: Mutex::new(SyncStatus {
            is_syncing: false,
            current_file: String::new(),
            pending_tasks: 0,
            message: "Ready".to_string(),
            debug_emit_error_count: 0,
        }),
        scan_tx: scan_tx.clone(),
        watcher: Mutex::new(None),
        current_queue: Mutex::new(Vec::new()),
        hash_cache: Mutex::new(HashMap::new()),
    });

    let app = tauri::test::mock_builder()
        .manage(state.clone())
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .expect("failed to build mock tauri app");

    let app_handle = app.handle().clone();
    (app_handle, state, scan_tx, scan_rx)
}

async fn connect_db() -> tokio_postgres::Client {
    let pg_host = std::env::var("E2E_PG_HOST").unwrap_or_else(|_| "localhost".to_string());
    let pg_port = std::env::var("E2E_PG_PORT").unwrap_or_else(|_| "54322".to_string());
    let conn_str = format!(
        "host={} port={} user=postgres password=postgres dbname=postgres sslmode=disable",
        pg_host, pg_port
    );

    let (client, connection) = tokio_postgres::connect(&conn_str, tokio_postgres::NoTls)
        .await
        .unwrap_or_else(|e| panic!("Failed to connect to embedded Postgres at {}: {}", conn_str, e));

    tokio::spawn(async move {
        if let Err(e) = connection.await {
            eprintln!("DB connection error: {}", e);
        }
    });

    client
}

#[tokio::test]
async fn test_e2e_sync_all_scenarios() {
    let nas_url = std::env::var("E2E_HTTP_URL")
        .expect("E2E_HTTP_URL environment variable must be set (panic if missing)");
    let api_token = std::env::var("E2E_API_TOKEN")
        .expect("E2E_API_TOKEN environment variable must be set (panic if missing)");
    let folder_id_str = std::env::var("E2E_FOLDER_ID")
        .expect("E2E_FOLDER_ID environment variable must be set (panic if missing)");
    let folder_id: i32 = folder_id_str
        .parse()
        .expect("E2E_FOLDER_ID must be a valid i32 integer");

    let device_id_a: i32 = std::env::var("E2E_DEVICE_ID")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(1);

    let virtual_name = "e2e_folder";
    println!("Starting E2E sync integration test against {}", nas_url);
    let db = connect_db().await;
    let http_client = reqwest::Client::new();

    // Clean up mapping/task tables so test starts from a deterministic DB state
    db.execute("DELETE FROM sync_tasks", &[]).await.ok();
    db.execute("DELETE FROM device_mapping", &[]).await.ok();
    db.execute("DELETE FROM nas_mapping", &[]).await.ok();
    db.execute("DELETE FROM master_mapping", &[]).await.ok();

    // =========================================================================
    // Scenario A: Upload Test
    // =========================================================================
    println!("\n=== Scenario A: Upload Test ===");
    let temp_dir_a = tempfile::tempdir().expect("failed to create tempdir A");
    let file_a_path = temp_dir_a.path().join("upload_file_a.txt");
    let file_a_content = "Real binary/text content for Scenario A upload test";
    fs::write(&file_a_path, file_a_content).expect("failed to write upload_file_a.txt");

    let expected_hash_a = sync::calculate_hash(&file_a_path)
        .expect("failed to calculate hash for upload_file_a.txt");

    let (app_a, _state_a, scan_tx_a, scan_rx_a) = setup_mock_app(
        temp_dir_a.path(),
        &nas_url,
        &api_token,
        folder_id,
        device_id_a,
    );

    let scan_task_a = tokio::spawn(sync::run_scan_loop(app_a.clone(), scan_rx_a));
    let exec_task_a = tokio::spawn(sync::run_execute_loop(app_a.clone()));

    // Trigger full scan
    scan_tx_a.send(ScanEvent::Full).await.expect("failed to send ScanEvent::Full");

    // Poll until tasks endpoint reports no pending tasks AND DB master_mapping has matching file_hash
    let tasks_url = format!("{}/api/sync/tasks?folder_id={}", nas_url, folder_id);
    let mut scenario_a_passed = false;

    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;

        let res = http_client
            .get(&tasks_url)
            .header("Authorization", format!("Bearer {}", api_token))
            .send()
            .await;

        if let Ok(r) = res {
            if let Ok(val) = r.json::<serde_json::Value>().await {
                let pending = val.get("total_pending").and_then(|v| v.as_i64()).unwrap_or(-1);
                if pending == 0 {
                    let rows = db
                        .query(
                            "SELECT file_hash FROM master_mapping WHERE virtual_name = $1 AND relative_path = $2 AND deleted_at IS NULL",
                            &[&virtual_name, &"upload_file_a.txt"],
                        )
                        .await
                        .expect("failed to query master_mapping for Scenario A");

                    if !rows.is_empty() {
                        let db_hash: String = rows[0].get(0);
                        assert_eq!(db_hash, expected_hash_a, "Scenario A: DB file_hash must match calculate_hash");
                        scenario_a_passed = true;
                        println!("Scenario A PASSED on attempt {} (hash: {})", attempt, db_hash);
                        break;
                    }
                }
            }
        }
    }
    assert!(scenario_a_passed, "Scenario A FAILED: File upload was not completed or DB record was missing");

    // =========================================================================
    // Scenario B: Download Test
    // =========================================================================
    println!("\n=== Scenario B: Download Test ===");
    // Precondition: Seed a file on the server by uploading it via app_a's token to /api/sync/upload
    let file_b_name = "download_file_b.txt";
    let file_b_content = "Server-seeded content for Scenario B download test";
    let file_b_bytes = file_b_content.as_bytes().to_vec();
    let file_b_hash = hash_sha256(&file_b_bytes);
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    let part = reqwest::multipart::Part::bytes(file_b_bytes)
        .file_name(file_b_name.to_string())
        .mime_str("text/plain")
        .unwrap();

    let form = reqwest::multipart::Form::new()
        .text("folder_id", folder_id.to_string())
        .text("relative_path", file_b_name.to_string())
        .text("file_size", file_b_content.len().to_string())
        .text("file_hash", file_b_hash)
        .text("last_modified_at", now_secs.to_string())
        .text("file_id", "0")
        .text("expected_version", "0")
        .part("file", part);

    let upload_url = format!("{}/api/sync/upload", nas_url);
    let upload_res = http_client
        .post(&upload_url)
        .header("Authorization", format!("Bearer {}", api_token))
        .multipart(form)
        .send()
        .await
        .expect("failed to seed file B via upload endpoint");

    assert!(
        upload_res.status().is_success(),
        "Scenario B precondition FAILED: Could not upload file B to server (HTTP {})",
        upload_res.status()
    );
    println!("Scenario B precondition: file B seeded to server via /api/sync/upload");

    // Register a brand-new device + API token + folder for App B
    let (device_id_b, folder_id_b, api_token_b) = register_test_device_and_folder(
        &db,
        "e2e-device-2",
        virtual_name,
        "/e2e/local-b",
    )
    .await;

    // Now create a SECOND tempdir B (empty) and SECOND mock app
    let temp_dir_b = tempfile::tempdir().expect("failed to create tempdir B");
    let (app_b, _state_b, scan_tx_b, scan_rx_b) = setup_mock_app(
        temp_dir_b.path(),
        &nas_url,
        &api_token_b,
        folder_id_b,
        device_id_b,
    );

    let scan_task_b = tokio::spawn(sync::run_scan_loop(app_b.clone(), scan_rx_b));
    let exec_task_b = tokio::spawn(sync::run_execute_loop(app_b.clone()));

    // Force scan on App B (which sees file B on server but missing locally)
    scan_tx_b.send(ScanEvent::Full).await.expect("failed to send ScanEvent::Full to App B");

    let expected_download_path = temp_dir_b.path().join(file_b_name);
    let mut scenario_b_passed = false;

    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        if expected_download_path.exists() {
            let actual_content = fs::read_to_string(&expected_download_path)
                .expect("failed to read downloaded file content");
            assert_eq!(
                actual_content, file_b_content,
                "Scenario B: Downloaded file content must match seeded content"
            );
            scenario_b_passed = true;
            println!("Scenario B PASSED on attempt {}: File downloaded and content verified.", attempt);
            break;
        }
    }
    assert!(scenario_b_passed, "Scenario B FAILED: File download was not completed into tempdir B");

    // Clean up App B tasks
    scan_task_b.abort();
    exec_task_b.abort();

    // =========================================================================
    // Scenario C: Delete Test
    // =========================================================================
    println!("\n=== Scenario C: Delete Test ===");
    // Delete file_a locally in temp_dir_a
    fs::remove_file(&file_a_path).expect("failed to delete upload_file_a.txt in tempdir A");

    // Trigger scan on App A
    scan_tx_a.send(ScanEvent::Full).await.expect("failed to trigger scan A for delete test");

    // Poll DB until master_mapping.deleted_at becomes non-null
    let mut scenario_c_passed = false;
    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let row = db
            .query_one(
                "SELECT deleted_at IS NOT NULL FROM master_mapping WHERE virtual_name = $1 AND relative_path = $2",
                &[&virtual_name, &"upload_file_a.txt"],
            )
            .await;

        if let Ok(r) = row {
            let is_deleted: bool = r.get(0);
            if is_deleted {
                scenario_c_passed = true;
                println!("Scenario C PASSED on attempt {}: Deletion propagated to master_mapping.deleted_at.", attempt);
                break;
            }
        }
    }
    assert!(scenario_c_passed, "Scenario C FAILED: Deletion of upload_file_a.txt did not propagate to DB");

    // =========================================================================
    // Scenario D: Move / Rename Test
    // =========================================================================
    println!("\n=== Scenario D: Move/Rename Test ===");
    let move_src_name = "move_src_file.txt";
    let move_dst_name = "move_dst_file.txt";
    let move_src_path = temp_dir_a.path().join(move_src_name);
    let move_dst_path = temp_dir_a.path().join(move_dst_name);

    fs::write(&move_src_path, "Content for move/rename scenario test")
        .expect("failed to write move_src_file.txt");

    let expected_move_hash = sync::calculate_hash(&move_src_path)
        .expect("failed to calculate hash for move_src_file.txt");

    // Trigger scan A to upload move_src_file.txt
    scan_tx_a.send(ScanEvent::Full).await.expect("failed to trigger scan A for move_src");

    // Wait until move_src_file.txt is uploaded
    let mut move_src_uploaded = false;
    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let rows = db
            .query(
                "SELECT relative_path FROM master_mapping WHERE virtual_name = $1 AND relative_path = $2 AND deleted_at IS NULL",
                &[&virtual_name, &move_src_name],
            )
            .await
            .expect("failed to query DB for move_src");

        if !rows.is_empty() {
            move_src_uploaded = true;
            println!("Scenario D precondition: move_src uploaded on attempt {}", attempt);
            break;
        }
    }
    assert!(move_src_uploaded, "Scenario D precondition FAILED: move_src_file.txt was not uploaded");

    // Rename file locally from move_src_name to move_dst_name
    fs::rename(&move_src_path, &move_dst_path).expect("failed to rename file locally");

    // Trigger scan A again to detect the change
    scan_tx_a.send(ScanEvent::Full).await.expect("failed to trigger scan A after rename");

    // Poll DB until master_mapping row for expected_move_hash has relative_path == move_dst_name
    let mut scenario_d_passed = false;
    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let rows = db
            .query(
                "SELECT relative_path FROM master_mapping WHERE virtual_name = $1 AND file_hash = $2 AND deleted_at IS NULL",
                &[&virtual_name, &expected_move_hash],
            )
            .await
            .expect("failed to query DB for move_dst");

        if !rows.is_empty() {
            let current_rel_path: String = rows[0].get(0);
            if current_rel_path == move_dst_name {
                scenario_d_passed = true;
                println!("Scenario D PASSED on attempt {}: master_mapping.relative_path updated to {}.", attempt, move_dst_name);
                break;
            }
        }
    }
    assert!(scenario_d_passed, "Scenario D FAILED: Renamed file relative_path was not updated in DB");

    // Clean up App A tasks
    scan_task_a.abort();
    exec_task_a.abort();

    // =========================================================================
    // Scenario E: Conflict Fork Test
    // =========================================================================
    println!("\n=== Scenario E: Conflict Fork Test ===");

    // 1. Register a genuinely separate THIRD device (device C)
    let (device_id_c, folder_id_c, api_token_c) = register_test_device_and_folder(
        &db,
        "e2e-device-3",
        virtual_name,
        "/e2e/local-c",
    )
    .await;

    // 2. Seed initial conflict_test.txt on the server via Device A's token
    let conflict_file_name = "conflict_test.txt";
    let initial_content = "initial content on device A";
    let file_a_bytes = initial_content.as_bytes().to_vec();
    let file_a_hash = hash_sha256(&file_a_bytes);
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    // Write it to Device A's temp dir physically so it's there
    let conflict_file_path_a = temp_dir_a.path().join(conflict_file_name);
    fs::write(&conflict_file_path_a, initial_content).expect("failed to write conflict_test.txt on device A");

    let part = reqwest::multipart::Part::bytes(file_a_bytes)
        .file_name(conflict_file_name.to_string())
        .mime_str("text/plain")
        .unwrap();

    let form = reqwest::multipart::Form::new()
        .text("folder_id", folder_id.to_string())
        .text("relative_path", conflict_file_name.to_string())
        .text("file_size", initial_content.len().to_string())
        .text("file_hash", file_a_hash)
        .text("last_modified_at", now_secs.to_string())
        .text("file_id", "0")
        .text("expected_version", "0")
        .part("file", part);

    let upload_url = format!("{}/api/sync/upload", nas_url);
    let upload_res = http_client
        .post(&upload_url)
        .header("Authorization", format!("Bearer {}", api_token))
        .multipart(form)
        .send()
        .await
        .expect("failed to upload initial conflict_test.txt");

    assert!(
        upload_res.status().is_success(),
        "Scenario E initial upload failed (HTTP {})",
        upload_res.status()
    );

    let upload_body: serde_json::Value = upload_res
        .json()
        .await
        .expect("failed to parse initial upload response JSON");

    let file_id_v1 = upload_body
        .get("file_id")
        .and_then(|v| v.as_i64())
        .unwrap_or(0) as i32;
    let version_num_v1 = upload_body
        .get("version")
        .and_then(|v| v.as_i64())
        .unwrap_or(0) as i32;

    assert_ne!(file_id_v1, 0, "Scenario E: file_id should be non-zero");
    assert_eq!(version_num_v1, 1, "Scenario E: initial version_num should be 1");
    println!("Scenario E: conflict_test.txt seeded (file_id: {}, version: {})", file_id_v1, version_num_v1);

    // 3. Using device C, download this same file into device C's own tempdir
    let temp_dir_c = tempfile::tempdir().expect("failed to create tempdir C");
    let (app_c, _state_c, scan_tx_c, scan_rx_c) = setup_mock_app(
        temp_dir_c.path(),
        &nas_url,
        &api_token_c,
        folder_id_c,
        device_id_c,
    );

    let scan_task_c = tokio::spawn(sync::run_scan_loop(app_c.clone(), scan_rx_c));
    let exec_task_c = tokio::spawn(sync::run_execute_loop(app_c.clone()));

    // Force scan on App C (sees file on server, downloads it)
    scan_tx_c.send(ScanEvent::Full).await.expect("failed to send ScanEvent::Full to App C");

    let expected_download_path_c = temp_dir_c.path().join(conflict_file_name);
    let mut c_downloaded = false;

    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        {
            let status = _state_c.status.lock().unwrap();
            println!("Attempt {} - Device C status: message='{}', pending_tasks={}", attempt, status.message, status.pending_tasks);
        }
        let tasks_rows = db.query("SELECT task_id, device_id, action_type, relative_path, status FROM sync_tasks", &[]).await.unwrap();
        for r in tasks_rows {
            let tid: i32 = r.get(0);
            let did: i32 = r.get(1);
            let act: String = r.get(2);
            let path: String = r.get(3);
            let stat: String = r.get(4);
            println!("   [DB task] task_id={}, device_id={}, action_type={}, path={}, status={}", tid, did, act, path, stat);
        }
        if expected_download_path_c.exists() {
            let actual_content = fs::read_to_string(&expected_download_path_c)
                .expect("failed to read downloaded file on device C");
            assert_eq!(actual_content, initial_content, "Scenario E: Downloaded content on device C mismatch");
            c_downloaded = true;
            println!("Scenario E: Device C download PASSED on attempt {}", attempt);
            break;
        }
    }
    assert!(c_downloaded, "Scenario E FAILED: Device C failed to download conflict_test.txt");

    // Verify in DB that device C mapped version is 1
    let rows_c_init = db
        .query(
            "SELECT last_synced_version, last_synced_hash FROM device_mapping WHERE device_id = $1 AND file_id = $2",
            &[&device_id_c, &file_id_v1],
        )
        .await
        .expect("failed to query device_mapping for device C");
    assert!(!rows_c_init.is_empty(), "Scenario E FAILED: device_mapping for device C not found after download");
    let c_synced_version_init: i32 = rows_c_init[0].get(0);
    assert_eq!(c_synced_version_init, 1, "Scenario E FAILED: device C's synced version in DB should be 1");

    // 4. Simulate a genuine conflict:
    // a. Edit local file on device A and upload (succeeds normally, CAS version 1 to 2)
    let first_edit_content = "edited content on device A (v2)";
    let file_a_edit_bytes = first_edit_content.as_bytes().to_vec();
    let file_a_edit_hash = hash_sha256(&file_a_edit_bytes);
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    let part = reqwest::multipart::Part::bytes(file_a_edit_bytes)
        .file_name(conflict_file_name.to_string())
        .mime_str("text/plain")
        .unwrap();

    let form = reqwest::multipart::Form::new()
        .text("folder_id", folder_id.to_string())
        .text("relative_path", conflict_file_name.to_string())
        .text("file_size", first_edit_content.len().to_string())
        .text("file_hash", file_a_edit_hash.clone())
        .text("last_modified_at", now_secs.to_string())
        .text("file_id", file_id_v1.to_string())
        .text("expected_version", "1") // Device A has version 1 cached
        .part("file", part);

    let upload_res = http_client
        .post(&upload_url)
        .header("Authorization", format!("Bearer {}", api_token))
        .multipart(form)
        .send()
        .await
        .expect("failed to upload Device A's edit");

    assert!(
        upload_res.status().is_success(),
        "Scenario E Device A edit upload failed (HTTP {})",
        upload_res.status()
    );

    let upload_body_a: serde_json::Value = upload_res
        .json()
        .await
        .expect("failed to parse Device A edit response JSON");

    let version_a = upload_body_a
        .get("version")
        .and_then(|v| v.as_i64())
        .unwrap_or(0) as i32;
    assert_eq!(version_a, 2, "Scenario E: Device A edit should update version to 2");
    println!("Scenario E: conflict_test.txt version 2 uploaded (file_id: {}, version: {})", file_id_v1, version_a);

    // Abort device C tasks so client loops don't background sync and catch up to v2 automatically
    scan_task_c.abort();
    exec_task_c.abort();

    // b. Edit device C's local copy to a third content and upload manually (expected_version = 1)
    let third_content = "third content edited on device C (conflict)";
    fs::write(&expected_download_path_c, third_content).expect("failed to edit local file on device C");

    let file_c_bytes = third_content.as_bytes().to_vec();
    let file_c_hash = hash_sha256(&file_c_bytes);
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();

    let part = reqwest::multipart::Part::bytes(file_c_bytes)
        .file_name(conflict_file_name.to_string())
        .mime_str("text/plain")
        .unwrap();

    let form = reqwest::multipart::Form::new()
        .text("folder_id", folder_id_c.to_string())
        .text("relative_path", conflict_file_name.to_string())
        .text("file_size", third_content.len().to_string())
        .text("file_hash", file_c_hash.clone())
        .text("last_modified_at", now_secs.to_string())
        .text("file_id", file_id_v1.to_string())
        .text("expected_version", "1") // device C's cached expected_version
        .part("file", part);

    let upload_url = format!("{}/api/sync/upload", nas_url);
    let upload_res = http_client
        .post(&upload_url)
        .header("Authorization", format!("Bearer {}", api_token_c))
        .multipart(form)
        .send()
        .await
        .expect("failed to send conflict upload for device C");

    assert!(
        upload_res.status().is_success(),
        "Scenario E FAILED: Upload request to simulate conflict failed (HTTP {})",
        upload_res.status()
    );

    let upload_body: serde_json::Value = upload_res
        .json()
        .await
        .expect("failed to parse conflict upload response JSON");

    println!("Scenario E: Upload response body: {:?}", upload_body);

    // 5. Assert concretely via both HTTP response and DB:
    // Check HTTP Response payload
    let resp_forked = upload_body
        .get("forked")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let resp_new_file_id = upload_body
        .get("new_file_id")
        .and_then(|v| v.as_i64())
        .unwrap_or(0) as i32;
    let resp_new_relative_path = upload_body
        .get("new_relative_path")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    assert!(resp_forked, "Scenario E FAILED: HTTP response should have forked=true");
    assert_ne!(resp_new_file_id, file_id_v1, "Scenario E FAILED: new_file_id should be different from original file_id");
    assert_eq!(
        resp_new_relative_path, "conflict_test (from e2e-device-3).txt",
        "Scenario E FAILED: new_relative_path name convention mismatch. Expected: conflict_test (from e2e-device-3).txt, got: {}",
        resp_new_relative_path
    );

    // Query DB mapping details
    // Original file mapping
    let original_rows = db
        .query(
            "SELECT version_num, file_hash, deleted_at FROM master_mapping WHERE file_id = $1",
            &[&file_id_v1],
        )
        .await
        .expect("failed to query master_mapping for original file");
    assert!(!original_rows.is_empty(), "Scenario E FAILED: original master_mapping row missing");
    let orig_version: i32 = original_rows[0].get(0);
    let orig_hash: String = original_rows[0].get(1);
    let orig_deleted: Option<std::time::SystemTime> = original_rows[0].get(2);

    assert_eq!(orig_version, 2, "Scenario E FAILED: original version should remain 2");
    assert_eq!(orig_hash, file_a_edit_hash, "Scenario E FAILED: original hash should remain v2 hash");
    assert!(orig_deleted.is_none(), "Scenario E FAILED: original file should not be soft-deleted");

    // Forked file mapping
    let forked_rows = db
        .query(
            "SELECT version_num, file_hash, deleted_at, relative_path FROM master_mapping WHERE file_id = $1",
            &[&resp_new_file_id],
        )
        .await
        .expect("failed to query master_mapping for forked file");
    assert!(!forked_rows.is_empty(), "Scenario E FAILED: forked master_mapping row missing in DB");
    let fork_version: i32 = forked_rows[0].get(0);
    let fork_hash: String = forked_rows[0].get(1);
    let fork_deleted: Option<std::time::SystemTime> = forked_rows[0].get(2);
    let fork_path: String = forked_rows[0].get(3);

    assert_eq!(fork_version, 1, "Scenario E FAILED: forked file version should be 1");
    assert_eq!(fork_hash, file_c_hash, "Scenario E FAILED: forked hash should match device C's input hash");
    assert!(fork_deleted.is_none(), "Scenario E FAILED: forked file should not be soft-deleted");
    assert_eq!(fork_path, "conflict_test (from e2e-device-3).txt");

    // Check device_mapping cleanup for the ORIGINAL file_id of device C
    let dev_mapping_rows = db
        .query(
            "SELECT 1 FROM device_mapping WHERE device_id = $1 AND file_id = $2",
            &[&device_id_c, &file_id_v1],
        )
        .await
        .expect("failed to query device_mapping for original file ID");
    
    let original_mapping_cleaned = dev_mapping_rows.is_empty();
    if !original_mapping_cleaned {
        println!("WARNING: device_mapping for original file_id was NOT cleaned up for device C!");
    } else {
        println!("Scenario E: device_mapping for original file_id was successfully cleaned up for device C.");
    }
    
    // Perform assertion if matching design doc
    assert!(original_mapping_cleaned, "Scenario E FAILED: device_mapping for original file_id was not cleaned up for device C per conflict design");

    // =========================================================================
    // Scenario F: Re-anchor Test
    // =========================================================================
    println!("\n=== Scenario F: Re-anchor Test ===");

    // 1. Register a genuinely new device (device D) via register_test_device_and_folder
    let (device_id_d, folder_id_d, api_token_d) = register_test_device_and_folder(
        &db,
        "e2e-device-4",
        virtual_name,
        "/e2e/local-d",
    )
    .await;

    // 2. Create a local file with real content, upload it via device D normally through a real scan/sync cycle.
    let temp_dir_d = tempfile::tempdir().expect("failed to create tempdir D");
    let file_d_name = "reanchor_test.txt";
    let file_d_path = temp_dir_d.path().join(file_d_name);
    let file_d_content = "Real content for Scenario F re-anchor test";
    fs::write(&file_d_path, file_d_content).expect("failed to write reanchor_test.txt");

    let expected_hash_d = sync::calculate_hash(&file_d_path)
        .expect("failed to calculate hash for reanchor_test.txt");

    let (app_d, _state_d, scan_tx_d, scan_rx_d) = setup_mock_app(
        temp_dir_d.path(),
        &nas_url,
        &api_token_d,
        folder_id_d,
        device_id_d,
    );

    let scan_task_d = tokio::spawn(sync::run_scan_loop(app_d.clone(), scan_rx_d));
    let exec_task_d = tokio::spawn(sync::run_execute_loop(app_d.clone()));

    // Trigger full scan
    scan_tx_d.send(ScanEvent::Full).await.expect("failed to send ScanEvent::Full to Device D");

    // Wait until upload task completes and no pending tasks remain
    let tasks_url_d = format!("{}/api/sync/tasks?folder_id={}", nas_url, folder_id_d);
    let mut upload_completed = false;
    let mut original_file_id: i32 = 0;

    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;

        let res = http_client
            .get(&tasks_url_d)
            .header("Authorization", format!("Bearer {}", api_token_d))
            .send()
            .await;

        if let Ok(r) = res {
            if let Ok(val) = r.json::<serde_json::Value>().await {
                let pending = val.get("total_pending").and_then(|v| v.as_i64()).unwrap_or(-1);
                if pending == 0 {
                    // Confirm via real DB query that it lands in master_mapping with a real file_id
                    let rows = db
                        .query(
                            "SELECT file_id, file_hash FROM master_mapping WHERE virtual_name = $1 AND relative_path = $2 AND deleted_at IS NULL",
                            &[&virtual_name, &file_d_name],
                        )
                        .await
                        .expect("failed to query master_mapping for Scenario F");

                    if !rows.is_empty() {
                        let db_id: i32 = rows[0].get(0);
                        let db_hash: String = rows[0].get(1);
                        assert_eq!(db_hash, expected_hash_d, "Scenario F: DB file_hash must match calculate_hash");
                        original_file_id = db_id;
                        upload_completed = true;
                        println!("Scenario F upload completed on attempt {} (file_id: {}, hash: {})", attempt, db_id, db_hash);
                        break;
                    }
                }
            }
        }
    }
    assert!(upload_completed, "Scenario F FAILED: File upload was not completed or DB record was missing");

    // Clean up first mock app client loops
    scan_task_d.abort();
    exec_task_d.abort();

    // 3. Simulate device D's local cache being fully lost (e.g. reinstall / hash_cache.json deleted):
    // Construct a FRESH AppState for device D — same device_id/token/folder, but hash_cache: HashMap::new() (genuinely empty) —
    // while the physical file on disk is untouched (same path, same content).
    let (app_d_fresh, state_d_fresh, scan_tx_d_fresh, scan_rx_d_fresh) = setup_mock_app(
        temp_dir_d.path(),
        &nas_url,
        &api_token_d,
        folder_id_d,
        device_id_d,
    );

    // Verify hash_cache is empty
    {
        let cache = state_d_fresh.hash_cache.lock().unwrap();
        assert!(cache.is_empty(), "Fresh app state hash_cache must be empty");
    }

    // Spawn loops for fresh app
    let scan_task_d_fresh = tokio::spawn(sync::run_scan_loop(app_d_fresh.clone(), scan_rx_d_fresh));
    let exec_task_d_fresh = tokio::spawn(sync::run_execute_loop(app_d_fresh.clone()));

    // 4. Trigger a full scan from this cache-less state
    scan_tx_d_fresh.send(ScanEvent::Full).await.expect("failed to trigger scan for fresh client");

    // Poll until the fresh scan is complete. Since no tasks are expected, we can poll the status message.
    let mut scan_completed = false;
    for attempt in 1..=30 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let status = state_d_fresh.status.lock().unwrap();
        if status.message == "同期完了" {
            scan_completed = true;
            println!("Scenario F scan completed on attempt {}", attempt);
            break;
        }
    }
    assert!(scan_completed, "Scenario F FAILED: Fresh scan did not complete");

    // Abort fresh client loops
    scan_task_d_fresh.abort();
    exec_task_d_fresh.abort();

    // 5. Assert via real server-side DB state after the scan that:
    // - The server RE-ANCHORED the report to the EXISTING original_file_id (matched by path + hash) rather than creating a new file_id.
    // - master_mapping still has exactly ONE row for this path, still original_file_id, not soft-deleted.
    // - device D's device_mapping row now reflects original_file_id again (recovered), proving actual re-anchoring occurred.
    
    // Check master_mapping
    let master_rows = db
        .query(
            "SELECT file_id, deleted_at FROM master_mapping WHERE virtual_name = $1 AND relative_path = $2",
            &[&virtual_name, &file_d_name],
        )
        .await
        .expect("failed to query master_mapping after re-anchor scan");

    assert_eq!(master_rows.len(), 1, "Scenario F FAILED: master_mapping must have exactly one row for this path (no duplicate IDs)");
    let final_master_file_id: i32 = master_rows[0].get(0);
    let final_master_deleted: Option<std::time::SystemTime> = master_rows[0].get(1);

    assert_eq!(final_master_file_id, original_file_id, "Scenario F FAILED: master_mapping file_id must match original_file_id");
    assert!(final_master_deleted.is_none(), "Scenario F FAILED: master_mapping row must not be soft-deleted");

    // Check device_mapping for device D
    let device_rows = db
        .query(
            "SELECT file_id, last_synced_path, last_synced_hash FROM device_mapping WHERE device_id = $1 AND file_id = $2",
            &[&device_id_d, &original_file_id],
        )
        .await
        .expect("failed to query device_mapping for device D after re-anchor scan");

    assert_eq!(device_rows.len(), 1, "Scenario F FAILED: device_mapping must have exactly one row matching (device_id_d, original_file_id)");
    let final_device_file_id: i32 = device_rows[0].get(0);
    let final_device_path: Option<String> = device_rows[0].get(1);
    let final_device_hash: String = device_rows[0].get(2);

    assert_eq!(final_device_file_id, original_file_id, "Scenario F FAILED: device_mapping file_id must match original_file_id");
    assert_eq!(final_device_path.as_deref(), Some(file_d_name), "Scenario F FAILED: device_mapping path mismatch");
    assert_eq!(final_device_hash, expected_hash_d, "Scenario F FAILED: device_mapping hash mismatch");

    // 6. Confirm no mass-deletion/spurious deletion occurred as a side effect
    // Check that Scenario E's conflict_test.txt (file_id_v1) is still untouched/not deleted in master_mapping
    let scenario_e_rows = db
        .query(
            "SELECT deleted_at FROM master_mapping WHERE file_id = $1",
            &[&file_id_v1],
        )
        .await
        .expect("failed to query Scenario E file status");
    assert!(!scenario_e_rows.is_empty(), "Scenario F check: Scenario E's conflict file must still exist");
    let scenario_e_deleted: Option<std::time::SystemTime> = scenario_e_rows[0].get(0);
    assert!(scenario_e_deleted.is_none(), "Scenario F FAILED: Scenario E's conflict file was spuriously soft-deleted!");

    println!("Scenario F PASSED successfully!");

    println!("\nALL E2E SCENARIOS (A, B, C, D, E, F) PASSED SUCCESSFULLY!");
}
