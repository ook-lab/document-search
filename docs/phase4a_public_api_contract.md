# Phase 4A: Public API データ契約

## 概要

Phase 4A では anon（未認証）ユーザーの公開面を最小化しました。
anon は実テーブルへの直接アクセスが禁止され、制限された RPC 経由でのみデータを取得できます。

**設計判断**: 公開検索は列挙攻撃（enumeration attack）を避けるため、絞り込み機能（workspace/doc_type フィルタ）を意図的に提供しません。フィルタが必要な場合は authenticated 接続で `unified_search_v2` を使用してください。

## 権限モデル

| ロール | 実テーブル SELECT | RPC 実行 | 備考 |
|--------|-------------------|----------|------|
| anon | ❌ 禁止 | ✅ public_search のみ | 公開検索用 |
| authenticated | ✅ RLS 適用 | ✅ すべて | 自分のデータのみ |
| service_role | ✅ RLS バイパス | ✅ すべて | バックエンド専用 |

## 公開 RPC: `public_search`

### 用途
ベクトル類似度検索（doc-search API 等で使用）

### パラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|-----|------|------|
| query_text | TEXT | ✅ | 検索クエリ文字列 |
| query_embedding | vector(1536) | ✅ | クエリの埋め込みベクトル |
| match_threshold | FLOAT | - | 類似度閾値（デフォルト: 0.0） |
| match_count | INT | - | 最大取得件数（デフォルト: 10） |

### 返却フィールド

| フィールド | 型 | 説明 |
|------------|-----|------|
| document_id | UUID | ドキュメントID |
| file_name | TEXT | ファイル名 |
| doc_type | TEXT | ドキュメントタイプ |
| workspace | TEXT | ワークスペース |
| document_date | DATE | ドキュメント日付 |
| summary | TEXT | 要約（200文字以内） |
| similarity | FLOAT | 類似度スコア |
| chunk_preview | TEXT | チャンクプレビュー（100文字以内） |

### 除外フィールド（PII/本文保護）

以下のフィールドは公開 RPC では返却されません：

- `attachment_text` - 本文全体
- `display_sender_email` - メールアドレス
- `display_post_text` - 投稿本文
- `metadata` - 詳細メタデータ
- `owner_id` - 所有者情報
- `chunk_content` - チャンク全文（preview のみ）

## 公開 RPC: `public_search_with_fulltext`

### 用途
ハイブリッド検索（ベクトル + 全文検索）

### パラメータ

| パラメータ | 型 | 必須 | 説明 |
|------------|-----|------|------|
| query_text | TEXT | ✅ | 検索クエリ文字列 |
| query_embedding | vector(1536) | ✅ | クエリの埋め込みベクトル |
| match_threshold | FLOAT | - | 類似度閾値（デフォルト: 0.0） |
| match_count | INT | - | 最大取得件数（デフォルト: 10） |
| vector_weight | FLOAT | - | ベクトル重み（デフォルト: 0.7） |
| fulltext_weight | FLOAT | - | 全文検索重み（デフォルト: 0.3） |

**注意**: `filter_doc_types` と `filter_workspace` は列挙攻撃防止のため削除されました。
公開検索は全データ対象に固定されています。フィルタが必要な場合は authenticated/service_role で `unified_search_v2` を使用してください。

### 返却フィールド

| フィールド | 型 | 説明 |
|------------|-----|------|
| document_id | UUID | ドキュメントID |
| file_name | TEXT | ファイル名 |
| doc_type | TEXT | ドキュメントタイプ |
| workspace | TEXT | ワークスペース |
| document_date | DATE | ドキュメント日付 |
| summary | TEXT | 要約（200文字以内） |
| combined_score | FLOAT | 統合スコア |
| chunk_preview | TEXT | チャンクプレビュー（100文字以内） |
| chunk_type | TEXT | チャンクタイプ |

## 実装詳細

### Migration ファイル
`supabase/migrations/20260117000002_anon_rpc_only.sql`

### クライアント側対応
`shared/common/database/client.py`
- anon 接続時は `all_chunks` 取得をスキップ（直接テーブルアクセス回避）

### テスト
`tests/test_phase4a_anon_rpc_only.py`
- ユニットテスト: アクセス制限ロジック
- 統合テスト: 実際の権限確認（`-m integration`）

## 確認コマンド

```sql
-- anon のテーブル権限を確認
SELECT
    grantee,
    table_name,
    privilege_type
FROM information_schema.table_privileges
WHERE table_schema = 'public'
AND grantee = 'anon';

-- RPC の権限を確認
SELECT
    proname,
    proacl
FROM pg_proc
WHERE proname LIKE 'public_search%';
```

## セキュリティ考慮事項

1. **SECURITY DEFINER**: RPC は service_role 権限で実行（RLS バイパス）
   - 入力は固定パラメータのみ（ユーザー指定のフィルタなし）
   - 返却フィールドは SQL で固定（動的カラム選択なし）
2. **search_path 固定**: SQL インジェクション対策として `SET search_path = public`
3. **フィールド制限**: 返却フィールドを最小限に固定（PII 漏洩防止）
4. **文字数制限**: summary は 200 文字、chunk_preview は 100 文字に制限
5. **列挙攻撃防止**: workspace/doc_type フィルタは公開 RPC から削除
   - 任意の workspace を指定して存在確認ができないようにする
   - フィルタが必要な場合は authenticated 接続で unified_search_v2 を使用
