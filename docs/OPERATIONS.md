# 運用ガイド - 3環境保証アーキテクチャ

## 関連ドキュメント

- [ARCHITECTURE.md](ARCHITECTURE.md) - システム設計の詳細
- [VERIFICATION_RUNBOOK.md](VERIFICATION_RUNBOOK.md) - マイグレーション適用後の検証手順

---

## 設計原則

### Web UI は投入のみ、処理は Worker のみ

| コンポーネント | 役割 | 処理実行 |
|--------------|------|---------|
| Web UI (Cloud Run / localhost) | 投入・閲覧・リセット | **構造的に不可能** |
| Worker (CLI) | 処理実行 | **唯一の入口** |

この設計により、以下を保証:
- Cloud Run / localhost / ターミナル で挙動差が出ない
- 意図しない処理実行事故を構造的に防止
- 止めている車を勝手に動かさない

### 重要：設定による切り替えは存在しない

Web から処理を実行する機能は**コードレベルで削除済み**です。
環境変数や設定ファイルで「処理を有効化」することは**不可能**です。
これは意図的な設計であり、事故防止のための構造的保証です。

### 分散ガード禁止の原則

**「無効化を最有効化してしまう」事故を防ぐため、以下を厳守：**

1. **ガードは設定で散らさない** - `ENABLE_PROCESSING`, `ALLOW_RUN`, `WEB_MODE` のような設定変数を複数箇所に持たない
2. **境界で分離する** - Web バイナリには処理コードが存在しない、Worker だけが処理できる
3. **停止の真実は DB のみ** - `worker_state.stop_requested` が唯一の停止レバー、プログラム内フラグは禁止
4. **語彙を統制する** - "start", "enable", "allow" を Web 側のコードで使わない

### 入口は3本のみ（Entry Point Consolidation）

**システムへの入口は以下の3本のみ。これ以上増やさない。**

| 入口 | 役割 | 説明 |
|------|------|------|
| `services/doc-processor/app.py` | Web API | 読み取り + enqueue のみ。処理実行コードなし |
| `scripts/ops.py` | 運用操作 | stats, stop, reset-status, reset-stages, requests |
| `scripts/processing/process_queued_documents.py` | 処理実行 | 唯一の処理実行入口 |

**廃止済み（410 Gone / stub化）:**
- `scripts/processing/process_single_doc.py` → stub（process_queued_documents.py --doc-id を使用）
- `scripts/reset/reset_*.py` → wrapper（ops.py を呼び出すだけ）

**CI で強制:**
- `scripts/processing/` 内で `asyncio.run` を持つファイルは `process_queued_documents.py` のみ許可
- `scripts/reset/` 内で `.update(` や `.insert(` を持つファイルは禁止（wrapper/stub のみ）
- 違反時は CI が失敗

**新しい処理スクリプトを追加したい場合:**
1. 新しいファイルを作成するのではなく、`process_queued_documents.py` に `--option` を追加
2. 例: `--workspace flyer` で特定ワークスペースのみ処理

### Web API の責務境界

| API | 責務 | 禁止事項 |
|-----|------|---------|
| `/api/ops/request-stop` | ops_requests に STOP 要求を書く | Worker を直接停止しない |
| `/api/ops/request-release-lease` | ops_requests に RELEASE_LEASE 要求を書く | 直接 DB を更新しない（緊急時のみフォールバック） |
| `/api/ops/clear-worker-state` | **非推奨** - request-release-lease を使用 | - |
| `/api/process/stats` | DB から統計を読む | 処理を実行しない |
| `/api/process/progress` | DB から進捗を読む | 処理を実行しない |

**注意:**
- `/api/process/start` は**存在しません**（410 Gone）。処理開始は CLI Worker のみ。
- `/api/process/stop`, `/api/process/reset` も**廃止**（410 Gone）。

### ops_requests テーブル（運用要求SSOT）

**ops_requests が運用要求の真実（SSOT）。**

```
ops_requests テーブル（SSOT）
    ↓ ops.py requests --apply で適用
worker_state.stop_requested（派生キャッシュ）
    ↓ Worker が読み取り
ExecutionPolicy.can_execute()
```

- Web/ops.py → ops_requests に INSERT（enqueue のみ）
- apply は **ops.py のみ**が行う（Worker は処理のみ）
- worker_state.stop_requested は派生キャッシュ（ops.py のみが書き込み）

| request_type | 説明 |
|--------------|------|
| `STOP` | 処理停止 |
| `RESUME` | 処理再開 |
| `RELEASE_LEASE` | リース解放（stuck対策） |
| `RESET_DOC` | 単一ドキュメントをpendingに戻す |
| `RESET_WORKSPACE` | workspace全体をpendingに戻す |
| `CLEAR_STAGES` | ステージE-Kをクリア |
| `PAUSE` | 一時停止（新規処理を抑止） |
| `RUN` | 処理実行要求（Web UI から Worker への依頼） |

### Run Request（処理実行要求）

**Web UI から Worker に処理を依頼する仕組み。**

```
[Web UI] POST /api/run-requests
    ↓
[ops_requests] INSERT (request_type=RUN, payload={max_items, workspace, ...})
    ↓
[Worker] --run-request <id> --execute
    ↓
[run_executions] INSERT/UPDATE (Evidence: 処理結果)
```

**設計原則:**
- ops_requests = 要求SSOT（意図のみ格納）
- run_executions = Evidence（実行結果のみ格納）
- Worker は ops_requests を更新しない
- 同一要求に対して複数回実行可能（リトライ対応）

**payload 構造:**
```json
{
  "max_items": 5,           // 最大処理件数（デフォルト5、上限100）
  "workspace": "ema_classroom",  // 対象ワークスペース（省略時は全体）
  "doc_id": "uuid"          // 特定ドキュメントのみ（省略時は自動選択）
}
```

**常駐禁止:**
- `--loop` 引数は削除済み
- `continuous_processing_loop` は削除済み
- Cloud Run 24時間課金を防ぐためバッチ1回実行のみ

### 廃止予定（Deprecation Timeline）

| 項目 | 廃止予定 | 移行先 |
|------|---------|--------|
| `worker_state.stop_requested` | 2025年Q2 | `ops_requests.STOP` |
| `/api/ops/clear-worker-state` | 廃止済み（410 Gone） | `/api/ops/request-release-lease` |
| `/api/process/start` | 廃止済み（410 Gone） | CLI Worker |
| `/api/process/stop` | 廃止済み（410 Gone） | `/api/ops/request-stop` |

### request_type 追加手順

新しい request_type を追加する場合の手順（分散を防ぐ）:

1. **DB**: `create_ops_requests.sql` の CHECK 制約に追加
2. **ExecutionPolicy**: 必要なら deny_code を追加
3. **ops.py**: `apply_ops_request()` に処理を追加
4. **Web API**: 必要なら enqueue 用エンドポイントを追加
5. **CI**: architecture-guard.yml を更新（必要な場合）
6. **Docs**: この表を更新

**注意**: この順序を守らないと、分散ガードが再発します。

---

## 3環境の起動コマンド

### 1. Cloud Run（本番）

```bash
# Dockerfile / Cloud Run 設定で起動
gunicorn -b :$PORT services.doc_processor.app:app \
    --workers 1 \
    --threads 8 \
    --timeout 3600

# 処理実行設定は不要（Web は enqueue-only、設定で変更不可）
```

### 2. localhost（開発）

```bash
# 方法1: 直接実行
cd document-management-system
python services/doc-processor/app.py

# 方法2: gunicorn（Cloud Run と同じ挙動）
gunicorn -b :5000 services.doc_processor.app:app --reload

# 処理実行設定は不要（Web は enqueue-only、設定で変更不可）
```

### 3. ターミナル（Worker）

```bash
cd document-management-system

# dry-run（処理対象を確認、実行しない）
python scripts/processing/process_queued_documents.py --limit 10

# 単一ドキュメント処理
python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute

# バッチ処理（100件）
python scripts/processing/process_queued_documents.py --limit 100 --execute

# 1件だけ処理
python scripts/processing/process_queued_documents.py --once --execute

# 特定ワークスペースのみ
python scripts/processing/process_queued_documents.py --workspace ema_classroom --limit 20 --execute

# Run Request 実行（Web UI からの要求を処理）
python scripts/processing/process_queued_documents.py --run-request <uuid> --execute

# 統計情報のみ
python scripts/processing/process_queued_documents.py --stats
```

**常駐禁止:** `--loop` は削除済み。Cloud Run 24時間課金を防ぐため、バッチ1回実行のみ対応。

---

## 運用コマンド（ops.py）

処理実行以外の運用操作は `scripts/ops.py` を使用。
**dry-run → apply の二段階実行**で事故防止。

```bash
cd document-management-system

# 統計情報
python scripts/ops.py stats
python scripts/ops.py stats --workspace ema_classroom

# 停止要求（ops_requests に登録）
python scripts/ops.py stop
python scripts/ops.py stop --workspace ema_classroom --reason "メンテナンス"

# リース解放要求（stuck対策）
python scripts/ops.py release-lease --workspace ema_classroom
python scripts/ops.py release-lease --doc-id <uuid>

# processing→pending にリセット（dry-run がデフォルト）
python scripts/ops.py reset-status --workspace ema_classroom         # dry-run
python scripts/ops.py reset-status --workspace ema_classroom --apply # 実行

# ステージE-K クリア（dry-run がデフォルト）
python scripts/ops.py reset-stages --workspace ema_classroom         # dry-run
python scripts/ops.py reset-stages --workspace ema_classroom --apply # 実行
python scripts/ops.py reset-stages --doc-id <uuid> --apply

# ops_requests の管理
python scripts/ops.py requests          # 未処理の要求一覧
python scripts/ops.py requests --apply  # 要求を適用
```

### dry-run / apply パターン

危険な操作は**必ず dry-run で確認してから apply**:

```bash
# 1. dry-run で影響範囲を確認（デフォルト）
python scripts/ops.py reset-stages --workspace ema_classroom
# → 対象件数と一覧が表示される

# 2. 問題なければ --apply で実行
python scripts/ops.py reset-stages --workspace ema_classroom --apply
# → 確認プロンプト → 実行
```

---

## よくある操作フロー

### 1. 止まっている処理を再開したい

```bash
# 1. 状態確認
python scripts/ops.py stats

# 2. processing で止まっているものを pending に戻す
python scripts/ops.py reset-status --workspace all --yes

# 3. 処理実行
python scripts/processing/process_queued_documents.py --limit 10 --execute
```

### 2. 特定ドキュメントを再処理したい

```bash
# 1. ステージデータをクリア
python scripts/ops.py reset-stages --doc-id <uuid>

# 2. 処理実行
python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute
```

### 3. UI で手動補正後に再処理

1. フロントエンドで補正テキストを編集
2. 「再処理要求を登録」ボタンをクリック
3. ターミナルで処理実行（または Worker が自動で拾う）:

```bash
python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute
```

---

## 廃止されたパターン

以下の操作は**構造的に不可能**です（コードが存在しない）:

### Web UI から処理実行（物理的に削除済み）

```
❌ /api/process/start → エンドポイント自体が存在しない（404）
❌ stage_h_reprocessor の直接処理実行 → コード削除済み（enqueue のみ）
❌ WEB_MODE 設定 → 設定変数自体が存在しない
```

### 理由

- 「ガードで守る」ではなく「コードが存在しない」で保証
- 設定ミスや復活事故の余地をゼロにする
- 分散ガード禁止の原則を徹底

---

## ヘルスチェック

Web UI のヘルスチェック:

```bash
curl http://localhost:5000/api/health
```

レスポンス例:

```json
{
  "status": "ok",
  "message": "Document Processing System is running",
  "version": "2025-01-16-enqueue-only",
  "mode": "enqueue_only",
  "note": "Processing is only available via CLI Worker"
}
```

`mode: enqueue_only` は固定値です（設定で変更不可）。

---

## トラブルシューティング

### Q: Web UI の「処理開始」ボタンが動かない

**A: 正常動作です。** Web から処理を開始する機能は廃止されました。CLI Worker を使用してください:

```bash
python scripts/processing/process_queued_documents.py --execute
```

### Q: processing 状態で止まったドキュメントがある

**A:** reset-status で pending に戻してから再処理:

```bash
python scripts/ops.py reset-status --workspace all --yes
python scripts/processing/process_queued_documents.py --execute
```

### Q: 処理結果がおかしいので再処理したい

**A:** ステージデータをクリアしてから再処理:

```bash
python scripts/ops.py reset-stages --doc-id <uuid>
python scripts/processing/process_queued_documents.py --doc-id <uuid> --execute
```

---

## processing_stage 値体系（SSOT）

`Rawdata_FILE_AND_MAIL.processing_stage` に設定される値の一覧。
**この値体系を変更する場合は、Worker と UI の両方を同時に更新すること。**

### 処理進行状態（Worker が設定）

| 値 | 意味 | 設定元 |
|----|------|--------|
| `開始` | 処理開始 | processor.py |
| `ダウンロード中` | ファイルダウンロード中 | processor.py |
| `Stage E-K: 処理中` | パイプライン実行中 | processor.py |
| `Stage H: 構造化` | Stage H 実行中 | processor.py |
| `Stage J: チャンク化` | Stage J 実行中 | processor.py |
| `Stage K: Embedding` | Stage K 実行中 | processor.py |
| `完了` | 処理完了 | processor.py |
| `エラー` | 処理失敗 | processor.py |

### Ingestion 状態（データ取り込み Worker が設定）

| 値 | 意味 | 設定元 |
|----|------|--------|
| `gmail_html` | Gmail HTML 取得済み | gmail_ingestion.py |
| `gmail_attachment_downloaded` | Gmail 添付ダウンロード済み | gmail_ingestion.py |
| `waseda_notice_downloaded` | 早稲アカお知らせダウンロード済み | notice_ingestion.py |
| `tokubai_flyer_downloaded` | トクバイチラシダウンロード済み | flyer_ingestion.py |
| `products_extracted` | 商品情報抽出済み | flyer_processor.py |

### 再処理要求（UI が設定、Worker が読み取り）

| 値パターン | 意味 | 設定元 |
|-----------|------|--------|
| `reprocess_from_h_requested:{source}` | Stage H から再処理要求 | stage_h_reprocessor.py |
| `None` | リセット済み（pending に戻す際） | ops.py / reset scripts |

### 新しい再処理要求を追加する場合

1. 値のパターンは `reprocess_from_{stage}_requested:{source}` に統一
2. `{stage}` = 開始したいステージ（e, f, h, i, j, k）
3. `{source}` = 要求元（manual_edit, api, batch_fix 等）
4. Worker 側で該当パターンを認識して適切なステージから処理開始

例：
```python
# Stage E から再処理（OCR やり直し）
'processing_stage': 'reprocess_from_e_requested:ocr_fix'

# Stage K のみ再処理（Embedding やり直し）
'processing_stage': 'reprocess_from_k_requested:embedding_update'
```
