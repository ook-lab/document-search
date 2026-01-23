# Phase 2: Database Permission Fix - Implementation Guide

## 概要

Phase 2の目標は、Supabaseのアクセス制御を正しく設定し、安全なローカル実行を可能にすることです。

---

## 1. Access Matrix (最終版)

### Category 1: External API (anon + RLS)
doc-search API から呼び出される。匿名ユーザー向け、SELECT のみ許可。

| テーブル | anon | authenticated | service_role | 備考 |
|---------|------|---------------|--------------|------|
| `Rawdata_FILE_AND_MAIL` | SELECT | ALL | ALL | RPC `unified_search_v2` 経由 |
| `10_ix_search_index` | SELECT | ALL | ALL | RPC 経由でベクトル検索 |

### Category 2: Admin UI (authenticated + RLS)
Streamlit管理UI (`document_review_app.py`, `review_ui.py`) から呼び出される。

| テーブル | anon | authenticated | service_role | 使用元 |
|---------|------|---------------|--------------|--------|
| `10_ix_search_index` | - | ALL | ALL | document_review_app.py |
| `Rawdata_RECEIPT_shops` | - | ALL | ALL | review_ui.py |
| `Rawdata_RECEIPT_items` | - | ALL | ALL | review_ui.py |
| `Rawdata_NETSUPER_items` | - | ALL | ALL | review_ui.py |
| `MASTER_Categories_product` | - | ALL | ALL | review_ui.py |
| `MASTER_Categories_purpose` | - | ALL | ALL | review_ui.py |
| `MASTER_Categories_expense` | - | ALL | ALL | review_ui.py |
| `MASTER_Rules_expense_mapping` | - | ALL | ALL | review_ui.py |
| `MASTER_Rules_transaction_dict` | - | ALL | ALL | review_ui.py |
| `MASTER_Product_generalize` | - | ALL | ALL | review_ui.py |
| `MASTER_Product_classify` | - | ALL | ALL | review_ui.py |
| `99_lg_correction_history` | - | ALL | ALL | document_review_app.py |
| `99_lg_image_proc_log` | - | SELECT | ALL | review_ui.py |
| `99_tmp_gemini_clustering` | - | ALL | ALL | review_ui.py |
| `60_ms_categories` | - | SELECT | ALL | review_ui.py |
| `v_expense_category_rules` | - | SELECT | SELECT | VIEW (要作成) |

### Category 3: Internal Worker Only (service_role のみ)
バッチ処理・ワーカー専用。クライアントからはアクセス不可。

| テーブル | anon | authenticated | service_role | 使用元 |
|---------|------|---------------|--------------|--------|
| `processing_lock` | - | - | ALL | Worker |
| `worker_state` | - | - | ALL | Worker |
| `ops_requests` | - | - | ALL | Worker |
| `run_executions` | - | - | ALL | Worker |
| `retry_queue` | - | - | ALL | Worker |
| `skip_allowlist` | - | - | ALL | Worker |
| `workspace_controls` | - | - | ALL | Worker (存在する場合) |

### Category 5: Execution Versioning (Phase 5)
AI推論履歴の管理。SELECT は Admin 全件可視、INSERT は Worker のみ。

| テーブル | anon | authenticated | service_role | 備考 |
|---------|------|---------------|--------------|------|
| `document_executions` | - | SELECT (全件), UPDATE*, DELETE* | ALL | Phase 5 追加、* = owner_id 制限 |

**注意:** authenticated は SELECT で全データ閲覧可能（Admin UI 全件可視）、UPDATE/DELETE は自分の owner_id のみ。INSERT は service_role（Worker 経由）のみ。

### Category 4: Log/Master (authenticated読み取り, service_role書き込み)
上記Category 2に含まれるため省略。

---

## 2. SQL Migration 実行手順

### 2.1 事前確認

```sql
-- 現在のRLS状態を確認
SELECT
    schemaname,
    tablename,
    rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
```

### 2.2 Migration実行

Supabase SQL Editorで `migrations/phase2_permission_fix.sql` を実行:

1. Supabase Dashboard にログイン
2. SQL Editor を開く
3. `phase2_permission_fix.sql` の内容をコピー&ペースト
4. 「Run」をクリック

### 2.3 実行後確認

```sql
-- RLSポリシーの確認
SELECT
    tablename,
    policyname,
    roles,
    cmd
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;
```

---

## 3. Code Changes (最小限)

### 3.1 現状

Admin UIは現在 `DatabaseClient()` (anon key) を使用:

```python
# frontend/document_review_app.py line 156
db_client = DatabaseClient()

# shared/kakeibo/review_ui.py line 28
db_client = DatabaseClient()
```

### 3.2 必要な変更

Admin UIが `authenticated` ロールを使用するには、Supabase Auth 統合が必要です。

#### Option A: 簡易認証 (推奨 - Phase 2.1)

Streamlit password入力 + Supabase Auth:

1. **DatabaseClient の拡張**

```python
# shared/common/database/client.py に追加

def __init__(self, use_service_role: bool = False, access_token: str = None):
    """
    Args:
        use_service_role: True = service_role key (Worker用)
        access_token: Supabase Auth JWT token (authenticated用)
    """
    if use_service_role:
        api_key = settings.SUPABASE_SERVICE_ROLE_KEY
    elif access_token:
        api_key = settings.SUPABASE_KEY  # anon key
        # JWT token をヘッダーに設定
        self.client = create_client(settings.SUPABASE_URL, api_key)
        self.client.auth.set_session(access_token, "")
        return
    else:
        api_key = settings.SUPABASE_KEY

    self.client = create_client(settings.SUPABASE_URL, api_key)
```

2. **Streamlit認証ページの追加**

```python
# frontend/auth.py (新規作成)
import streamlit as st
from supabase import create_client

def login_page():
    st.title("Login")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        response = client.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        if response.user:
            st.session_state.access_token = response.session.access_token
            st.rerun()
        else:
            st.error("Login failed")

def require_auth():
    if "access_token" not in st.session_state:
        login_page()
        st.stop()
    return st.session_state.access_token
```

3. **Admin UIの修正**

```python
# frontend/document_review_app.py
from frontend.auth import require_auth

def pdf_review_ui():
    access_token = require_auth()
    db_client = DatabaseClient(access_token=access_token)
    # ...
```

#### Option B: 暫定対応 (Phase 2.0)

**重要**: SQL Migration適用後、Admin UIは `anon` では動作しなくなります。

暫定対応として、Admin UIで `service_role` を使用することも可能ですが:
- セキュリティ上は非推奨
- 開発・ローカルテスト用途のみ

```python
# frontend/document_review_app.py (暫定)
db_client = DatabaseClient(use_service_role=True)
```

### 3.3 Worker側 (変更不要)

Worker系はすでに `use_service_role=True` を使用:

```python
# scripts/utils/fix_database.py
db_client = DatabaseClient(use_service_role=True)
```

---

## 4. Local Verification Procedures

### 4.1 SQL Migration 適用確認

```sql
-- 1. RLSが有効化されているか確認
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
AND tablename IN (
    'Rawdata_FILE_AND_MAIL',
    '10_ix_search_index',
    'processing_lock',
    'worker_state'
);

-- 期待結果: 全て rowsecurity = true

-- 2. anon の権限確認
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'anon'
AND table_schema = 'public'
ORDER BY table_name;

-- 期待結果:
-- Rawdata_FILE_AND_MAIL: SELECT のみ
-- 10_ix_search_index: SELECT のみ
-- processing_lock: なし
-- worker_state: なし

-- 3. authenticated の権限確認
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'authenticated'
AND table_schema = 'public'
AND table_name IN ('Rawdata_RECEIPT_shops', 'MASTER_Categories_product')
ORDER BY table_name;

-- 期待結果: ALL (INSERT, UPDATE, DELETE, SELECT)
```

### 4.2 Python からの接続テスト

```python
# tests/test_permissions.py (新規作成)

import pytest
from shared.common.database.client import DatabaseClient

def test_anon_cannot_write_internal_tables():
    """anon は内部テーブルに書き込めない"""
    db = DatabaseClient(use_service_role=False)

    with pytest.raises(Exception):
        db.client.table('processing_lock').insert({
            'id': 999,
            'is_processing': False
        }).execute()

def test_anon_can_read_rawdata():
    """anon は Rawdata_FILE_AND_MAIL を読める"""
    db = DatabaseClient(use_service_role=False)

    result = db.client.table('Rawdata_FILE_AND_MAIL').select('id').limit(1).execute()
    assert result is not None

def test_service_role_can_write_internal():
    """service_role は内部テーブルに書き込める"""
    db = DatabaseClient(use_service_role=True)

    # 既存レコードの更新（新規挿入ではない）
    result = db.client.table('processing_lock').update({
        'is_processing': False
    }).eq('id', 1).execute()

    assert result.data is not None
```

### 4.3 Admin UI動作確認

SQL Migration適用後:

1. **Option A (認証あり)**: ログイン後に正常動作することを確認
2. **Option B (暫定)**: `service_role` で動作することを確認

---

## 5. Migration順序

1. **まず SQL Migration を適用** (`phase2_permission_fix.sql`)
2. **次に Code Changes を適用** (認証機能追加 or 暫定対応)
3. **最後に Verification** (テスト実行)

---

## 6. Rollback手順

問題が発生した場合:

```sql
-- RLSを無効化（緊急時のみ）
ALTER TABLE "Rawdata_FILE_AND_MAIL" DISABLE ROW LEVEL SECURITY;
ALTER TABLE "10_ix_search_index" DISABLE ROW LEVEL SECURITY;
-- ... 他のテーブルも同様

-- anon に全権限を戻す（緊急時のみ）
GRANT ALL ON "Rawdata_FILE_AND_MAIL" TO anon;
-- ... 他のテーブルも同様
```

---

## 7. 注意事項

- **本番環境では Option A (認証あり) を強く推奨**
- **service_role キーは絶対に外部公開しない**
- **Cloud Run デプロイ時は環境変数で認証情報を管理**
