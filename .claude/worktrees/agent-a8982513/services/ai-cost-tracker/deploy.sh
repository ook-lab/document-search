#!/bin/bash
set -e

PROJECT_ID="${GCP_PROJECT_ID:-consummate-yew-479020-u2}"
SERVICE_NAME="ai-cost-tracker"
REGION="asia-northeast1"

cd ~/document-management-system

if [ -f ".env" ]; then
    set -a
    source <(grep -E '^[A-Z_]+=.*' .env)
    set +a
    echo "✓ 環境変数を読み込みました"
else
    echo "警告: .envファイルが見つかりません"
fi

docker build -t asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest -f services/ai-cost-tracker/Dockerfile .
docker push asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest

gcloud run deploy ${SERVICE_NAME} \
    --image asia-northeast1-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest \
    --region ${REGION} \
    --memory 512Mi \
    --cpu 1 \
    --timeout 60 \
    --allow-unauthenticated \
    --set-env-vars "SUPABASE_URL=${SUPABASE_URL:-}" \
    --set-env-vars "SUPABASE_KEY=${SUPABASE_KEY:-}" \
    --set-env-vars "SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY:-}" \
    --set-env-vars "LOG_LEVEL=${LOG_LEVEL:-INFO}"

echo "✅ ${SERVICE_NAME} deployed!"
