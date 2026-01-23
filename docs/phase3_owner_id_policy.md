# Phase 3: owner_id 責務ポリシー

## 目的

書込み経路の前提を固定し、owner_id 欠落を「即死」させる。

## owner_id を持つテーブル一覧

| テーブル | カラム名 | 書込み経路 | 責務 |
|----------|----------|------------|------|
| Rawdata_FILE_AND_MAIL | owner_id | service_role | 明示指定（取込主体のユーザーID） |
| 10_ix_search_index | owner_id | service_role | 親ドキュメントの owner_id を継承 |
| 10_ix_search_index | owner_id | authenticated | auth.uid() で自動設定（RLS） |
| Rawdata_RECEIPT_shops | owner_id | service_role | 明示指定（レシート所有者） |
| Rawdata_RECEIPT_items | (なし) | service_role | 親レシート経由で制御 |
| MASTER_Rules_transaction_dict | created_by | authenticated | auth.uid() で自動設定（RLS） |
| 99_lg_correction_history | corrector_id | authenticated | auth.uid() で自動設定（RLS） |

## 責務の分離

### client (authenticated) 経路

- **原則**: owner_id は渡さない（RLS の WITH CHECK で auth.uid() が強制される）
- **例外**: 明示的に渡す場合は auth.uid() と一致することを RLS が検証

```python
# OK: owner_id を渡さない（RLS が auth.uid() を強制）
db.client.table('10_ix_search_index').insert({
    'document_id': doc_id,
    'chunk_content': content,
    # owner_id は RLS で自動設定
}).execute()

# NG: 他人の owner_id を渡す（RLS で拒否される）
db.client.table('10_ix_search_index').insert({
    'document_id': doc_id,
    'chunk_content': content,
    'owner_id': other_user_id,  # RLS エラー
}).execute()
```

### server (service_role) 経路

- **原則**: owner_id を必ず明示指定
- **欠落は即死**: DatabaseClient でバリデーション

```python
# OK: owner_id を明示指定
db.client.table('Rawdata_FILE_AND_MAIL').insert({
    'source_id': source_id,
    'file_name': file_name,
    'owner_id': user_id,  # 必須
}).execute()

# NG: owner_id 欠落（コードレベルで例外）
db.client.table('Rawdata_FILE_AND_MAIL').insert({
    'source_id': source_id,
    'file_name': file_name,
    # owner_id 欠落 → OwnerIdRequiredError
}).execute()
```

## 三段構えの防衛線

### 第一防衛線: DB 制約

```sql
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ALTER COLUMN owner_id SET NOT NULL;
```

- owner_id が NULL の INSERT は DB レベルで拒否

### 第二防衛線: RLS ポリシー

```sql
-- authenticated 経路: owner_id = auth.uid() を強制
CREATE POLICY "authenticated_insert_own"
ON "10_ix_search_index" FOR INSERT TO authenticated
WITH CHECK (owner_id = auth.uid());
```

- 他人の owner_id での INSERT は RLS レベルで拒否

### 第三防衛線: コードバリデーション

```python
class OwnerIdRequiredError(Exception):
    """owner_id が必須のテーブルに owner_id なしで INSERT しようとした"""
    pass

# DatabaseClient で INSERT 前にチェック
OWNER_ID_REQUIRED_TABLES = {
    'Rawdata_FILE_AND_MAIL': 'owner_id',
    '10_ix_search_index': 'owner_id',
    'Rawdata_RECEIPT_shops': 'owner_id',
    'MASTER_Rules_transaction_dict': 'created_by',
    '99_lg_correction_history': 'corrector_id',
}
```

## 既存データの扱い

### ⚠️ 重要: SYSTEM_OWNER_ID は実在ユーザーを使用

`00000000-0000-0000-0000-000000000000` のようなダミーUUID ではなく、
**Supabase Auth に実在するユーザー**を SYSTEM_OWNER_ID として使用してください。

理由:
- ダミーUUID は Auth に存在しないため、監査・ロール判定・参照整合で問題が発生
- 将来 RLS を厳格化する際、「誰のものでもないデータ」が残ると移行コストが爆発

### Migration 実行手順

```bash
# 1. SYSTEM ユーザーを作成（初回のみ）
supabase auth admin create-user \
  --email system@example.com \
  --password <secure-password>

# 出力された user_id をメモ（例: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee）

# 2. owner_id マッピングテーブルを作成（推奨）
# workspace/source_type から owner_id を推定するためのマッピング
psql "postgresql://postgres:postgres@localhost:54322/postgres" <<EOF
CREATE TABLE IF NOT EXISTS _migration_owner_mapping (
    workspace TEXT,
    source_type TEXT,
    owner_id UUID NOT NULL
);
INSERT INTO _migration_owner_mapping VALUES
    ('business', NULL, 'user-a-uuid'),      -- ビジネス系 → ユーザーA
    ('household', NULL, 'user-b-uuid'),     -- 家計簿系 → ユーザーB
    (NULL, 'gmail', 'user-c-uuid');         -- Gmail → ユーザーC
EOF

# 3. SYSTEM_OWNER_ID を設定して migration 実行
psql "postgresql://postgres:postgres@localhost:54322/postgres" <<EOF
SET app.system_owner_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
\i supabase/migrations/20260117000001_owner_id_not_null.sql
EOF
```

### 推定ロジック

Migration は以下の順序で owner_id を設定:

1. **マッピングテーブルから推定**
   - `_migration_owner_mapping` テーブルが存在する場合
   - workspace / source_type に基づいて owner_id を割り当て

2. **親ドキュメントから継承**
   - `10_ix_search_index` は親 `Rawdata_FILE_AND_MAIL` の owner_id を継承

3. **SYSTEM_OWNER_ID にフォールバック**
   - 推定できなかったデータのみ SYSTEM_OWNER_ID を使用

## service_role 経路での owner_id 取得方法

### ケース 1: ユーザー操作起点（Gmail取込など）

```python
# Gmail API からユーザーメールアドレスを取得
# → Supabase Auth でユーザーID を検索
user = db.client.auth.admin.get_user_by_email(email)
owner_id = user.id
```

### ケース 2: バッチ処理（定期取込など）

```python
# 環境変数でデフォルトオーナーを指定
DEFAULT_OWNER_ID = os.getenv('DEFAULT_OWNER_ID', '00000000-0000-0000-0000-000000000000')
```

### ケース 3: 親レコードから継承

```python
# ドキュメントのチャンク作成時
doc = db.client.table('Rawdata_FILE_AND_MAIL').select('owner_id').eq('id', doc_id).execute()
owner_id = doc.data[0]['owner_id']

# チャンク INSERT
db.client.table('10_ix_search_index').insert({
    'document_id': doc_id,
    'owner_id': owner_id,  # 親から継承
    ...
}).execute()
```
