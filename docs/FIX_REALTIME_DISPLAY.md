# リアルタイム表示修正手順

## 問題の原因
`processing_lock`テーブルに必要なカラムが存在しないため、進捗情報が保存・表示されない。

## 解決手順

### 1. Supabaseでマイグレーションを実行

1. Supabaseダッシュボードを開く
2. **SQL Editor**に移動
3. 以下のSQLファイルの内容をコピー&ペーストして実行:
   ```
   database/migrations/add_processing_lock_columns.sql
   ```

### 2. 実行確認

SQLエディタで以下を実行して、カラムが追加されたことを確認:

```sql
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'processing_lock'
ORDER BY ordinal_position;
```

以下のカラムが表示されればOK:
- current_index (integer)
- total_count (integer)
- current_file (text)
- success_count (integer)
- error_count (integer)
- logs (jsonb)
- cpu_percent (real)
- memory_percent (real)
- memory_used_gb (real)
- memory_total_gb (real)
- throttle_delay (real)
- adjustment_count (integer)
- max_parallel (integer)
- current_workers (integer)

### 3. Cloud Runに再デプロイ

マイグレーション実行後、Cloud Runに再デプロイ:

```powershell
cd C:\Users\ookub\document-management-system

# 環境変数読み込み
Get-Content .env | ForEach-Object { if ($_ -match "^([^=]+)=(.*)$") { Set-Item -Path "Env:$($matches[1])" -Value $matches[2] } }

# ビルド
gcloud builds submit --region=asia-northeast1 --config=cloudbuild.yaml --substitutions="_GOOGLE_AI_API_KEY=$env:GOOGLE_AI_API_KEY,_ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY,_OPENAI_API_KEY=$env:OPENAI_API_KEY,_SUPABASE_URL=$env:SUPABASE_URL,_SUPABASE_KEY=$env:SUPABASE_KEY,_SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY"

# デプロイ
gcloud run deploy doc-processor --image asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor:latest --region asia-northeast1 --allow-unauthenticated --service-account document-management-system@consummate-yew-479020-u2.iam.gserviceaccount.com --timeout 3600 --memory 16Gi --cpu 4
```

### 4. 動作確認

ブラウザでCloud RunのURLにアクセスし、処理を開始。以下が表示されることを確認:
- 処理中のファイル名
- 進捗状況（X / Y件）
- 成功件数・エラー件数
- CPU・メモリ使用率
- 並列処理数

これらが全て「0」や「-」ではなく、実際の値が表示されればOK。
