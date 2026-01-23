# Phase 5: Execution Versioning（非破壊的処理）

## 概要

Phase 5 では AI 推論結果の非破壊的な管理を実現します。

**解決する問題:**
1. 再処理時に過去の推論結果が上書きされる
2. 失敗時に正常な過去の結果まで消える
3. AI 推論とドキュメント実体が密結合している

**解決策:**
- `document_executions` テーブルで推論履歴を管理
- `active_execution_id` で「採用中の結果」を参照
- 失敗しても active は変更しない（前の成功結果を保持）

## データモデル

### document_executions テーブル

| カラム | 型 | 説明 |
|--------|-----|------|
| id | UUID | PK |
| document_id | UUID | 対象ドキュメント（FK） |
| owner_id | UUID | データ所有者（NOT NULL） |
| status | TEXT | queued/running/succeeded/failed/canceled |
| model_version | TEXT | 使用モデル |
| prompt_hash | TEXT | プロンプトのハッシュ |
| input_hash | TEXT | 入力の SHA-256（冪等性用） |
| normalized_hash | TEXT | 正規化後テキストの SHA-256 |
| retry_of_execution_id | UUID | リトライ元（系譜追跡） |
| error_code | TEXT | エラーコード |
| error_message | TEXT | エラーメッセージ |
| result_data | JSONB | AI 推論結果 |
| processing_duration_ms | INT | 処理時間 |
| created_at | TIMESTAMPTZ | 作成日時 |
| completed_at | TIMESTAMPTZ | 完了日時 |

### Rawdata_FILE_AND_MAIL.active_execution_id

| カラム | 型 | 説明 |
|--------|-----|------|
| active_execution_id | UUID | 採用中の execution（FK、nullable） |

## 状態遷移

```
                    ┌─────────────────┐
                    │     queued      │
                    └────────┬────────┘
                             │ 処理開始
                             ▼
                    ┌─────────────────┐
                    │     running     │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
      ┌───────────┐  ┌───────────┐  ┌───────────┐
      │ succeeded │  │  failed   │  │ canceled  │
      └─────┬─────┘  └───────────┘  └───────────┘
            │
            │ active_execution_id 更新
            ▼
    ┌────────────────────┐
    │ documents.active   │
    │ が新 execution を  │
    │ 指すように更新     │
    └────────────────────┘
```

## 処理フロー

### 正常フロー

```python
# 1. execution 作成
ctx = manager.create_execution(
    document_id=doc_id,
    owner_id=owner_id,
    input_text=combined_text,
    model_version="gemini-2.5-flash"
)

# 2. 処理実行
result = await process_document(...)

# 3. succeeded にマーク + active 切り替え
manager.mark_succeeded(
    execution_id=ctx.execution_id,
    result_data={'summary': ..., 'metadata': ...},
    processing_duration_ms=duration
)
```

### 失敗フロー

```python
try:
    result = await process_document(...)
except Exception as e:
    # failed にマーク（active は変更しない）
    manager.mark_failed(
        execution_id=ctx.execution_id,
        error_code='PROCESSING_ERROR',
        error_message=str(e)
    )
```

## ハッシュ仕様

### input_hash

推論入力の SHA-256。同一入力の検知・冪等性のために使用。

```python
content = input_text + "\n---METADATA---\n" + json.dumps(metadata, sort_keys=True)
input_hash = hashlib.sha256(content.encode()).hexdigest()
```

### normalized_hash

前処理後テキストの SHA-256。将来の正規化処理差分検知用。

```python
normalized_hash = hashlib.sha256(normalized_text.encode()).hexdigest()
```

## 使用方法

### パイプラインでの有効化

```python
result = await pipeline.process_document(
    file_path=path,
    file_name=name,
    doc_type=doc_type,
    workspace=workspace,
    mime_type=mime_type,
    source_id=source_id,
    owner_id=owner_id,
    enable_execution_tracking=True  # Phase 5 有効化
)
```

### ExecutionManager 直接使用

```python
from shared.processing.execution_manager import ExecutionManager

manager = ExecutionManager()

# 既存の成功 execution を検索（冪等性）
existing = manager.find_existing_execution(doc_id, input_hash)
if existing:
    print("同一入力の処理済み execution あり")
    return existing['result_data']

# 履歴取得
history = manager.get_execution_history(doc_id, limit=10)

# active execution 取得
active = manager.get_active_execution(doc_id)
```

## テスト実行

```bash
# ユニットテスト（Supabase 不要）
pytest tests/test_phase5_execution_versioning.py -v -m "not integration"

# 統合テスト（Supabase ローカル起動済み）
export SUPABASE_URL=http://127.0.0.1:54321
export SUPABASE_SERVICE_ROLE_KEY=<key>
pytest tests/test_phase5_execution_versioning.py -v -m integration
```

## 検証コマンド

```sql
-- ドキュメントの execution 履歴
SELECT
    d.file_name,
    e.id AS execution_id,
    e.status,
    e.model_version,
    e.created_at,
    CASE WHEN d.active_execution_id = e.id THEN '★' ELSE '' END AS is_active
FROM "Rawdata_FILE_AND_MAIL" d
JOIN document_executions e ON e.document_id = d.id
ORDER BY d.id, e.created_at DESC;

-- 失敗した execution の一覧
SELECT
    d.file_name,
    e.error_code,
    e.error_message,
    e.created_at
FROM document_executions e
JOIN "Rawdata_FILE_AND_MAIL" d ON e.document_id = d.id
WHERE e.status = 'failed'
ORDER BY e.created_at DESC;

-- active がない（未処理）ドキュメント
SELECT id, file_name, processing_status
FROM "Rawdata_FILE_AND_MAIL"
WHERE active_execution_id IS NULL
AND processing_status = 'completed';
```

## RLS ポリシー

| ロール | SELECT | INSERT | UPDATE | DELETE |
|--------|--------|--------|--------|--------|
| authenticated | 自分の owner_id のみ | - | - | - |
| service_role | 全て | 全て | 全て | 全て |
| anon | - | - | - | - |

## 次フェーズ候補（Phase 5 では未実施）

1. **chunks に execution_id を付与**
   - 10_ix_search_index.execution_id を追加
   - どの execution 由来のチャンクかを追跡可能に

2. **result_data の正規化**
   - summary, metadata, chunks を別テーブルに分離
   - より細かい粒度での管理

3. **冪等性の自動化**
   - input_hash が同一なら処理をスキップ
   - 既存の成功 execution を再利用

4. **実行キューの統合**
   - document_executions.status='queued' をキューとして使用
   - run_executions との統合

## 関連ドキュメント

- [Phase 3: owner_id ポリシー](phase3_owner_id_policy.md)
- [Phase 4A: Public API 契約](phase4a_public_api_contract.md)
- [Phase 4B: 整合性チェック](phase4b_integrity_checks.md)
