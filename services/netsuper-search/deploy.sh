#!/bin/bash

# ネットスーパー横断検索アプリをCloud Runにデプロイ

set -e

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"

# プロジェクトルートの.envファイルから環境変数を読み込む
ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE" 2>/dev/null || true
    set +a
else
    echo "エラー: .envファイルが見つかりません ($ENV_FILE)"
    exit 1
fi

# 環境変数の確認（デバッグ用）
echo "SUPABASE_URL長: ${#SUPABASE_URL}文字"
echo "SUPABASE_KEY長: ${#SUPABASE_KEY}文字"
echo "SUPABASE_SERVICE_ROLE_KEY長: ${#SUPABASE_SERVICE_ROLE_KEY}文字"
echo "OPENAI_API_KEY長: ${#OPENAI_API_KEY}文字"

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ] || [ -z "$OPENAI_API_KEY" ]; then
    echo "エラー: SUPABASE_URL, SUPABASE_KEY, または OPENAI_API_KEY が設定されていません"
    exit 1
fi

if [ -z "$SUPABASE_SERVICE_ROLE_KEY" ]; then
    echo "警告: SUPABASE_SERVICE_ROLE_KEY が設定されていません（RLSバイパスが必要な場合は設定してください）"
fi

# GCPプロジェクトID（環境変数または デフォルト値）
PROJECT_ID="${GCP_PROJECT_ID:-consummate-yew-479020-u2}"
SERVICE_NAME="netsuper-search"
REGION="asia-northeast1"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "=================================="
echo "ネットスーパー検索アプリデプロイ"
echo "=================================="

# Cloud Buildでビルド＆デプロイ（ローカルにDockerは不要）
echo ""
echo "Cloud Buildでビルド＆デプロイ中..."
gcloud run deploy ${SERVICE_NAME} \
  --source . \
  --platform managed \
  --region ${REGION} \
  --project ${PROJECT_ID} \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 10 \
  --set-env-vars "SUPABASE_URL=${SUPABASE_URL},SUPABASE_KEY=${SUPABASE_KEY},SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY},OPENAI_API_KEY=${OPENAI_API_KEY}"

echo ""
echo "=================================="
echo "✅ デプロイ完了！"
echo "=================================="
echo ""
echo "アプリURL:"
gcloud run services describe ${SERVICE_NAME} \
  --region ${REGION} \
  --project ${PROJECT_ID} \
  --format 'value(status.url)'
