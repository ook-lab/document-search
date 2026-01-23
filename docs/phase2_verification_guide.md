# Phase 2: Admin UI 認証 検証ガイド

## 概要

Admin UI から `service_role` を完全排除し、`authenticated` + RLS で動作させる実装の検証手順です。

## 前提条件

1. Supabase プロジェクトで認証ユーザーが作成済み
2. `SUPABASE_URL` と `SUPABASE_KEY` (anon key) が環境変数に設定済み

---

## Step 1: Supabase Auth ユーザー作成

Supabase ダッシュボードで管理者ユーザーを作成します。

1. Supabase ダッシュボード → Authentication → Users
2. 「Add User」→ 「Create New User」
3. メールアドレスとパスワードを設定
4. 「Auto Confirm User」をチェック（メール確認をスキップ）

---

## Step 2: RLS ポリシー適用

```bash
# Supabase SQL Editor で実行
# ファイル: migrations/phase2_authenticated_minimal.sql
```

### 適用される権限サマリー

| テーブル | anon | authenticated | service_role |
|----------|------|---------------|--------------|
| Rawdata_FILE_AND_MAIL | SELECT | SELECT,UPDATE,DELETE | ALL |
| 10_ix_search_index | SELECT | SELECT,INSERT,DELETE | ALL |
| Rawdata_RECEIPT_shops | - | SELECT | ALL |
| Rawdata_RECEIPT_items | - | SELECT,UPDATE | ALL |
| 99_lg_correction_history | - | SELECT,INSERT | ALL |
| 99_lg_image_proc_log | - | SELECT | ALL |
| MASTER_Rules_transaction_dict | - | SELECT,INSERT,UPDATE | ALL |
| MASTER_Categories_* | - | SELECT | ALL |

---

## Step 3: ローカル検証

### 3.1 Document Review App

```bash
cd document-management-system
streamlit run frontend/document_review_app.py
```

**検証項目:**

1. **未ログイン状態**
   - 「管理機能を使用するにはログインが必要です」と表示される
   - データにアクセスできない

2. **ログイン後**
   - サイドバーにログインフォームが表示される
   - Supabase Auth のメール/パスワードでログイン
   - ログイン成功後、ドキュメント一覧が表示される

3. **操作確認**
   - ドキュメントの閲覧（SELECT）
   - メタデータの編集・保存（UPDATE）
   - ドキュメントの削除（DELETE）
   - 修正履歴の記録（INSERT on 99_lg_correction_history）

### 3.2 家計簿レビューUI

```bash
streamlit run shared/kakeibo/review_ui.py
```

**検証項目:**

1. **未ログイン状態**
   - 「管理機能を使用するにはログインが必要です」と表示される

2. **ログイン後**
   - レシート一覧が表示される
   - 商品分類の編集が可能
   - 辞書への保存が可能

---

## Step 4: 権限拒否の確認

### 4.1 anon での書き込み拒否

```python
# テストスクリプト
from shared.common.database.client import DatabaseClient

# anon key で接続
db = DatabaseClient()

# 書き込みを試みる（失敗するはず）
try:
    db.client.table('Rawdata_FILE_AND_MAIL').update({
        'review_status': 'test'
    }).eq('id', 'some-id').execute()
    print("ERROR: anon で UPDATE できてしまった")
except Exception as e:
    print(f"OK: anon での UPDATE が拒否された: {e}")
```

### 4.2 authenticated での読み取り専用テーブル確認

```python
# ログイン後の access_token を使用
db = DatabaseClient(access_token=access_token)

# MASTER_Categories_product への INSERT を試みる（失敗するはず）
try:
    db.client.table('MASTER_Categories_product').insert({
        'name': 'test'
    }).execute()
    print("ERROR: authenticated で MASTER への INSERT ができてしまった")
except Exception as e:
    print(f"OK: MASTER への INSERT が拒否された: {e}")
```

---

## Step 5: Worker テーブルのアクセス拒否確認

```python
# anon または authenticated で Worker テーブルにアクセス
db = DatabaseClient()  # または access_token 付き

try:
    db.client.table('processing_lock').select('*').execute()
    print("ERROR: Worker テーブルにアクセスできてしまった")
except Exception as e:
    print(f"OK: Worker テーブルへのアクセスが拒否された: {e}")
```

---

## トラブルシューティング

### 「Invalid login credentials」エラー

- Supabase ダッシュボードでユーザーが「Auto Confirm」されているか確認
- メールアドレスとパスワードが正しいか確認

### 「permission denied for table」エラー

- RLS ポリシーが適用されているか確認
- GRANT 文が実行されているか確認

### 「JWT expired」エラー

- セッションがタイムアウトしている
- 再ログインが必要

---

## ファイル一覧

| ファイル | 説明 |
|----------|------|
| `shared/common/auth/admin_auth.py` | 認証モジュール |
| `shared/common/database/client.py` | DatabaseClient（access_token 対応） |
| `migrations/phase2_authenticated_minimal.sql` | RLS ポリシー（最小権限） |
| `frontend/document_review_app.py` | Document Review App（認証対応） |
| `shared/kakeibo/review_ui.py` | 家計簿レビューUI（認証対応） |
