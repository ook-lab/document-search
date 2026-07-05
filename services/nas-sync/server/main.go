package main

import (
	"crypto/sha256"
	"database/sql"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	_ "github.com/lib/pq"
)

// App context
type App struct {
	db          *sql.DB
	storagePath string
}

//go:embed dist/*
var webAssets embed.FS

// JSON request/response types
type RegisterDeviceRequest struct {
	DeviceName string `json:"device_name"`
	OSType     string `json:"os_type"`
}

type RegisterDeviceResponse struct {
	DeviceID int    `json:"device_id"`
	Token    string `json:"token"`
}

type RegisterFolderRequest struct {
	LocalPath   string `json:"local_path"`
	VirtualName string `json:"virtual_name"`
}

type RegisterFolderResponse struct {
	FolderID int `json:"folder_id"`
}

type FileMetadata struct {
	RelativePath   string    `json:"relative_path"`
	FileName       string    `json:"file_name"`
	IsDirectory    bool      `json:"is_directory"`
	FileSize       int64     `json:"file_size"`
	FileHash       string    `json:"file_hash"`
	LastModifiedAt time.Time `json:"last_modified_at"`
}

type ScanRequest struct {
	FolderID int            `json:"folder_id"`
	Files    []FileMetadata `json:"files"`
}

type MoveTask struct {
	FromPath string `json:"from_path"`
	ToPath   string `json:"to_path"`
}

type GoSyncTask struct {
	TaskID         int    `json:"task_id"`
	RelativePath   string `json:"relative_path"`
	ToPath         string `json:"to_path"`
	ActionType     string `json:"action_type"`
	FileSize       int64  `json:"file_size"`
	FileHash       string `json:"file_hash"`
	LastModifiedBy string `json:"last_modified_by"`
}

type ScanResponse struct {
	Tasks        []GoSyncTask `json:"tasks"`
	TotalPending int          `json:"total_pending"`
}

type MoveRequest struct {
	FolderID int    `json:"folder_id"`
	FromPath string `json:"from_path"`
	ToPath   string `json:"to_path"`
}

type DeleteRequest struct {
	FolderID     int    `json:"folder_id"`
	RelativePath string `json:"relative_path"`
}

func main() {
	// 1. Read environment variables
	dbHost := getEnv("DB_HOST", "localhost")
	dbPort := getEnv("DB_PORT", "5432")
	dbUser := getEnv("DB_USER", "sync_user")
	dbPass := getEnv("DB_PASSWORD", "sync_secure_password_2026")
	dbName := getEnv("DB_NAME", "sync_metadata")
	storagePath := getEnv("STORAGE_PATH", "./storage")

	// 2. Ensure storage path exists
	if err := os.MkdirAll(storagePath, 0755); err != nil {
		log.Fatalf("Failed to create storage directory: %v", err)
	}

	// 3. Connect to database
	connStr := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable",
		dbHost, dbPort, dbUser, dbPass, dbName)
	
	var db *sql.DB
	var err error
	
	// Retry connection a few times for container startup ordering
	for i := 1; i <= 5; i++ {
		db, err = sql.Open("postgres", connStr)
		if err == nil {
			err = db.Ping()
			if err == nil {
				break
			}
		}
		log.Printf("Database connection attempt %d failed: %v. Retrying in 3 seconds...", i, err)
		time.Sleep(3 * time.Second)
	}
	if err != nil {
		log.Fatalf("Could not connect to database: %v", err)
	}
	defer db.Close()

	log.Println("Successfully connected to database.")

	// Ensure mapping tables exist (3-Tier: master / nas / device)
	_, err = db.Exec(`
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
			last_modified_by_device_id INT REFERENCES devices(device_id) ON DELETE SET NULL,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			CONSTRAINT unique_master_path UNIQUE (virtual_name, relative_path)
		);

		CREATE TABLE IF NOT EXISTS nas_mapping (
			file_id INT REFERENCES master_mapping(file_id) ON DELETE CASCADE PRIMARY KEY,
			file_size BIGINT NOT NULL,
			file_hash VARCHAR(64) NOT NULL,
			last_modified_at TIMESTAMP NOT NULL
		);

		CREATE TABLE IF NOT EXISTS device_mapping (
			device_id INT REFERENCES devices(device_id) ON DELETE CASCADE,
			file_id INT REFERENCES master_mapping(file_id) ON DELETE CASCADE,
			last_synced_mtime TIMESTAMP NOT NULL,
			last_synced_size BIGINT NOT NULL,
			last_synced_hash VARCHAR(64) NOT NULL,
			PRIMARY KEY (device_id, file_id)
		);

		CREATE TABLE IF NOT EXISTS sync_tasks (
			task_id SERIAL PRIMARY KEY,
			device_id INT REFERENCES devices(device_id) ON DELETE CASCADE,
			folder_id INT REFERENCES sync_folders(folder_id) ON DELETE CASCADE,
			relative_path VARCHAR(1024) NOT NULL,
			to_path VARCHAR(1024) DEFAULT '',
			action_type VARCHAR(20) NOT NULL,
			status VARCHAR(20) NOT NULL DEFAULT 'pending',
			file_size BIGINT NOT NULL DEFAULT 0,
			file_hash VARCHAR(64) NOT NULL DEFAULT '',
			last_modified_by VARCHAR(100) DEFAULT '',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);

		CREATE INDEX IF NOT EXISTS idx_sync_tasks_device ON sync_tasks(device_id, status);
		CREATE INDEX IF NOT EXISTS idx_master_mapping_lookup ON master_mapping(virtual_name, is_deleted);

		-- Dynamic migrations for existing databases
		ALTER TABLE master_mapping ADD COLUMN IF NOT EXISTS last_modified_by_device_id INT REFERENCES devices(device_id) ON DELETE SET NULL;
		ALTER TABLE sync_tasks ADD COLUMN IF NOT EXISTS last_modified_by VARCHAR(100) DEFAULT '';
		ALTER TABLE sync_tasks DROP COLUMN IF EXISTS device_hash;
		ALTER TABLE sync_tasks DROP COLUMN IF EXISTS file_modified_at;
	`)
	if err != nil {
		log.Printf("Failed to create mapping tables: %v", err)
	}

	// Migrate: populate nas_mapping from is_uploaded=TRUE rows (one-time, idempotent)
	_, _ = db.Exec(`
		INSERT INTO nas_mapping (file_id, file_size, file_hash, last_modified_at)
		SELECT file_id, file_size, file_hash, last_modified_at
		FROM master_mapping
		WHERE is_uploaded = TRUE AND is_deleted = FALSE
		ON CONFLICT DO NOTHING
	`) // Silently ignored if is_uploaded column already dropped

	// Remove obsolete column
	_, _ = db.Exec(`ALTER TABLE master_mapping DROP COLUMN IF EXISTS is_uploaded`)


	app := &App{
		db:          db,
		storagePath: storagePath,
	}

	// 4. Set up routes
	mux := http.NewServeMux()

	// Client Sync APIs
	mux.HandleFunc("/api/devices/register", app.handleRegisterDevice)
	mux.Handle("/api/sync/folders", app.authMiddleware(http.HandlerFunc(app.handleRegisterFolder)))
	mux.Handle("/api/sync/scan", app.authMiddleware(http.HandlerFunc(app.handleScan)))
	mux.Handle("/api/sync/upload", app.authMiddleware(http.HandlerFunc(app.handleUpload)))
	mux.Handle("/api/sync/download", app.authMiddleware(http.HandlerFunc(app.handleDownload)))
	mux.Handle("/api/sync/delete", app.authMiddleware(http.HandlerFunc(app.handleDelete)))
	mux.Handle("/api/sync/move", app.authMiddleware(http.HandlerFunc(app.handleMove)))
	mux.Handle("/api/sync/tasks", app.authMiddleware(http.HandlerFunc(app.handleGetTasks)))
	mux.Handle("/api/sync/tasks/complete", app.authMiddleware(http.HandlerFunc(app.handleCompleteTask)))


	// Web UI APIs (Read-only / Delete via web browser client)
	mux.HandleFunc("/api/web/folders", app.handleWebFolders)
	mux.HandleFunc("/api/web/folders/unified", app.handleWebFoldersUnified)
	mux.HandleFunc("/api/web/files", app.handleWebFiles)

	mux.HandleFunc("/api/web/activities", app.handleWebActivities)
	mux.HandleFunc("/api/web/devices", app.handleWebDevices)


	// Web UI Static Hosting (embedded)
	subFS, err := fs.Sub(webAssets, "dist")
	if err != nil {
		log.Fatalf("Failed to extract embedded assets sub-directory: %v", err)
	}
	mux.Handle("/", http.FileServer(http.FS(subFS)))

	// 5. Start Server
	port := ":8080"
	log.Printf("Server starting on port %s...", port)
	if err := http.ListenAndServe(port, mux); err != nil {
		log.Fatalf("Server failed: %v", err)
	}
}

// --- Helper Functions ---

func getEnv(key, fallback string) string {
	if value, exists := os.LookupEnv(key); exists {
		return value
	}
	return fallback
}

func hashToken(token string) string {
	hasher := sha256.New()
	hasher.Write([]byte(token))
	return hex.EncodeToString(hasher.Sum(nil))
}

func securePath(storagePath string, virtualName string, relativePath string) (string, error) {
	cleanRelPath := filepath.Clean(relativePath)
	if strings.HasPrefix(cleanRelPath, "..") || strings.HasPrefix(cleanRelPath, "/") || strings.Contains(cleanRelPath, "../") {
		return "", fmt.Errorf("invalid relative path: directory traversal attempt")
	}

	baseDir := filepath.Join(storagePath, virtualName)
	fullPath := filepath.Join(baseDir, cleanRelPath)

	absBase, err := filepath.Abs(baseDir)
	if err != nil {
		return "", err
	}
	absFull, err := filepath.Abs(fullPath)
	if err != nil {
		return "", err
	}

	if !strings.HasPrefix(absFull, absBase) {
		return "", fmt.Errorf("invalid path: path escaped directory root")
	}

	return fullPath, nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()

	_, err = io.Copy(out, in)
	return err
}





// --- Middleware ---

func (app *App) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
			http.Error(w, "Unauthorized: missing bearer token", http.StatusUnauthorized)
			return
		}

		token := strings.TrimPrefix(authHeader, "Bearer ")
		tokenHash := hashToken(token)

		var deviceID int
		err := app.db.QueryRow(`
			SELECT device_id FROM api_tokens 
			WHERE token_hash = $1 AND created_at > $2`,
			tokenHash, time.Now().AddDate(-1, 0, 0), // Valid for 1 year
		).Scan(&deviceID)

		if err != nil {
			if err == sql.ErrNoRows {
				http.Error(w, "Unauthorized: invalid or expired token", http.StatusUnauthorized)
			} else {
				log.Printf("Token auth error: %v", err)
				http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			}
			return
		}

		r.Header.Set("X-Device-ID", strconv.Itoa(deviceID))
		next.ServeHTTP(w, r)
	})
}

func getDeviceID(r *http.Request) (int, error) {
	val := r.Header.Get("X-Device-ID")
	if val == "" {
		return 0, fmt.Errorf("device_id not found in request context")
	}
	id, err := strconv.Atoi(val)
	if err != nil {
		return 0, err
	}
	return id, nil
}

// --- Request Handlers ---

// POST /api/devices/register
func (app *App) handleRegisterDevice(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	var req RegisterDeviceRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}

	if req.DeviceName == "" || req.OSType == "" {
		http.Error(w, "device_name and os_type are required", http.StatusBadRequest)
		return
	}

	var deviceID int
	err := app.db.QueryRow(
		"INSERT INTO devices (device_name, os_type) VALUES ($1, $2) RETURNING device_id",
		req.DeviceName, req.OSType,
	).Scan(&deviceID)
	if err != nil {
		log.Printf("Failed to register device: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	// Pseudo-random hex key generation
	h := sha256.New()
	h.Write([]byte(fmt.Sprintf("%d-%s-%d-%d", deviceID, req.DeviceName, time.Now().UnixNano(), os.Getpid())))
	token := hex.EncodeToString(h.Sum(nil))
	tokenHash := hashToken(token)

	_, err = app.db.Exec(
		"INSERT INTO api_tokens (device_id, token_hash) VALUES ($1, $2)",
		deviceID, tokenHash,
	)
	if err != nil {
		log.Printf("Failed to save api token: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(RegisterDeviceResponse{
		DeviceID: deviceID,
		Token:    token,
	})
}

// POST /api/sync/folders
func (app *App) handleRegisterFolder(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	deviceID, err := getDeviceID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}

	var req RegisterFolderRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}

	if req.LocalPath == "" || req.VirtualName == "" {
		http.Error(w, "local_path and virtual_name are required", http.StatusBadRequest)
		return
	}

	var folderID int
	err = app.db.QueryRow(
		"SELECT folder_id FROM sync_folders WHERE device_id = $1 AND local_path = $2",
		deviceID, req.LocalPath,
	).Scan(&folderID)

	if err == nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(RegisterFolderResponse{FolderID: folderID})
		return
	} else if err != sql.ErrNoRows {
		log.Printf("Failed to query folder: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	err = app.db.QueryRow(
		"INSERT INTO sync_folders (device_id, local_path, virtual_name) VALUES ($1, $2, $3) RETURNING folder_id",
		deviceID, req.LocalPath, req.VirtualName,
	).Scan(&folderID)
	if err != nil {
		log.Printf("Failed to register folder: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(RegisterFolderResponse{FolderID: folderID})
}

// POST /api/sync/scan
func (app *App) handleScan(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	deviceID, err := getDeviceID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}

	var req ScanRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}

	var ownerDeviceID int
	err = app.db.QueryRow("SELECT device_id FROM sync_folders WHERE folder_id = $1", req.FolderID).Scan(&ownerDeviceID)
	if err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, "Folder not found", http.StatusNotFound)
		} else {
			log.Printf("Folder owner query error: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		}
		return
	}
	if ownerDeviceID != deviceID {
		http.Error(w, "Forbidden: you do not own this folder", http.StatusForbidden)
		return
	}

	var virtualName string
	err = app.db.QueryRow("SELECT virtual_name FROM sync_folders WHERE folder_id = $1", req.FolderID).Scan(&virtualName)
	if err != nil {
		http.Error(w, "Folder not found", http.StatusNotFound)
		return
	}

	// 1. Get PC-A's device_mapping (last known synced state)
	type deviceMeta struct {
		FileID    int
		Path      string
		Size      int64
		Hash      string
		Mtime     time.Time
		IsDir     bool
	}
	dbDeviceFiles := make(map[string]deviceMeta)
	
	rows, err := app.db.Query(`
		SELECT m.file_id, m.relative_path, d.last_synced_size, d.last_synced_hash, d.last_synced_mtime, m.is_directory
		FROM device_mapping d
		JOIN master_mapping m ON d.file_id = m.file_id
		WHERE d.device_id = $1 AND m.virtual_name = $2
	`, deviceID, virtualName)
	if err != nil {
		log.Printf("DB query device_mapping failed: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	for rows.Next() {
		var dm deviceMeta
		if err := rows.Scan(&dm.FileID, &dm.Path, &dm.Size, &dm.Hash, &dm.Mtime, &dm.IsDir); err == nil {
			dbDeviceFiles[dm.Path] = dm
		}
	}

	// 2. [Cycle Step 1 & 2] Merge Local Folder state -> PC Mapping -> Master Mapping (Metadata Merge)
	tx, err := app.db.Begin()
	if err != nil {
		log.Printf("Failed to begin transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer tx.Rollback()

	localProcessed := make(map[string]bool)

	for _, localFile := range req.Files {
		relPath := localFile.RelativePath
		localProcessed[relPath] = true

		devFile, deviceKnew := dbDeviceFiles[relPath]

		// This device's own local file hasn't changed since its own last known sync,
		// regardless of whether master has since moved on -- nothing to reconcile here;
		// that's an ordinary "needs to download the newer version" case handled by the
		// separate diff/task-generation step below, not a local update.
		if deviceKnew && localFile.FileHash == devFile.Hash &&
			localFile.LastModifiedAt.Unix() == devFile.Mtime.Unix() && localFile.FileSize == devFile.Size {
			continue
		}

		// Lock the master_mapping row for this path so a concurrent write (this device's
		// own direct upload, or another device's scan/upload) can't be decided from a
		// stale snapshot -- the same protection as handleUpload, applied here too.
		var fileID int
		var existingHash string
		var existingIsDeleted bool
		lookupErr := tx.QueryRow(`
			SELECT file_id, file_hash, is_deleted FROM master_mapping
			WHERE virtual_name = $1 AND relative_path = $2
			FOR UPDATE
		`, virtualName, relPath).Scan(&fileID, &existingHash, &existingIsDeleted)

		switch {
		case lookupErr == sql.ErrNoRows:
			// Genuinely new path, system-wide -> no possible conflict.
			err = tx.QueryRow(`
				INSERT INTO master_mapping (virtual_name, relative_path, file_name, is_directory, file_size, file_hash, last_modified_at, is_deleted)
				VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE)
				RETURNING file_id
			`, virtualName, relPath, localFile.FileName, localFile.IsDirectory, localFile.FileSize, localFile.FileHash, localFile.LastModifiedAt).Scan(&fileID)
			if err != nil {
				log.Printf("Failed to insert local addition to master_mapping: %v", err)
				http.Error(w, "Internal Server Error", http.StatusInternalServerError)
				return
			}
		case lookupErr != nil:
			log.Printf("Failed to lock master_mapping during scan: %v", lookupErr)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		case existingIsDeleted || existingHash == localFile.FileHash:
			// A deleted slot being reoccupied, or this device's content already matches
			// the current master -- neither case is a conflict, just apply normally.
			_, err = tx.Exec(`
				UPDATE master_mapping SET file_size=$1, file_hash=$2, last_modified_at=$3, is_deleted=FALSE, updated_at=CURRENT_TIMESTAMP
				WHERE file_id=$4
			`, localFile.FileSize, localFile.FileHash, localFile.LastModifiedAt, fileID)
			if err != nil {
				log.Printf("Failed to update master_mapping: %v", err)
				http.Error(w, "Internal Server Error", http.StatusInternalServerError)
				return
			}
		default:
			// Master already holds different, active content. Only an edit built on
			// that exact content is a legitimate sequential update; anything else (this
			// device never knew this file, or its last known state doesn't match) is a
			// genuine conflict.
			if deviceKnew && devFile.Hash == existingHash {
				err = tx.QueryRow(`
					UPDATE master_mapping SET file_size=$1, file_hash=$2, last_modified_at=$3, is_deleted=FALSE, updated_at=CURRENT_TIMESTAMP
					WHERE file_id=$4
					RETURNING file_id
				`, localFile.FileSize, localFile.FileHash, localFile.LastModifiedAt, fileID).Scan(&fileID)
				if err != nil {
					log.Printf("Failed to update master_mapping: %v", err)
					http.Error(w, "Internal Server Error", http.StatusInternalServerError)
					return
				}
				_, _ = tx.Exec("DELETE FROM device_mapping WHERE file_id = $1 AND device_id != $2", fileID, deviceID)
			} else {
				deviceName := "unknown"
				_ = tx.QueryRow("SELECT device_name FROM devices WHERE device_id = $1", deviceID).Scan(&deviceName)

				alreadyForked, ferr := app.findExistingConflictFork(tx, virtualName, relPath, deviceName, localFile.FileHash)
				if ferr != nil {
					log.Printf("Failed to check for existing conflict fork: %v", ferr)
					http.Error(w, "Internal Server Error", http.StatusInternalServerError)
					return
				}
				if alreadyForked {
					// Already recorded by an earlier scan hitting this same
					// still-unresolved conflict -> nothing new to do this time.
					continue
				}

				newFileID, newRelPath, ferr := app.registerAsNewFile(tx, virtualName, relPath, deviceName, localFile.FileHash, localFile.FileSize, localFile.LastModifiedAt, localFile.IsDirectory)
				if ferr != nil {
					log.Printf("Failed to register conflicting local file as new file: %v", ferr)
					http.Error(w, "Internal Server Error", http.StatusInternalServerError)
					return
				}
				fileID = newFileID
				_, _ = tx.Exec(`
					INSERT INTO activity_logs (device_id, action_type, file_path, description)
					VALUES ($1, 'CONFLICT', $2, $3)`,
					deviceID, relPath, fmt.Sprintf("Local file %s conflicted with the current version; registered separately as %s", relPath, newRelPath),
				)
			}
		}

		// Reflect this device's local state to device_mapping (whichever fileID applies).
		_, err = tx.Exec(`
			INSERT INTO device_mapping (device_id, file_id, last_synced_mtime, last_synced_size, last_synced_hash)
			VALUES ($1, $2, $3, $4, $5)
			ON CONFLICT (device_id, file_id)
			DO UPDATE SET last_synced_mtime = EXCLUDED.last_synced_mtime, last_synced_size = EXCLUDED.last_synced_size, last_synced_hash = EXCLUDED.last_synced_hash
		`, deviceID, fileID, localFile.LastModifiedAt, localFile.FileSize, localFile.FileHash)
		if err != nil {
			log.Printf("Failed to update device_mapping: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
	}

	// Local Deletion -> Reflect to PC Mapping (remove) and Master (mark deleted)
	for relPath, devFile := range dbDeviceFiles {
		if !localProcessed[relPath] {
			_, err = tx.Exec(`
				UPDATE master_mapping
				SET is_deleted = TRUE, last_modified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
				WHERE file_id = $1
			`, devFile.FileID)
			if err != nil {
				log.Printf("Failed to delete in master_mapping: %v", err)
				http.Error(w, "Internal Server Error", http.StatusInternalServerError)
				return
			}

			// Remove from PC Mapping
			_, _ = tx.Exec("DELETE FROM device_mapping WHERE file_id = $1 AND device_id = $2", devFile.FileID, deviceID)
			// Invalidate other devices
			_, _ = tx.Exec("DELETE FROM device_mapping WHERE file_id = $1 AND device_id != $2", devFile.FileID, deviceID)

			// This path no longer exists anywhere master-side, so any queued
			// download/upload/move for it (on this device or any sibling device
			// sharing this virtual folder) is now stale -- drop it immediately
			// rather than waiting for some future scan to notice.
			_, _ = tx.Exec(`
				DELETE FROM sync_tasks
				WHERE action_type IN ('download_new', 'download_overwrite', 'upload_new', 'upload_overwrite', 'move')
				  AND (relative_path = $1 OR to_path = $1)
				  AND folder_id IN (SELECT folder_id FROM sync_folders WHERE virtual_name = $2)
			`, relPath, virtualName)
		}
	}

	if err := tx.Commit(); err != nil {
		log.Printf("Failed to commit merge transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	// 3. [Cycle Step 3] Compute Diffs: master vs nas_mapping vs device_mapping

	// 3a. Load master_mapping
	type masterMeta struct {
		FileID         int
		Path           string
		IsDir          bool
		Size           int64
		Hash           string
		Mtime          time.Time
		IsDeleted      bool
		LastModifiedBy string
	}
	var masterFiles []masterMeta

	rowsMaster, err := app.db.Query(`
		SELECT m.file_id, m.relative_path, m.is_directory, m.file_size, m.file_hash, m.last_modified_at, m.is_deleted, COALESCE(d.device_name, '')
		FROM master_mapping m
		LEFT JOIN devices d ON m.last_modified_by_device_id = d.device_id
		WHERE m.virtual_name = $1
	`, virtualName)
	if err != nil {
		log.Printf("DB query master_mapping failed: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rowsMaster.Close()

	for rowsMaster.Next() {
		var mm masterMeta
		if err := rowsMaster.Scan(&mm.FileID, &mm.Path, &mm.IsDir, &mm.Size, &mm.Hash, &mm.Mtime, &mm.IsDeleted, &mm.LastModifiedBy); err == nil {
			masterFiles = append(masterFiles, mm)
		}
	}

	// 3b. Load nas_mapping (what NAS physically has)
	type nasMeta struct {
		Size  int64
		Hash  string
		Mtime time.Time
	}
	nasMap := make(map[string]nasMeta)

	rowsNas, _ := app.db.Query(`
		SELECT m.relative_path, n.file_size, n.file_hash, n.last_modified_at
		FROM nas_mapping n
		JOIN master_mapping m ON n.file_id = m.file_id
		WHERE m.virtual_name = $1
	`, virtualName)
	for rowsNas.Next() {
		var path string
		var nm nasMeta
		if rowsNas.Scan(&path, &nm.Size, &nm.Hash, &nm.Mtime) == nil {
			nasMap[path] = nm
		}
	}
	rowsNas.Close()

	// 3c. Refresh device_mapping cache to reflect step-2 changes
	dbDeviceFiles = make(map[string]deviceMeta)
	rowsRefresh, _ := app.db.Query(`
		SELECT m.file_id, m.relative_path, d.last_synced_size, d.last_synced_hash, d.last_synced_mtime, m.is_directory
		FROM device_mapping d
		JOIN master_mapping m ON d.file_id = m.file_id
		WHERE d.device_id = $1 AND m.virtual_name = $2
	`, deviceID, virtualName)
	for rowsRefresh.Next() {
		var dm deviceMeta
		if rowsRefresh.Scan(&dm.FileID, &dm.Path, &dm.Size, &dm.Hash, &dm.Mtime, &dm.IsDir) == nil {
			dbDeviceFiles[dm.Path] = dm
		}
	}
	rowsRefresh.Close()

	// 3d. Detect tasks
	type rawTask struct {
		RelativePath   string
		ToPath         string
		ActionType     string
		FileSize       int64
		FileHash       string
		LastModifiedBy string
	}
	detectedTasks := make([]rawTask, 0)

	type moveCandidate struct {
		FileID   int
		FromPath string
		Hash     string
	}
	var moveFromCandidates []moveCandidate
	var moveToCandidates []masterMeta

	for _, mast := range masterFiles {
		devFile, devExists := dbDeviceFiles[mast.Path]
		nasFile, nasExists := nasMap[mast.Path]

		if mast.IsDeleted {
			// File deleted: device still has it locally -> need to delete local copy
			if devExists {
				moveFromCandidates = append(moveFromCandidates, moveCandidate{
					FileID:   mast.FileID,
					FromPath: mast.Path,
					Hash:     devFile.Hash,
				})
			}
			continue
		}

		if mast.IsDir {
			// Directories have no physical NAS payload, only master_mapping records them.
			// If device doesn't have the dir yet, generate download (mkdir) task.
			if !devExists {
				moveToCandidates = append(moveToCandidates, mast)
			}
			continue
		}

		switch {
		case devExists && nasExists:
			// Compare by content hash; mtime can diverge across platforms without content change
			if devFile.Hash != nasFile.Hash {
				if nasFile.Mtime.After(devFile.Mtime) {
					// NAS has newer version -> download to device
					detectedTasks = append(detectedTasks, rawTask{
						RelativePath:   mast.Path,
						ActionType:     "download_overwrite",
						FileSize:       nasFile.Size,
						FileHash:       nasFile.Hash,
						LastModifiedBy: mast.LastModifiedBy,
					})
				} else {
					// Device has newer version -> upload to NAS
					detectedTasks = append(detectedTasks, rawTask{
						RelativePath:   mast.Path,
						ActionType:     "upload_overwrite",
						FileSize:       mast.Size,
						FileHash:       mast.Hash,
						LastModifiedBy: mast.LastModifiedBy,
					})
				}
			}

		case devExists && !nasExists:
			// Device has it, NAS doesn't -> upload
			detectedTasks = append(detectedTasks, rawTask{
				RelativePath:   mast.Path,
				ActionType:     "upload_new",
				FileSize:       mast.Size,
				FileHash:       mast.Hash,
				LastModifiedBy: mast.LastModifiedBy,
			})

		case !devExists && nasExists:
			// NAS has it, device doesn't -> download (candidate for move detection)
			moveToCandidates = append(moveToCandidates, mast)

		default:
			// Neither device nor NAS has it (orphan master entry) -> nothing
		}
	}

	// Rename matching
	matchedFrom := make(map[string]bool)
	matchedTo := make(map[string]bool)

	for _, fromCand := range moveFromCandidates {
		for _, toCand := range moveToCandidates {
			if matchedTo[toCand.Path] {
				continue
			}
			if fromCand.Hash == toCand.Hash && fromCand.Hash != "" {
				detectedTasks = append(detectedTasks, rawTask{
					RelativePath:   fromCand.FromPath,
					ToPath:         toCand.Path,
					ActionType:     "move",
					FileSize:       toCand.Size,
					FileHash:       toCand.Hash,
					LastModifiedBy: toCand.LastModifiedBy,
				})
				matchedFrom[fromCand.FromPath] = true
				matchedTo[toCand.Path] = true
				break
			}
		}
	}

	// Finalize remaining unmatched candidates
	for _, fromCand := range moveFromCandidates {
		if !matchedFrom[fromCand.FromPath] {
			detectedTasks = append(detectedTasks, rawTask{
				RelativePath: fromCand.FromPath,
				ActionType:   "delete_local",
			})
		}
	}

	for _, toCand := range moveToCandidates {
		if !matchedTo[toCand.Path] {
			detectedTasks = append(detectedTasks, rawTask{
				RelativePath:   toCand.Path,
				ActionType:     "download_new",
				FileSize:       toCand.Size,
				FileHash:       toCand.Hash,
				LastModifiedBy: toCand.LastModifiedBy,
			})
		}
	}

	// 4. [Real-time Tasks Sync] Upsert detected tasks to DB, prune obsolete pending tasks
	txTasks, err := app.db.Begin()
	if err != nil {
		log.Printf("Failed to start tasks transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer txTasks.Rollback()

	// A. Upsert detected tasks (Only insert if not exists. Do not touch processing tasks)
	for _, t := range detectedTasks {
		var existingStatus string
		err = txTasks.QueryRow(`
			SELECT status FROM sync_tasks
			WHERE device_id = $1 AND relative_path = $2 AND action_type = $3 AND folder_id = $4
		`, deviceID, t.RelativePath, t.ActionType, req.FolderID).Scan(&existingStatus)

		if err == sql.ErrNoRows {
			// Insert new pending task
			_, err = txTasks.Exec(`
				INSERT INTO sync_tasks (device_id, folder_id, relative_path, to_path, action_type, status, file_size, file_hash, last_modified_by)
				VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8)
			`, deviceID, req.FolderID, t.RelativePath, t.ToPath, t.ActionType, t.FileSize, t.FileHash, t.LastModifiedBy)
			if err != nil {
				log.Printf("Failed to insert task to DB: %v", err)
			}
		} else if err != nil {
			log.Printf("Failed to query existing task: %v", err)
		}
	}

	// B. Clean up obsolete pending tasks (Tasks that are no longer detected and are STILL 'pending')
	type taskKey struct {
		path string
		act  string
	}
	detectedSet := make(map[taskKey]bool)
	for _, t := range detectedTasks {
		detectedSet[taskKey{path: t.RelativePath, act: t.ActionType}] = true
	}

	rowsTasks, err := txTasks.Query("SELECT task_id, relative_path, action_type, status FROM sync_tasks WHERE device_id = $1 AND folder_id = $2", deviceID, req.FolderID)
	if err == nil {
		type obsoleteTask struct {
			taskID int
			status string
		}
		var obsoleteTasks []obsoleteTask
		for rowsTasks.Next() {
			var tid int
			var rpath, act, stat string
			if rowsTasks.Scan(&tid, &rpath, &act, &stat) == nil {
				if !detectedSet[taskKey{path: rpath, act: act}] && stat == "pending" {
					obsoleteTasks = append(obsoleteTasks, obsoleteTask{taskID: tid, status: stat})
				}
			}
		}
		rowsTasks.Close()

		for _, ot := range obsoleteTasks {
			_, _ = txTasks.Exec("DELETE FROM sync_tasks WHERE task_id = $1", ot.taskID)
		}
	}

	if err := txTasks.Commit(); err != nil {
		log.Printf("Failed to commit tasks transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	// 5. Load active tasks for this device from DB and return to client (max 200 per cycle)
	finalTasks := make([]GoSyncTask, 0)
	rowsFinal, err := app.db.Query(`
		SELECT task_id, relative_path, COALESCE(to_path, ''), action_type, file_size, file_hash, COALESCE(last_modified_by, '')
		FROM sync_tasks
		WHERE device_id = $1 AND folder_id = $2 AND status != 'completed'
		ORDER BY task_id ASC
		LIMIT 200
	`, deviceID, req.FolderID)
	if err != nil {
		log.Printf("Failed to query final tasks: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rowsFinal.Close()

	for rowsFinal.Next() {
		var gt GoSyncTask
		if err := rowsFinal.Scan(&gt.TaskID, &gt.RelativePath, &gt.ToPath, &gt.ActionType, &gt.FileSize, &gt.FileHash, &gt.LastModifiedBy); err == nil {
			finalTasks = append(finalTasks, gt)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ScanResponse{
		Tasks: finalTasks,
	})
}



// POST /api/sync/upload
func (app *App) handleUpload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	deviceID, err := getDeviceID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}

	err = r.ParseMultipartForm(50 << 20) // 50MB
	if err != nil {
		log.Printf("Parse multipart form error: %v", err)
		http.Error(w, "Bad Request: upload too large or malformed", http.StatusBadRequest)
		return
	}

	folderIDStr := r.FormValue("folder_id")
	relativePath := r.FormValue("relative_path")
	fileHash := r.FormValue("file_hash")
	fileSizeStr := r.FormValue("file_size")
	lastModifiedStr := r.FormValue("last_modified_at")

	if folderIDStr == "" || relativePath == "" || fileHash == "" || fileSizeStr == "" || lastModifiedStr == "" {
		http.Error(w, "Missing required fields", http.StatusBadRequest)
		return
	}

	folderID, err := strconv.Atoi(folderIDStr)
	if err != nil {
		http.Error(w, "Invalid folder_id", http.StatusBadRequest)
		return
	}

	fileSize, err := strconv.ParseInt(fileSizeStr, 10, 64)
	if err != nil {
		http.Error(w, "Invalid file_size", http.StatusBadRequest)
		return
	}

	lastModifiedAt, err := time.Parse(time.RFC3339, lastModifiedStr)
	if err != nil {
		lastModifiedAt, err = time.Parse("2006-01-02T15:04:05Z07:00", lastModifiedStr)
		if err != nil {
			http.Error(w, "Invalid last_modified_at (must be RFC3339)", http.StatusBadRequest)
			return
		}
	}

	var ownerDeviceID int
	var virtualName string
	err = app.db.QueryRow("SELECT device_id, virtual_name FROM sync_folders WHERE folder_id = $1", folderID).Scan(&ownerDeviceID, &virtualName)
	if err != nil {
		http.Error(w, "Folder not found", http.StatusNotFound)
		return
	}
	if ownerDeviceID != deviceID {
		http.Error(w, "Forbidden", http.StatusForbidden)
		return
	}

	targetFilePath, err := securePath(app.storagePath, virtualName, relativePath)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	file, _, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "Missing file data", http.StatusBadRequest)
		return
	}
	defer file.Close()

	if err := os.MkdirAll(filepath.Dir(targetFilePath), 0755); err != nil {
		log.Printf("Failed to create parent directory: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	isUpdate := false
	if _, err := os.Stat(targetFilePath); err == nil {
		isUpdate = true
	}

	// Uploaded bytes are staged to a temp path first. Where they end up on disk
	// (original path vs. a forked path) is decided below, inside the locked
	// transaction, once we know whether this upload wins or loses a genuine conflict.
	tempFilePath := targetFilePath + ".uploading.tmp"

	dedupSuccess := false
	if fileHash != "" {
		var srcRelPath string
		var srcVirtualName string
		err = app.db.QueryRow("SELECT relative_path, virtual_name FROM master_mapping WHERE file_hash = $1 AND is_deleted = FALSE LIMIT 1", fileHash).Scan(&srcRelPath, &srcVirtualName)
		if err == nil {
			srcFilePath, err := securePath(app.storagePath, srcVirtualName, srcRelPath)
			if err == nil {
				if _, err := os.Stat(srcFilePath); err == nil {
					if err := os.MkdirAll(filepath.Dir(tempFilePath), 0755); err == nil {
						// Try hard link first (instant, 0-space)
						if err := os.Link(srcFilePath, tempFilePath); err == nil {
							log.Printf("[DEDUP] Instantly hard-linked %s from existing %s (hash: %s)", relativePath, srcRelPath, fileHash)
							dedupSuccess = true
						} else {
							// Fallback to copy if cross-device or not supported
							if err := copyFile(srcFilePath, tempFilePath); err == nil {
								log.Printf("[DEDUP] Instantly copied %s from existing %s (hash: %s)", relativePath, srcRelPath, fileHash)
								dedupSuccess = true
							}
						}
					}
				}
			}
		}
	}

	if !dedupSuccess {
		out, err := os.Create(tempFilePath)
		if err != nil {
			log.Printf("Failed to create file on disk: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}

		_, err = io.Copy(out, file)
		out.Close()
		if err != nil {
			log.Printf("Failed to save file contents: %v", err)
			os.Remove(tempFilePath)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
	}

	fileName := filepath.Base(relativePath)

	// 3-Tier mapping database updates (wrapped in a transaction)
	tx, err := app.db.Begin()
	if err != nil {
		log.Printf("Failed to begin transaction: %v", err)
		os.Remove(tempFilePath)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer tx.Rollback()

	// Lock the existing row (if any) for this path so a concurrent upload of the
	// same file from another device can't be decided from a stale snapshot.
	var fileID int
	var existingHash string
	lookupErr := tx.QueryRow(`
		SELECT file_id, file_hash
		FROM master_mapping
		WHERE virtual_name = $1 AND relative_path = $2
		FOR UPDATE
	`, virtualName, relativePath).Scan(&fileID, &existingHash)

	outcome := "normal" // normal | no_change | conflict
	newRelPath := ""    // only set when outcome == "conflict"

	switch {
	case lookupErr == sql.ErrNoRows:
		// First-ever upload of this path -> no possible conflict.
		err = tx.QueryRow(`
			INSERT INTO master_mapping (virtual_name, relative_path, file_name, is_directory, file_size, file_hash, last_modified_at, is_deleted, last_modified_by_device_id)
			VALUES ($1, $2, $3, FALSE, $4, $5, $6, FALSE, $7)
			RETURNING file_id
		`, virtualName, relativePath, fileName, fileSize, fileHash, lastModifiedAt, deviceID).Scan(&fileID)
		if err != nil {
			log.Printf("Failed to insert master_mapping on upload: %v", err)
			os.Remove(tempFilePath)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
	case lookupErr != nil:
		log.Printf("Failed to lock master_mapping on upload: %v", lookupErr)
		os.Remove(tempFilePath)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	case existingHash == fileHash:
		// Identical content re-uploaded (e.g. a retry) -> nothing to change.
		outcome = "no_change"
	default:
		var deviceKnownHash string
		_ = tx.QueryRow(`SELECT last_synced_hash FROM device_mapping WHERE device_id = $1 AND file_id = $2`, deviceID, fileID).Scan(&deviceKnownHash)

		if deviceKnownHash == existingHash {
			// This device's edit is a direct descendant of the current master -> normal update.
			_, err = tx.Exec(`
				UPDATE master_mapping SET file_size=$1, file_hash=$2, last_modified_at=$3, is_deleted=FALSE, last_modified_by_device_id=$4, updated_at=CURRENT_TIMESTAMP
				WHERE file_id=$5
			`, fileSize, fileHash, lastModifiedAt, deviceID, fileID)
			if err != nil {
				log.Printf("Failed to update master_mapping on upload: %v", err)
				os.Remove(tempFilePath)
				http.Error(w, "Internal Server Error", http.StatusInternalServerError)
				return
			}
		} else {
			// Genuine conflict: this device's edit does not build on the current master
			// state (its last known base doesn't match what's there now). The existing
			// master content is left completely untouched -- this device's own content
			// is simply registered as its own new, independently-named file instead.
			// No winner/loser decision, no physical file is ever moved or deleted.
			deviceName := "unknown"
			_ = tx.QueryRow("SELECT device_name FROM devices WHERE device_id = $1", deviceID).Scan(&deviceName)

			alreadyForked, ferr := app.findExistingConflictFork(tx, virtualName, relativePath, deviceName, fileHash)
			if ferr != nil {
				log.Printf("Failed to check for existing conflict fork: %v", ferr)
				os.Remove(tempFilePath)
				http.Error(w, "Internal Server Error", http.StatusInternalServerError)
				return
			}
			if alreadyForked {
				// This exact content was already saved separately by an earlier attempt
				// (e.g. a previous scan hitting the same still-unresolved conflict) ->
				// nothing new to do this time.
				outcome = "no_change"
			} else {
				outcome = "conflict"
				newFileID, genRelPath, ferr := app.registerAsNewFile(tx, virtualName, relativePath, deviceName, fileHash, fileSize, lastModifiedAt, false)
				if ferr != nil {
					log.Printf("Failed to register conflicting upload as new file: %v", ferr)
					os.Remove(tempFilePath)
					http.Error(w, "Internal Server Error", http.StatusInternalServerError)
					return
				}
				fileID = newFileID
				newRelPath = genRelPath
				_, _ = tx.Exec(`
					INSERT INTO activity_logs (device_id, action_type, file_path, description)
					VALUES ($1, 'CONFLICT', $2, $3)`,
					deviceID, relativePath, fmt.Sprintf("Upload of %s conflicted with the current version; saved separately as %s", relativePath, genRelPath),
				)
			}
		}
	}

	// B/C/D only apply when master_mapping's content for THIS path actually changed.
	if outcome == "normal" || outcome == "conflict" {
		// B. Record that NAS physically has this file
		_, err = tx.Exec(`
			INSERT INTO nas_mapping (file_id, file_size, file_hash, last_modified_at)
			VALUES ($1, $2, $3, $4)
			ON CONFLICT (file_id)
			DO UPDATE SET file_size = EXCLUDED.file_size, file_hash = EXCLUDED.file_hash, last_modified_at = EXCLUDED.last_modified_at
		`, fileID, fileSize, fileHash, lastModifiedAt)
		if err != nil {
			log.Printf("Failed to update nas_mapping on upload: %v", err)
			os.Remove(tempFilePath)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}

		// C. Update device_mapping for this device (acknowledging successful upload)
		_, err = tx.Exec(`
			INSERT INTO device_mapping (device_id, file_id, last_synced_mtime, last_synced_size, last_synced_hash)
			VALUES ($1, $2, $3, $4, $5)
			ON CONFLICT (device_id, file_id)
			DO UPDATE SET last_synced_mtime = EXCLUDED.last_synced_mtime, last_synced_size = EXCLUDED.last_synced_size, last_synced_hash = EXCLUDED.last_synced_hash
		`, deviceID, fileID, lastModifiedAt, fileSize, fileHash)
		if err != nil {
			log.Printf("Failed to update device_mapping on upload: %v", err)
			os.Remove(tempFilePath)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}

		// D. Delete device_mapping for ALL OTHER devices (they need to download the new version)
		_, _ = tx.Exec("DELETE FROM device_mapping WHERE file_id = $1 AND device_id != $2", fileID, deviceID)
	}

	// E. Delete corresponding upload tasks from sync_tasks (handled either way: normal
	// update, or registered separately as a conflict).
	_, err = tx.Exec(`
		DELETE FROM sync_tasks
		WHERE device_id = $1 AND relative_path = $2 AND action_type IN ('upload_new', 'upload_overwrite')
	`, deviceID, relativePath)
	if err != nil {
		log.Printf("Failed to delete upload task from sync_tasks: %v", err)
	}

	// F. This device just asserted its own state for this path, so any download task
	// still queued for it here is now stale (based on a snapshot from before this
	// upload) -> drop it.
	_, err = tx.Exec(`
		DELETE FROM sync_tasks
		WHERE device_id = $1 AND relative_path = $2 AND action_type IN ('download_new', 'download_overwrite')
	`, deviceID, relativePath)
	if err != nil {
		log.Printf("Failed to delete stale download task from sync_tasks: %v", err)
	}

	if err := tx.Commit(); err != nil {
		log.Printf("Failed to commit upload transaction: %v", err)
		os.Remove(tempFilePath)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	// Finalize physical placement of the uploaded bytes now that the outcome is committed.
	switch outcome {
	case "normal":
		if err := os.Rename(tempFilePath, targetFilePath); err != nil {
			if err := copyFile(tempFilePath, targetFilePath); err != nil {
				log.Printf("Failed to place uploaded file at %s: %v", targetFilePath, err)
			} else {
				os.Remove(tempFilePath)
			}
		}
	case "conflict":
		conflictFilePath, perr := securePath(app.storagePath, virtualName, newRelPath)
		if perr != nil {
			log.Printf("Failed to resolve conflict file path: %v", perr)
			os.Remove(tempFilePath)
		} else {
			if err := os.MkdirAll(filepath.Dir(conflictFilePath), 0755); err != nil {
				log.Printf("Failed to create parent directory for conflict file: %v", err)
			}
			if err := os.Rename(tempFilePath, conflictFilePath); err != nil {
				if err := copyFile(tempFilePath, conflictFilePath); err != nil {
					log.Printf("Failed to place conflicting file at %s: %v", conflictFilePath, err)
				} else {
					os.Remove(tempFilePath)
				}
			}
		}
	case "no_change":
		os.Remove(tempFilePath)
	}

	actionType := "CREATE"
	if isUpdate {
		actionType = "UPDATE"
	}
	_, _ = app.db.Exec(`
		INSERT INTO activity_logs (device_id, action_type, file_path, description)
		VALUES ($1, $2, $3, $4)`,
		deviceID, actionType, relativePath, fmt.Sprintf("File %s at %s", strings.ToLower(actionType), relativePath),
	)

	w.WriteHeader(http.StatusOK)
	w.Write([]byte("Upload successful"))
}

// findExistingConflictFork checks whether this device's exact content has already been
// registered as a conflict-fork of originalRelPath. Both handleUpload and handleScan can
// detect the very same unresolved conflict repeatedly (e.g. every periodic scan, since
// clients never rename anything locally) -- without this check, each detection would
// otherwise create yet another fork file, reproducing the same runaway growth this whole
// redesign exists to prevent.
func (app *App) findExistingConflictFork(tx *sql.Tx, virtualName, originalRelPath, deviceName, hash string) (bool, error) {
	ext := filepath.Ext(originalRelPath)
	stem := strings.TrimSuffix(originalRelPath, ext)
	pattern := stem + " (from " + deviceName + "%" + ext
	var count int
	err := tx.QueryRow(`
		SELECT count(*) FROM master_mapping
		WHERE virtual_name = $1 AND relative_path LIKE $2 AND file_hash = $3
	`, virtualName, pattern, hash).Scan(&count)
	if err != nil {
		return false, err
	}
	return count > 0, nil
}

// registerAsNewFile records content that genuinely conflicted with the current state of
// originalRelPath as its own new, independently-named file (never overwriting or moving
// the existing entry, never touching any device's local files). It only writes the
// master_mapping row; the caller is responsible for nas_mapping/device_mapping and for
// placing any physical bytes, since those differ between the upload and scan call sites.
func (app *App) registerAsNewFile(tx *sql.Tx, virtualName, originalRelPath string, deviceName string, hash string, size int64, mtime time.Time, isDir bool) (int, string, error) {
	ext := filepath.Ext(originalRelPath)
	stem := strings.TrimSuffix(originalRelPath, ext)

	newRelPath := ""
	for n := 0; ; n++ {
		suffix := fmt.Sprintf(" (from %s)", deviceName)
		if n > 0 {
			suffix = fmt.Sprintf(" (from %s) (%d)", deviceName, n)
		}
		candidate := stem + suffix + ext
		var exists int
		_ = tx.QueryRow("SELECT 1 FROM master_mapping WHERE virtual_name = $1 AND relative_path = $2", virtualName, candidate).Scan(&exists)
		if exists == 0 {
			newRelPath = candidate
			break
		}
	}

	var newFileID int
	err := tx.QueryRow(`
		INSERT INTO master_mapping (virtual_name, relative_path, file_name, is_directory, file_size, file_hash, last_modified_at, is_deleted)
		VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE)
		RETURNING file_id
	`, virtualName, newRelPath, filepath.Base(newRelPath), isDir, size, hash, mtime).Scan(&newFileID)
	if err != nil {
		return 0, "", fmt.Errorf("insert conflicting content as new file: %w", err)
	}

	return newFileID, newRelPath, nil
}

// GET /api/sync/download
func (app *App) handleDownload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	// For browser compatibility, we support both Auth-header and Query Token
	// Since download is triggered by direct HTML anchor links in Web UI, they cannot easily attach headers.
	// We read Authorization header, or fallback to the Tailscale connection origin.
	// In closed network, we allow direct download if folder ID ownership is bypassable (since user is on Tailscale).
	// To be secure yet usable:
	var deviceID int
	authHeader := r.Header.Get("Authorization")
	if authHeader != "" && strings.HasPrefix(authHeader, "Bearer ") {
		token := strings.TrimPrefix(authHeader, "Bearer ")
		tokenHash := hashToken(token)
		_ = app.db.QueryRow("SELECT device_id FROM api_tokens WHERE token_hash = $1", tokenHash).Scan(&deviceID)
	}

	folderIDStr := r.URL.Query().Get("folder_id")
	relativePath := r.URL.Query().Get("relative_path")

	if folderIDStr == "" || relativePath == "" {
		http.Error(w, "folder_id and relative_path query parameters are required", http.StatusBadRequest)
		return
	}

	folderID, err := strconv.Atoi(folderIDStr)
	if err != nil {
		http.Error(w, "Invalid folder_id", http.StatusBadRequest)
		return
	}

	var virtualName string
	err = app.db.QueryRow("SELECT virtual_name FROM sync_folders WHERE folder_id = $1", folderID).Scan(&virtualName)
	if err != nil {
		http.Error(w, "Folder not found", http.StatusNotFound)
		return
	}

	// Validate path security
	targetFilePath, err := securePath(app.storagePath, virtualName, relativePath)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}




	var isDeleted bool
	var isDirectory bool
	var fileID int
	var fileSize int64
	var fileHash string
	var mtime time.Time

	err = app.db.QueryRow(`
		SELECT file_id, file_size, file_hash, last_modified_at, is_deleted, is_directory 
		FROM master_mapping 
		WHERE virtual_name = $1 AND relative_path = $2
	`, virtualName, relativePath).Scan(&fileID, &fileSize, &fileHash, &mtime, &isDeleted, &isDirectory)

	if err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, "File not found in database", http.StatusNotFound)
		} else {
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		}
		return
	}

	if isDeleted {
		http.Error(w, "File is logically deleted", http.StatusNotFound)
		return
	}

	// Serve the file and record sync status (device_mapping) for this device
	if deviceID != 0 {
		tx, err := app.db.Begin()
		if err == nil {
			defer tx.Rollback()
			
			_, err = tx.Exec(`
				INSERT INTO device_mapping (device_id, file_id, last_synced_mtime, last_synced_size, last_synced_hash)
				VALUES ($1, $2, $3, $4, $5)
				ON CONFLICT (device_id, file_id)
				DO UPDATE SET last_synced_mtime = EXCLUDED.last_synced_mtime, last_synced_size = EXCLUDED.last_synced_size, last_synced_hash = EXCLUDED.last_synced_hash
			`, deviceID, fileID, mtime, fileSize, fileHash)
			if err != nil {
				log.Printf("Failed to record device_mapping in handleDownload: %v", err)
			}

			// Delete corresponding download tasks from sync_tasks
			_, err = tx.Exec(`
				DELETE FROM sync_tasks 
				WHERE device_id = $1 AND relative_path = $2 AND action_type IN ('download_new', 'download_overwrite')
			`, deviceID, relativePath)
			if err != nil {
				log.Printf("Failed to delete download task from sync_tasks: %v", err)
			}

			_ = tx.Commit()
		}
	}

	if isDirectory {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("Directory synced"))
		return
	}

	http.ServeFile(w, r, targetFilePath)
}


// DELETE /api/sync/delete
func (app *App) handleDelete(w http.ResponseWriter, r *http.Request) {
	// Web UI supports DELETE method, we allow it without token in closed system, but check query
	if r.Method != http.MethodDelete {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	var req DeleteRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}

	var deviceID int
	authHeader := r.Header.Get("Authorization")
	if authHeader != "" && strings.HasPrefix(authHeader, "Bearer ") {
		token := strings.TrimPrefix(authHeader, "Bearer ")
		tokenHash := hashToken(token)
		_ = app.db.QueryRow("SELECT device_id FROM api_tokens WHERE token_hash = $1", tokenHash).Scan(&deviceID)
	}

	if req.FolderID == 0 || req.RelativePath == "" {
		http.Error(w, "folder_id and relative_path are required", http.StatusBadRequest)
		return
	}

	var virtualName string
	err := app.db.QueryRow("SELECT virtual_name FROM sync_folders WHERE folder_id = $1", req.FolderID).Scan(&virtualName)
	if err != nil {
		http.Error(w, "Folder not found", http.StatusNotFound)
		return
	}

	// Get all sibling folders with the same virtualName
	// 2-Tier mapping database updates (wrapped in a transaction)
	tx, err := app.db.Begin()
	if err != nil {
		log.Printf("Failed to begin transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer tx.Rollback()

	var fileID int
	err = tx.QueryRow(`
		UPDATE master_mapping
		SET is_deleted = TRUE, last_modified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
		WHERE virtual_name = $1 AND relative_path = $2
		RETURNING file_id
	`, virtualName, req.RelativePath).Scan(&fileID)

	if err == nil {
		// Remove from nas_mapping (NAS no longer physically has it)
		_, _ = tx.Exec("DELETE FROM nas_mapping WHERE file_id = $1", fileID)

		if deviceID != 0 {
			// Device deleted local file -> Remove from device_mapping (synced)
			_, _ = tx.Exec("DELETE FROM device_mapping WHERE file_id = $1 AND device_id = $2", fileID, deviceID)

			// Delete corresponding delete_local task
			_, _ = tx.Exec(`
				DELETE FROM sync_tasks
				WHERE device_id = $1 AND relative_path = $2 AND action_type = 'delete_local'
			`, deviceID, req.RelativePath)
		} else {
			// Web client logical delete -> Remove sync status for all devices to trigger sync
			_, _ = tx.Exec("DELETE FROM device_mapping WHERE file_id = $1", fileID)
		}

		// This path no longer exists anywhere master-side, so any queued
		// download/upload/move for it (on this device or any sibling device
		// sharing this virtual folder) is now stale -- drop it immediately
		// rather than waiting for some future scan to notice.
		_, _ = tx.Exec(`
			DELETE FROM sync_tasks
			WHERE action_type IN ('download_new', 'download_overwrite', 'upload_new', 'upload_overwrite', 'move')
			  AND (relative_path = $1 OR to_path = $1)
			  AND folder_id IN (SELECT folder_id FROM sync_folders WHERE virtual_name = $2)
		`, req.RelativePath, virtualName)
	} else if err == sql.ErrNoRows {
		if deviceID != 0 {
			_, _ = tx.Exec(`
				DELETE FROM sync_tasks 
				WHERE device_id = $1 AND relative_path = $2 AND action_type = 'delete_local'
			`, deviceID, req.RelativePath)
		}
		http.Error(w, "File not found or already deleted", http.StatusNotFound)
		return
	} else {
		log.Printf("Failed to update master_mapping on handleDelete: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	if err := tx.Commit(); err != nil {
		log.Printf("Failed to commit delete transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	// Physically remove the file from NAS disk to free up space (or un-link hard link)
	targetFilePath, err := securePath(app.storagePath, virtualName, req.RelativePath)
	if err == nil {
		if err := os.Remove(targetFilePath); err != nil && !os.IsNotExist(err) {
			log.Printf("Failed to physically remove file %s: %v", targetFilePath, err)
		} else {
			log.Printf("[DELETE] Physically removed file from NAS: %s", targetFilePath)
		}
	}

	_, _ = app.db.Exec(`
		INSERT INTO activity_logs (device_id, action_type, file_path, description)
		VALUES (NULL, 'DELETE', $1, $2)`, // NULL device means web interface action
		req.RelativePath, fmt.Sprintf("File logical deleted via Web Explorer: %s", req.RelativePath),
	)


	w.WriteHeader(http.StatusOK)
	w.Write([]byte("Logical deletion successful"))
}

// POST /api/sync/move
func (app *App) handleMove(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	deviceID, err := getDeviceID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}

	var req MoveRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}

	if req.FolderID == 0 || req.FromPath == "" || req.ToPath == "" {
		http.Error(w, "folder_id, from_path, and to_path are required", http.StatusBadRequest)
		return
	}

	var virtualName string
	err = app.db.QueryRow("SELECT virtual_name FROM sync_folders WHERE folder_id = $1", req.FolderID).Scan(&virtualName)
	if err != nil {
		http.Error(w, "Folder not found", http.StatusNotFound)
		return
	}

	srcFilePath, err := securePath(app.storagePath, virtualName, req.FromPath)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	targetFilePath, err := securePath(app.storagePath, virtualName, req.ToPath)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	if err := os.MkdirAll(filepath.Dir(targetFilePath), 0755); err != nil {
		log.Printf("Failed to create target parent directories: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	if _, err := os.Stat(srcFilePath); err == nil {
		if err := os.Rename(srcFilePath, targetFilePath); err != nil {
			log.Printf("Failed to rename physical file from %s to %s: %v", srcFilePath, targetFilePath, err)
			if err := copyFile(srcFilePath, targetFilePath); err == nil {
				_ = os.Remove(srcFilePath)
			} else {
				http.Error(w, "Internal Server Error during file move", http.StatusInternalServerError)
				return
			}
		}
	}

	// Update mapping tables inside a transaction
	tx, err := app.db.Begin()
	if err != nil {
		log.Printf("Failed to begin move transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer tx.Rollback()

	var fileID int
	var fileSize int64
	var fileHash string
	var mtime time.Time

	fileName := filepath.Base(req.ToPath)

	err = tx.QueryRow(`
		UPDATE master_mapping
		SET relative_path = $1, file_name = $2, is_deleted = FALSE, updated_at = CURRENT_TIMESTAMP
		WHERE virtual_name = $3 AND relative_path = $4
		RETURNING file_id, file_size, file_hash, last_modified_at
	`, req.ToPath, fileName, virtualName, req.FromPath).Scan(&fileID, &fileSize, &fileHash, &mtime)

	if err != nil {
		if err == sql.ErrNoRows {
			// If not found in master, insert as new
			// (Should not happen normally, but provides self-healing)
			err = tx.QueryRow(`
				INSERT INTO master_mapping (virtual_name, relative_path, file_name, is_directory, file_size, file_hash, last_modified_at, is_deleted)
				VALUES ($1, $2, $3, FALSE, 0, '', CURRENT_TIMESTAMP, FALSE)
				ON CONFLICT (virtual_name, relative_path)
				DO UPDATE SET is_deleted = FALSE, updated_at = CURRENT_TIMESTAMP
				RETURNING file_id, file_size, file_hash, last_modified_at
			`, virtualName, req.ToPath, fileName).Scan(&fileID, &fileSize, &fileHash, &mtime)
		}
		
		if err != nil {
			log.Printf("DB error in handleMove mapping update: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
	}

	// Update device_mapping for the active device
	_, err = tx.Exec(`
		INSERT INTO device_mapping (device_id, file_id, last_synced_mtime, last_synced_size, last_synced_hash)
		VALUES ($1, $2, $3, $4, $5)
		ON CONFLICT (device_id, file_id)
		DO UPDATE SET last_synced_mtime = EXCLUDED.last_synced_mtime, last_synced_size = EXCLUDED.last_synced_size, last_synced_hash = EXCLUDED.last_synced_hash
	`, deviceID, fileID, mtime, fileSize, fileHash)
	if err != nil {
		log.Printf("Failed to record device_mapping in handleMove: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	// Reset sync mappings for all other devices so they detect the rename/move
	_, _ = tx.Exec("DELETE FROM device_mapping WHERE file_id = $1 AND device_id != $2", fileID, deviceID)

	// Delete corresponding move task from sync_tasks
	_, err = tx.Exec(`
		DELETE FROM sync_tasks 
		WHERE device_id = $1 AND relative_path = $2 AND action_type = 'move'
	`, deviceID, req.FromPath)
	if err != nil {
		log.Printf("Failed to delete move task from sync_tasks: %v", err)
	}

	if err := tx.Commit(); err != nil {
		log.Printf("Failed to commit move transaction: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	_, _ = app.db.Exec(`
		INSERT INTO activity_logs (device_id, action_type, file_path, description)
		VALUES ($1, 'UPDATE', $2, $3)`,
		deviceID, req.ToPath, fmt.Sprintf("File renamed/moved from %s to %s", req.FromPath, req.ToPath),
	)

	w.WriteHeader(http.StatusOK)
	w.Write([]byte("Move successful"))
}


// --- Web UI Specific APIs ---

// GET /api/web/folders
func (app *App) handleWebFolders(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	rows, err := app.db.Query(`
		SELECT sf.folder_id, sf.device_id, d.device_name, sf.virtual_name, sf.local_path
		FROM sync_folders sf
		LEFT JOIN devices d ON sf.device_id = d.device_id
		ORDER BY sf.folder_id ASC
	`)
	if err != nil {
		log.Printf("Query folders error: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type FolderRes struct {
		FolderID    int    `json:"folder_id"`
		DeviceID    int    `json:"device_id"`
		DeviceName  string `json:"device_name"`
		VirtualName string `json:"virtual_name"`
		LocalPath   string `json:"local_path"`
	}

	folders := make([]FolderRes, 0)
	for rows.Next() {
		var f FolderRes
		var dn sql.NullString
		if err := rows.Scan(&f.FolderID, &f.DeviceID, &dn, &f.VirtualName, &f.LocalPath); err != nil {
			log.Printf("Scan folder error: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
		f.DeviceName = "Unknown"
		if dn.Valid {
			f.DeviceName = dn.String
		}
		folders = append(folders, f)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(folders)
}

// GET /api/web/folders/unified
func (app *App) handleWebFoldersUnified(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	rows, err := app.db.Query(`
		SELECT MIN(sf.folder_id) as folder_id, sf.virtual_name, string_agg(COALESCE(d.device_name, 'Unknown'), ',') as device_names
		FROM sync_folders sf
		LEFT JOIN devices d ON sf.device_id = d.device_id
		GROUP BY sf.virtual_name
		ORDER BY sf.virtual_name ASC
	`)
	if err != nil {
		log.Printf("Query folders error: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type FolderRes struct {
		FolderID    int      `json:"folder_id"`
		VirtualName string   `json:"virtual_name"`
		Devices     []string `json:"devices"`
	}

	folders := make([]FolderRes, 0)
	for rows.Next() {
		var f FolderRes
		var dns string
		if err := rows.Scan(&f.FolderID, &f.VirtualName, &dns); err != nil {
			log.Printf("Scan folder error: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
		
		// Split devices comma-separated string
		deviceList := strings.Split(dns, ",")
		uniqueDevices := make([]string, 0)
		seen := make(map[string]bool)
		for _, dev := range deviceList {
			dev = strings.TrimSpace(dev)
			if dev != "" && !seen[dev] {
				seen[dev] = true
				uniqueDevices = append(uniqueDevices, dev)
			}
		}
		f.Devices = uniqueDevices
		folders = append(folders, f)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(folders)
}



// GET /api/web/files
func (app *App) handleWebFiles(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	folderIDStr := r.URL.Query().Get("folder_id")
	if folderIDStr == "" {
		http.Error(w, "folder_id parameter is required", http.StatusBadRequest)
		return
	}
	folderID, err := strconv.Atoi(folderIDStr)
	if err != nil {
		http.Error(w, "Invalid folder_id", http.StatusBadRequest)
		return
	}

	// SQL window function queries all files under the same virtualName of this folderID
	// and picks the newest file version for any duplicate relative paths.
	rows, err := app.db.Query(`
		SELECT file_id, relative_path, file_name, file_size, file_hash, last_modified_at
		FROM master_mapping
		WHERE virtual_name = (SELECT virtual_name FROM sync_folders WHERE folder_id = $1)
		  AND is_deleted = FALSE
		ORDER BY relative_path ASC
	`, folderID)
	if err != nil {
		log.Printf("Query master_mapping files error: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type FileRes struct {
		FileID         int       `json:"file_id"`
		FolderID       int       `json:"folder_id"`
		RelativePath   string    `json:"relative_path"`
		FileName       string    `json:"file_name"`
		FileSize       int64     `json:"file_size"`
		FileHash       string    `json:"file_hash"`
		LastModifiedAt time.Time `json:"last_modified_at"`
		DeviceName     string    `json:"device_name"`
	}

	files := make([]FileRes, 0)
	for rows.Next() {
		var f FileRes
		if err := rows.Scan(&f.FileID, &f.RelativePath, &f.FileName, &f.FileSize, &f.FileHash, &f.LastModifiedAt); err != nil {
			log.Printf("Scan master file error: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
		f.FolderID = folderID
		f.DeviceName = "NAS Master"
		files = append(files, f)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(files)
}

// GET /api/web/activities
func (app *App) handleWebActivities(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	rows, err := app.db.Query(`
		SELECT al.log_id, d.device_name, al.action_type, al.file_path, al.created_at
		FROM activity_logs al
		LEFT JOIN devices d ON al.device_id = d.device_id
		ORDER BY al.created_at DESC
		LIMIT 50
	`)
	if err != nil {
		log.Printf("Query activities error: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type LogRes struct {
		LogID      int       `json:"log_id"`
		DeviceName string    `json:"device_name"`
		ActionType string    `json:"action_type"`
		FilePath   string    `json:"file_path"`
		CreatedAt  time.Time `json:"created_at"`
	}

	logs := make([]LogRes, 0)
	for rows.Next() {
		var l LogRes
		var dn sql.NullString
		if err := rows.Scan(&l.LogID, &dn, &l.ActionType, &l.FilePath, &l.CreatedAt); err != nil {
			log.Printf("Scan activity error: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
		l.DeviceName = "Web"
		if dn.Valid {
			l.DeviceName = dn.String
		}
		logs = append(logs, l)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(logs)
}

// GET /api/web/devices
func (app *App) handleWebDevices(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	rows, err := app.db.Query(`
		SELECT device_id, device_name, os_type, last_connected_at
		FROM devices
		ORDER BY device_id ASC
	`)
	if err != nil {
		log.Printf("Query devices error: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type DeviceRes struct {
		DeviceID        int       `json:"device_id"`
		DeviceName      string    `json:"device_name"`
		OSType          string    `json:"os_type"`
		LastConnectedAt time.Time `json:"last_connected_at"`
	}

	devices := make([]DeviceRes, 0)
	for rows.Next() {
		var d DeviceRes
		if err := rows.Scan(&d.DeviceID, &d.DeviceName, &d.OSType, &d.LastConnectedAt); err != nil {
			log.Printf("Scan device error: %v", err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
			return
		}
		devices = append(devices, d)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(devices)
}

// POST /api/sync/tasks/complete?task_id=X
func (app *App) handleCompleteTask(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}
	deviceID, err := getDeviceID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}
	taskIDStr := r.URL.Query().Get("task_id")
	taskID, err := strconv.Atoi(taskIDStr)
	if err != nil {
		http.Error(w, "Invalid task_id", http.StatusBadRequest)
		return
	}
	_, err = app.db.Exec(`DELETE FROM sync_tasks WHERE task_id = $1 AND device_id = $2`, taskID, deviceID)
	if err != nil {
		log.Printf("Failed to complete task %d: %v", taskID, err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusOK)
}

// GET /api/sync/tasks?folder_id=X
func (app *App) handleGetTasks(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		return
	}

	deviceID, err := getDeviceID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}

	folderIDStr := r.URL.Query().Get("folder_id")
	if folderIDStr == "" {
		http.Error(w, "folder_id is required", http.StatusBadRequest)
		return
	}

	folderID, err := strconv.Atoi(folderIDStr)
	if err != nil {
		http.Error(w, "Invalid folder_id", http.StatusBadRequest)
		return
	}

	rows, err := app.db.Query(`
		SELECT task_id, relative_path, COALESCE(to_path, ''), action_type, file_size, file_hash, COALESCE(last_modified_by, '')
		FROM sync_tasks
		WHERE device_id = $1 AND folder_id = $2 AND status != 'completed'
		ORDER BY task_id ASC
		LIMIT 200
	`, deviceID, folderID)
	if err != nil {
		log.Printf("Failed to query tasks: %v", err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	finalTasks := make([]GoSyncTask, 0)
	for rows.Next() {
		var gt GoSyncTask
		if err := rows.Scan(&gt.TaskID, &gt.RelativePath, &gt.ToPath, &gt.ActionType, &gt.FileSize, &gt.FileHash, &gt.LastModifiedBy); err == nil {
			finalTasks = append(finalTasks, gt)
		}
	}

	var totalPending int
	app.db.QueryRow(`SELECT COUNT(*) FROM sync_tasks WHERE device_id = $1 AND folder_id = $2 AND status != 'completed'`, deviceID, folderID).Scan(&totalPending)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ScanResponse{Tasks: finalTasks, TotalPending: totalPending})
}

