# Phase 2: Supabase Local 完結セットアップ

クラウド Dashboard/SQL Editor 依存ゼロ。`supabase local` で完結する手順。

## 前提条件

- Docker Desktop がインストール済み
- Supabase CLI がインストール済み (`npm install -g supabase`)

## 手順

### Step 1: Supabase Local 起動

```bash
cd document-management-system

# 初回のみ: supabase init
supabase init

# ローカル環境起動
supabase start
```

起動後、以下の情報が表示されます:
```
API URL: http://localhost:54321
anon key: eyJ...
service_role key: eyJ...
```

### Step 2: 環境変数設定

`.env.local` を作成:

```bash
# supabase start の出力から取得
SUPABASE_URL=http://localhost:54321
SUPABASE_KEY=eyJ...(anon key)
SUPABASE_SERVICE_ROLE_KEY=eyJ...(service_role key)
```

### Step 3: Migration 適用

```bash
# Migration を適用
supabase db reset

# または個別に適用
supabase db push
```

Migration ファイル:
- `supabase/migrations/20260116000001_add_owner_id.sql` - owner_id カラム追加
- `supabase/migrations/20260116000002_rls_with_owner.sql` - RLS ポリシー設定

### Step 4: テストユーザー作成

```bash
# Supabase Studio で作成（http://localhost:54323）
# Authentication > Users > Add User

# または CLI で作成
supabase auth admin create-user \
  --email alice@example.com \
  --password alice123456 \
  --user-id 11111111-1111-1111-1111-111111111111

supabase auth admin create-user \
  --email bob@example.com \
  --password bob123456 \
  --user-id 22222222-2222-2222-2222-222222222222
```

### Step 5: Seed データ投入

```bash
# seed.sql を SQL Editor で実行
# または psql で直接投入

psql "postgresql://postgres:postgres@localhost:54322/postgres" \
  -f supabase/seed.sql
```

### Step 6: テスト実行

```bash
# 環境変数をロード
source .env.local  # Linux/Mac
# または
set -a; . .env.local; set +a  # bash

# テスト実行
python tests/test_phase2_permissions.py
```

### Step 7: UI 検証

```bash
# Document Review App
streamlit run frontend/document_review_app.py

# 家計簿レビューUI
streamlit run shared/kakeibo/review_ui.py
```

---

## 一本道コマンド（コピペ用）

```bash
# 1. ローカル環境起動
supabase start

# 2. DB リセット & Migration 適用
supabase db reset

# 3. テストユーザー作成
supabase auth admin create-user \
  --email alice@example.com \
  --password alice123456 \
  --user-id 11111111-1111-1111-1111-111111111111

supabase auth admin create-user \
  --email bob@example.com \
  --password bob123456 \
  --user-id 22222222-2222-2222-2222-222222222222

# 4. Seed データ投入
psql "postgresql://postgres:postgres@localhost:54322/postgres" \
  -f supabase/seed.sql

# 5. テスト実行
python tests/test_phase2_permissions.py
```

---

## RLS ポリシーサマリー

### UPDATE/DELETE 許可条件

| テーブル | USING 条件 | WITH CHECK 条件 |
|----------|------------|-----------------|
| Rawdata_FILE_AND_MAIL | `owner_id = auth.uid()` | `owner_id = auth.uid()` |
| 10_ix_search_index (DELETE) | `owner_id = auth.uid()` | - |
| 10_ix_search_index (INSERT) | - | `owner_id = auth.uid()` |
| Rawdata_RECEIPT_shops | `owner_id = auth.uid()` | `owner_id = auth.uid()` |
| Rawdata_RECEIPT_items | 親レシートの `owner_id = auth.uid()` | 同左 |
| MASTER_Rules_transaction_dict | `created_by = auth.uid()` | `created_by = auth.uid()` |
| 99_lg_correction_history (INSERT) | - | `corrector_id = auth.uid()` |

### anon SELECT 許可テーブル一覧（確定）

| テーブル | anon SELECT | 理由 |
|----------|-------------|------|
| `Rawdata_FILE_AND_MAIL` | ✅ 許可 | doc-search API（公開検索） |
| `10_ix_search_index` | ✅ 許可 | doc-search API（公開検索） |
| 上記以外すべて | ❌ 禁止 | 管理者専用データ |

**重要**: anon SELECT を許可するテーブルは上記2つのみ。
それ以外のテーブルは GRANT SELECT すら付与しない。

### authenticated SELECT 範囲（現設計）

| テーブル | authenticated SELECT | 理由 |
|----------|---------------------|------|
| 全テーブル | ✅ 全データ見える | Admin UI で全ドキュメント/レシートをレビューするため |

**設計判断**: 現在の Admin UI は「管理者が全データをレビューする」前提で設計。
将来「自分のデータのみ表示」に変更する場合は、RLS の SELECT ポリシーを修正:

```sql
-- 例: Rawdata_FILE_AND_MAIL を自分のデータのみに制限
CREATE POLICY "authenticated_select_own_rawdata"
ON "Rawdata_FILE_AND_MAIL" FOR SELECT TO authenticated
USING (owner_id = auth.uid());
```

### anon 書き込み不可の確認

すべての書き込み系操作（INSERT/UPDATE/DELETE）に対して:
- GRANT 文で anon に書き込み権限を付与していない
- RLS ポリシーで anon 向けの書き込みポリシーを作成していない

---

## トラブルシューティング

### 「relation does not exist」エラー

Migration が適用されていない可能性があります。

```bash
supabase db reset
```

### 「permission denied」エラー

RLS ポリシーが正しく設定されていない可能性があります。

```sql
-- Supabase Studio SQL Editor で確認
SELECT * FROM pg_policies WHERE schemaname = 'public';
```

### ユーザー認証失敗

ユーザーが作成されているか確認:

```bash
# Supabase Studio: http://localhost:54323
# Authentication > Users
```

---

## ファイル構成

```
supabase/
├── config.toml                          # Supabase Local 設定
├── migrations/
│   ├── 20260116000001_add_owner_id.sql # owner_id カラム追加
│   └── 20260116000002_rls_with_owner.sql # RLS ポリシー
└── seed.sql                             # テストデータ

tests/
└── test_phase2_permissions.py           # RLS 境界テスト
```
