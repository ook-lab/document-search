#!/bin/bash
# doc-processor デプロイスクリプト（環境変数を設定）
set -e

PROJECT_ID="consummate-yew-479020-u2"
SERVICE_NAME="doc-processor"
REGION="asia-northeast1"

# プロジェクトルートに移動
cd ~/document-management-system

# .envファイルから環境変数を読み込む
if [ -f ".env" ]; then
    set -a
    source <(grep -E '^[A-Z_]+=.*' .env)
    set +a
    echo "✓ 環境変数を読み込みました"
else
    echo "警告: .envファイルが見つかりません"
fi

# Docker ビルド & プッシュ
docker build -t asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest -f services/doc-processor/Dockerfile .
docker push asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest

# Cloud Run にデプロイ（環境変数を設定）
gcloud run deploy ${SERVICE_NAME} \
    --image asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest \
    --region ${REGION} \
    --memory 16Gi \
    --cpu 4 \
    --timeout 3600 \
    --allow-unauthenticated \
    --min-instances 0 \
    --max-instances 10 \
    --concurrency 1 \
    --execution-environment gen1 \
    --set-env-vars "GOOGLE_AI_API_KEY=${GOOGLE_AI_API_KEY:-}" \
    --set-env-vars "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}" \
    --set-env-vars "OPENAI_API_KEY=${OPENAI_API_KEY:-}" \
    --set-env-vars "SUPABASE_URL=${SUPABASE_URL:-}" \
    --set-env-vars "SUPABASE_KEY=${SUPABASE_KEY:-}" \
    --set-env-vars "SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY:-}" \
    --set-env-vars "DOC_PROCESSOR_API_KEY=${DOC_PROCESSOR_API_KEY:-}" \
    --set-env-vars "LOG_LEVEL=${LOG_LEVEL:-INFO}"

echo "✅ ${SERVICE_NAME} deployed!"
