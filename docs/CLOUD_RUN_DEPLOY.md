# Cloud Run Jobs デプロイ手順

## 前提条件
- Google Cloud プロジェクトが作成済み
- gcloud CLI がインストール済み
- Docker がインストール済み

## 1. 初回セットアップ

### 1.1 Google Cloud プロジェクトの設定
```bash
# プロジェクトIDを設定
export PROJECT_ID="your-project-id"
gcloud config set project $PROJECT_ID

# リージョンを設定（東京リージョン推奨）
export REGION="asia-northeast1"
```

### 1.2 必要なAPIを有効化
```bash
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com
```

### 1.3 Artifact Registry リポジトリの作成
```bash
gcloud artifacts repositories create cloud-run-repo \
    --repository-format=docker \
    --location=$REGION \
    --description="Cloud Run Docker repository"
```

## 2. コンテナのビルドとデプロイ

### 2.1 コンテナイメージのビルドとプッシュ
```bash
# プロジェクトルートに移動
cd C:\Users\ookub\document-management-system

# Cloud Build でビルド（推奨）
gcloud builds submit \
    --tag $REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-repo/doc-processor:latest \
    -f Dockerfile.processor \
    .
```

### 2.2 Cloud Run Job の作成
```bash
gcloud run jobs create doc-processor-job \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-repo/doc-processor:latest \
    --region $REGION \
    --tasks 1 \
    --task-timeout 1h \
    --max-retries 3 \
    --memory 2Gi \
    --cpu 2 \
    --set-env-vars "WORKSPACE=all,LIMIT=100"
```

## 3. 実行方法

### 3.1 基本実行
```bash
# デフォルト設定で実行
gcloud run jobs execute doc-processor-job --region $REGION
```

### 3.2 パラメータを上書きして実行
```bash
# 特定のワークスペースを100件処理
gcloud run jobs execute doc-processor-job \
    --region $REGION \
    --update-env-vars "WORKSPACE=gmail,LIMIT=100"

# 全ワークスペースを500件処理
gcloud run jobs execute doc-processor-job \
    --region $REGION \
    --update-env-vars "WORKSPACE=all,LIMIT=500"
```

### 3.3 並列処理（高速化）
```bash
# タスクを5つ並列で実行（500件を5つで分担）
gcloud run jobs execute doc-processor-job \
    --region $REGION \
    --tasks 5 \
    --update-env-vars "LIMIT=500"
```

## 4. スケジュール実行（定期実行）

### 4.1 Cloud Scheduler のセットアップ
```bash
# Cloud Scheduler API を有効化
gcloud services enable cloudscheduler.googleapis.com

# 毎日午前3時に実行（JST = UTC+9なので、UTC 18:00）
gcloud scheduler jobs create http doc-processor-schedule \
    --location=$REGION \
    --schedule="0 18 * * *" \
    --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/doc-processor-job:run" \
    --http-method=POST \
    --oauth-service-account-email="$PROJECT_ID@appspot.gserviceaccount.com"
```

## 5. ログの確認

### 5.1 実行ログの表示
```bash
# 最新の実行ログを表示
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=doc-processor-job" \
    --limit 50 \
    --format "table(timestamp,textPayload)"
```

### 5.2 Cloud Console で確認
https://console.cloud.google.com/run/jobs

## 6. 更新方法

### 6.1 コードを更新してデプロイ
```bash
# 1. コードを修正
# 2. Git にコミット・プッシュ
git add .
git commit -m "Update processor logic"
git push

# 3. 新しいイメージをビルド
gcloud builds submit \
    --tag $REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-repo/doc-processor:latest \
    -f Dockerfile.processor \
    .

# 4. Jobを更新（自動的に新しいイメージを使用）
gcloud run jobs update doc-processor-job \
    --region $REGION \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-repo/doc-processor:latest
```

## 7. コスト最適化のヒント

- **タスク数**: 大量データは並列処理（tasks > 1）で高速化
- **リソース**: 必要最小限のメモリ・CPUに設定
- **タイムアウト**: 処理時間に合わせて適切に設定
- **リトライ**: 失敗時の再試行回数を調整

## トラブルシューティング

### エラー: "Permission denied"
```bash
# サービスアカウントに権限を付与
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$PROJECT_ID@appspot.gserviceaccount.com" \
    --role="roles/run.invoker"
```

### エラー: "Image not found"
```bash
# イメージが正しくプッシュされているか確認
gcloud artifacts docker images list $REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-repo
```
