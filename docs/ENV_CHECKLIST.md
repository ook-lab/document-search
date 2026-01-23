# 環境変数チェックリスト

環境差を完全解消するための最終確認リストです。

## Cloud Run サービス × 環境変数マトリクス

| 環境変数 | doc-processor | doc-search | netsuper-search | 必須 |
|---------|:-------------:|:----------:|:---------------:|:----:|
| `SUPABASE_URL` | ✓ | ✓ | ✓ | Yes |
| `SUPABASE_KEY` | ✓ | ✓ | ✓ | Yes |
| `SUPABASE_SERVICE_ROLE_KEY` | ✓ | ✓ | - | Yes* |
| `OPENAI_API_KEY` | ✓ | ✓ | ✓ | Yes |
| `GOOGLE_AI_API_KEY` | ✓ | ✓ | - | No |
| `ANTHROPIC_API_KEY` | ✓ | ✓ | - | No |
| `DOC_PROCESSOR_API_KEY` | ✓ | - | - | Yes** |
| `REQUIRE_AUTH` | ✓ | - | - | No |
| `PORT` | (自動) | (自動) | (自動) | - |

*RLSバイパスが必要な機能で必須
**本番環境では必須

## 確認コマンド

### 1. Cloud Run の環境変数を確認

```bash
# doc-processor
gcloud run services describe doc-processor \
  --region asia-northeast1 \
  --format 'yaml(spec.template.spec.containers[0].env)'

# doc-search (mail-doc-search-system)
gcloud run services describe mail-doc-search-system \
  --region asia-northeast1 \
  --format 'yaml(spec.template.spec.containers[0].env)'

# netsuper-search
gcloud run services describe netsuper-search \
  --region asia-northeast1 \
  --format 'yaml(spec.template.spec.containers[0].env)'
```

### 2. 環境変数の値が一致しているか確認

```bash
# SUPABASE_URL が全サービスで同一か
for svc in doc-processor mail-doc-search-system netsuper-search; do
  echo "$svc:"
  gcloud run services describe $svc \
    --region asia-northeast1 \
    --format 'value(spec.template.spec.containers[0].env)' | grep SUPABASE_URL
done
```

## Supabase 側のチェック

### 必須テーブル

| テーブル名 | 用途 | 必須 |
|-----------|------|:----:|
| `Rawdata_FILE_AND_MAIL` | 文書管理 | Yes |
| `10_ix_search_index` | 検索インデックス | Yes |
| `ops_requests` | 運用リクエスト | Yes |
| `processing_lock` | 処理ロック | Yes |
| `worker_state` | ワーカー状態 | Yes |
| `99_lg_correction_history` | 修正履歴 | No |
| `Rawdata_RECEIPT_items` | 家計簿明細 | No* |
| `Rawdata_RECEIPT_shops` | 家計簿店舗 | No* |

*家計簿機能を使用する場合は必須

### 必須関数

| 関数名 | 用途 |
|--------|------|
| `match_documents` | ベクトル検索（フォールバック） |
| `unified_search_v2` | ハイブリッド検索 |

### 確認SQL（Supabase SQL Editor で実行）

```sql
-- テーブル存在確認
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'Rawdata_FILE_AND_MAIL',
    '10_ix_search_index',
    'ops_requests',
    'processing_lock',
    'worker_state'
  );

-- 関数存在確認
SELECT routine_name
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND routine_name IN ('match_documents', 'unified_search_v2');
```

## 合格判定基準

以下の3条件を全て満たせば「環境差完全解消」と判定できます：

1. **Cloud Run 3サービス**で必須環境変数が全て設定されている
2. **Supabase** で必須テーブル・関数が存在し、主要クエリがエラーなく通る
3. **ローカルと本番**で認証（`DOC_PROCESSOR_API_KEY`）と接続（`BACKEND_URL`）が同じ条件で通る

## 実行が必要なマイグレーション

### 1. worker_state テーブル作成

```bash
# Supabase SQL Editor で実行
cat database/migrations/create_worker_state.sql
```

### 2. match_documents 関数更新

```bash
# Supabase SQL Editor で実行
cat database/add_match_documents_function.sql
```
