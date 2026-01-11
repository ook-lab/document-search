#!/bin/bash

# ===================================================================
# doc-processor Cloud Run デプロイスクリプト
# ===================================================================

set -e  # エラー時に即終了

# プロジェクト設定
PROJECT_ID="consummate-yew-479020-u2"
REGION="asia-northeast1"
SERVICE_NAME="doc-processor"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:latest"
SERVICE_ACCOUNT="document-management-system@${PROJECT_ID}.iam.gserviceaccount.com"

# .envファイルから環境変数を読み込み
if [ ! -f ".env" ]; then
    echo "エラー: .envファイルが見つかりません"
    exit 1
fi

# .envファイルから環境変数を抽出（コメントと空行を除外）
export $(grep -v '^#' .env | grep -v '^$' | xargs)

echo "============================================"
echo "1. Docker イメージをビルド"
echo "============================================"
gcloud builds submit \
    --region=${REGION} \
    --config=cloudbuild.yaml \
    --substitutions=_GOOGLE_AI_API_KEY="${GOOGLE_AI_API_KEY}",_ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}",_OPENAI_API_KEY="${OPENAI_API_KEY}",_SUPABASE_URL="${SUPABASE_URL}",_SUPABASE_KEY="${SUPABASE_KEY}",_SUPABASE_SERVICE_ROLE_KEY="${SUPABASE_SERVICE_ROLE_KEY}"

echo "============================================"
echo "2. Cloud Run にデプロイ"
echo "============================================"
gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_NAME} \
    --region ${REGION} \
    --platform managed \
    --allow-unauthenticated \
    --service-account ${SERVICE_ACCOUNT} \
    --timeout 3600 \
    --memory 16Gi \
    --cpu 4 \
    --set-env-vars "GOOGLE_AI_API_KEY=${GOOGLE_AI_API_KEY}" \
    --set-env-vars "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" \
    --set-env-vars "OPENAI_API_KEY=${OPENAI_API_KEY}" \
    --set-env-vars "SUPABASE_URL=${SUPABASE_URL}" \
    --set-env-vars "SUPABASE_KEY=${SUPABASE_KEY}" \
    --set-env-vars "SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}"

echo "============================================"
echo "✅ デプロイ完了"
echo "============================================"
gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format='value(status.url)'
