# 検証 Runbook（マイグレーション適用後）

このドキュメントは、設計完成後のマイグレーション適用と3実行環境の同一挙動を検証するための手順書です。

---

## **絶対禁止（このRunbookでは絶対にやらない）**

> **以下の操作は、このRunbook実行中は絶対に行わないでください。**
> **止めている車を勝手に動かす事故を防ぐための最小ルールです。**

- **Worker の `--execute` / `--loop` を起動しない**
  ```bash
  # これらは絶対に実行しない
  python scripts/processing/process_queued_documents.py --execute
  python scripts/processing/process_queued_documents.py --loop --execute
  ```

- **`ops.py ... --apply` を実行しない**
  ```bash
  # これらは絶対に実行しない
  python scripts/ops.py requests --apply
  python scripts/ops.py reset-status --apply
  python scripts/ops.py reset-stages --apply
  ```

- **DB の `processing_status` を直接 `completed` にしない**
  ```sql
  -- これは絶対に実行しない（トリガー検証の BEGIN/ROLLBACK 内を除く）
  UPDATE "Rawdata_FILE_AND_MAIL" SET processing_status = 'completed' ...
  ```

---

## 手順タグの意味

| タグ | 意味 | 安全性 |
|------|------|--------|
| `[APPLY]` | 本番DBに変更を加える | **要注意** |
| `[VERIFY]` | 読み取りのみ、変更なし | 安全 |
| `[DESTRUCTIVE-TEST]` | BEGIN/ROLLBACK囲い済み、実際には変更されない | 安全 |
| `[SMOKE]` | curl/pythonコマンドでの動作確認 | 安全 |

---

## 1. [VERIFY] 事前安全確認

### 1-1. Worker が起動していないことを確認

```bash
# プロセス確認（何も出なければOK）
ps aux | grep process_queued_documents | grep -v grep
```

### 1-2. [VERIFY] 現在の STOP 要求を確認（3スコープ）

**必ずこのSQLを実行してから次へ進む。**

Supabase SQL Editor で実行（読み取りのみ）：

```sql
-- ============================================================
-- [VERIFY] STOP 可視化（3スコープ）
-- このSQLは読み取りのみ。変更は発生しない。
-- ============================================================

-- 1. グローバル STOP
SELECT 'GLOBAL' as scope, id, request_type, status, created_at
FROM ops_requests
WHERE request_type IN ('STOP', 'PAUSE')
  AND status = 'queued'
  AND scope_type = 'global'
ORDER BY created_at DESC
LIMIT 10;

-- 2. Workspace STOP
SELECT 'WORKSPACE' as scope, scope_id as workspace, id, request_type, status, created_at
FROM ops_requests
WHERE request_type IN ('STOP', 'PAUSE')
  AND status = 'queued'
  AND scope_type = 'workspace'
ORDER BY created_at DESC
LIMIT 10;

-- 3. Document STOP
SELECT 'DOCUMENT' as scope, scope_id as doc_id, id, request_type, status, created_at
FROM ops_requests
WHERE request_type IN ('STOP', 'PAUSE')
  AND status = 'queued'
  AND scope_type = 'document'
ORDER BY created_at DESC
LIMIT 10;
```

**確認ポイント:**
- STOP 要求があれば「止まっている理由」が明確
- なければ「止まっていない」（だからこそ Worker を起動しない）

---

## 2. [APPLY] マイグレーション適用

### 方法A: Supabase SQL Editor

`database/migrations/create_ops_requests.sql` の内容をコピーして実行。

**注意:** このSQLは冪等（何度実行しても同じ結果）になっています。

### 方法B: psql

```bash
psql "$DATABASE_URL" -f database/migrations/create_ops_requests.sql
```

---

## 3. [VERIFY] DB 適用後の検証SQL

Supabase SQL Editor で実行（すべて読み取りのみ）：

### 3-1. [VERIFY] テーブル・カラムの確認

```sql
-- ============================================================
-- [VERIFY] ops_requests テーブルの存在確認
-- このSQLは読み取りのみ。変更は発生しない。
-- ============================================================
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'ops_requests'
ORDER BY ordinal_position;
```

### 3-2. [VERIFY] CHECK 制約の確認

```sql
-- ============================================================
-- [VERIFY] request_type と status の CHECK 制約
-- このSQLは読み取りのみ。変更は発生しない。
-- ============================================================
SELECT conname as constraint_name, pg_get_constraintdef(oid) as definition
FROM pg_constraint
WHERE conrelid = 'ops_requests'::regclass
  AND contype = 'c';
```

期待結果（2つの CHECK 制約）:
- `ops_requests_request_type_check`
- `ops_requests_status_check`

### 3-3. [VERIFY] トリガー存在確認

```sql
-- ============================================================
-- [VERIFY] 状態遷移トリガーの存在確認
-- このSQLは読み取りのみ。変更は発生しない。
-- ============================================================
SELECT trigger_name, event_manipulation, action_timing
FROM information_schema.triggers
WHERE event_object_table = 'ops_requests'
ORDER BY trigger_name;
```

期待結果:
```
trigger_name                          | event_manipulation | action_timing
--------------------------------------+--------------------+--------------
trg_ops_requests_status_transition    | UPDATE             | BEFORE
```

### 3-4. [VERIFY] 関数存在確認

```sql
-- ============================================================
-- [VERIFY] 関数の存在確認
-- このSQLは読み取りのみ。変更は発生しない。
-- ============================================================
SELECT routine_name, routine_type
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND routine_name IN (
    'enforce_ops_requests_status_transition',
    'has_pending_stop_request'
  )
ORDER BY routine_name;
```

期待結果:
```
routine_name                           | routine_type
---------------------------------------+-------------
enforce_ops_requests_status_transition | FUNCTION
has_pending_stop_request               | FUNCTION
```

---

## 4. [DESTRUCTIVE-TEST] 状態遷移制約の破壊テスト

> **このセクションのSQLはすべて BEGIN/ROLLBACK で囲まれています。**
> **実際にはデータは変更されません。**

### 4-1. [DESTRUCTIVE-TEST] 状態遷移制約が機能することを確認

```sql
-- ============================================================
-- [DESTRUCTIVE-TEST] 状態遷移制約の破壊テスト
-- BEGIN/ROLLBACK で囲まれているため、実際にはデータは変更されない
-- ============================================================
BEGIN;

-- テスト用レコードを作成
INSERT INTO ops_requests (request_type, scope_type, status, requested_by)
VALUES ('STOP', 'global', 'queued', 'verification_test_' || NOW()::TEXT)
RETURNING id, request_type, status;

-- まず applied に遷移（これは成功するはず）
UPDATE ops_requests
SET status = 'applied'
WHERE requested_by LIKE 'verification_test_%';

-- 結果確認（applied になっているはず）
SELECT id, status, applied_at FROM ops_requests WHERE requested_by LIKE 'verification_test_%';

-- 次に queued に戻そうとする（これは失敗するはず）
-- エラー: "Invalid status transition: applied -> queued is not allowed"
UPDATE ops_requests
SET status = 'queued'
WHERE requested_by LIKE 'verification_test_%';

-- ここには到達しない（上でエラーになるため）
-- もし到達したらトリガーが機能していない

ROLLBACK;
-- ロールバックにより、テストデータは残らない
```

**期待結果:**
- 最初の UPDATE（→applied）は成功
- 2番目の UPDATE（→queued）でエラー: `Invalid status transition: applied -> queued is not allowed`
- ROLLBACK によりデータは残らない

### 4-2. [DESTRUCTIVE-TEST] applied_at が自動設定されることを確認

```sql
-- ============================================================
-- [DESTRUCTIVE-TEST] applied_at 自動設定の確認
-- BEGIN/ROLLBACK で囲まれているため、実際にはデータは変更されない
-- ============================================================
BEGIN;

-- テスト用レコードを作成
INSERT INTO ops_requests (request_type, scope_type, status, requested_by)
VALUES ('RELEASE_LEASE', 'workspace', 'queued', 'applied_at_test_' || NOW()::TEXT)
RETURNING id, status, applied_at;
-- applied_at は NULL のはず

-- applied に遷移
UPDATE ops_requests
SET status = 'applied'
WHERE requested_by LIKE 'applied_at_test_%';

-- applied_at が自動設定されていることを確認
SELECT id, status, applied_at, (applied_at IS NOT NULL) as auto_set
FROM ops_requests
WHERE requested_by LIKE 'applied_at_test_%';
-- applied_at が NOW() 付近の値になっているはず

ROLLBACK;
```

---

## 5. [SMOKE] 3実行環境スモークテスト

### 5-0. [VERIFY] 環境指紋の確認

**3環境で同一のコードが動いていることを確認する。**

```bash
# localhost
curl -s http://localhost:5000/api/health | python -m json.tool

# Cloud Run
curl -s "$CLOUDRUN_URL/api/health" | python -m json.tool
```

確認ポイント:
- `version` が一致
- `git_sha` が一致（実装済みの場合）
- `build_time` が妥当な範囲

### 5-A. [SMOKE] localhost（Web）

#### 起動

```bash
python services/doc-processor/app.py
```

#### テスト

```bash
# 1) health
curl -s http://localhost:5000/api/health
# 期待: {"status": "healthy", "mode": "enqueue_only", ...}

# 2) ops requests 可視化
curl -s http://localhost:5000/api/ops/requests | python -m json.tool | head -30
# 期待: {"success": true, "requests": [...], "count": N, "note": "適用するには..."}

# 3) 処理開始が 410 Gone であること
curl -s -X POST http://localhost:5000/api/process/start
# 期待: {"error": "Gone", "message": "このエンドポイントは廃止されました", ...}

# 4) 他の廃止エンドポイントも 410 であること
curl -s -X POST http://localhost:5000/api/process/stop
curl -s -X POST http://localhost:5000/api/process/reset
```

### 5-B. [SMOKE] Cloud Run（Web）

```bash
# 環境変数を設定（実際のURLに置き換え）
CLOUDRUN_URL="https://your-cloud-run-url.run.app"

curl -s "$CLOUDRUN_URL/api/health"
curl -s "$CLOUDRUN_URL/api/ops/requests" | python -m json.tool | head -30
curl -s -X POST "$CLOUDRUN_URL/api/process/start"
```

期待: localhost と同一の結果

### 5-C. [SMOKE] Terminal（CLI: ops のみ）

**process_queued_documents.py は起動しない**

```bash
# 1) ops_requests の一覧（read-only）
python scripts/ops.py requests

# 2) 統計情報
python scripts/ops.py stats

# 3) dry-run で確認（apply しない）
python scripts/ops.py requests --dry-run
```

期待:
- `ops.py` が apply せずに可視化できる
- Worker が動いていないことを確認

---

## 6. 410 Gone の仕様（固定）

> **410 Gone = 存在しない、復活させない**
>
> このレスポンスは「エンドポイントが廃止された」ことを意味します。
> 「設定で有効化できる」「フラグを戻せば動く」という状態ではありません。
> **コード自体が削除されている**ため、復活には新規実装が必要です。

### 410 レスポンス例

```json
{
  "error": "Gone",
  "message": "このエンドポイントは廃止されました",
  "migration": {
    "alternative": "CLI Worker を使用してください",
    "command": "python scripts/processing/process_queued_documents.py --execute",
    "reason": "Web から処理を実行する機能は構造的に存在しません"
  }
}
```

### 410 になっているエンドポイント

| エンドポイント | 移行先 |
|---------------|--------|
| `POST /api/process/start` | CLI Worker |
| `POST /api/process/stop` | `/api/ops/request-stop` |
| `POST /api/process/reset` | `/api/ops/request-release-lease` |
| `POST /api/ops/clear-worker-state` | `/api/ops/request-release-lease` |

---

## 7. 検証完了チェックリスト

| # | 項目 | 手順 | タグ | 結果 |
|---|------|------|------|------|
| 1 | Worker が起動していない | 1-1 | VERIFY | [ ] |
| 2 | STOP 要求の確認（3スコープ） | 1-2 | VERIFY | [ ] |
| 3 | マイグレーション適用 | 2 | APPLY | [ ] |
| 4 | テーブル存在確認 | 3-1 | VERIFY | [ ] |
| 5 | CHECK 制約存在確認 | 3-2 | VERIFY | [ ] |
| 6 | トリガー存在確認 | 3-3 | VERIFY | [ ] |
| 7 | 関数存在確認 | 3-4 | VERIFY | [ ] |
| 8 | 状態遷移制約テスト | 4-1 | DESTRUCTIVE-TEST | [ ] |
| 9 | applied_at 自動設定テスト | 4-2 | DESTRUCTIVE-TEST | [ ] |
| 10 | localhost: /api/health | 5-A | SMOKE | [ ] |
| 11 | localhost: /api/ops/requests | 5-A | SMOKE | [ ] |
| 12 | localhost: /api/process/start = 410 | 5-A | SMOKE | [ ] |
| 13 | Cloud Run: 同上 | 5-B | SMOKE | [ ] |
| 14 | CLI: ops.py requests | 5-C | SMOKE | [ ] |

**全項目チェック完了 = 設計＋適用 完成**

---

## 8. 次フェーズ（処理を動かす判断後）

> **このセクションは、あなたが「動かす」と明確に決めた後にのみ実行してください。**

```bash
# 単一ドキュメントのみ処理（最小実行）
python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute

# 特定ワークスペースのみ処理
python scripts/processing/process_queued_documents.py --workspace <ws> --limit 5 --execute

# 継続ループ（本番運用）
python scripts/processing/process_queued_documents.py --loop --execute
```
