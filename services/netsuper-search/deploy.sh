#!/bin/bash

# ネットスーパー横断検索アプリをCloud Runにデプロイ

set -e

# プロジェクトルートの.envファイルから必要な環境変数を読み込む
if [ -f "../.env" ]; then
    # 空白を削除して変数を抽出（改行文字も削除）
    export SUPABASE_URL=$(grep "^SUPABASE_URL" ../.env | sed 's/.*=[ ]*//' | tr -d ' \r\n')
    export SUPABASE_KEY=$(grep "^SUPABASE_KEY" ../.env | sed 's/.*=[ ]*//' | tr -d ' \r\n')
    export OPENAI_API_KEY=$(grep "^OPENAI_API_KEY" ../.env | sed 's/.*=[ ]*//' | tr -d ' \r\n')
else
    echo "エラー: ../.env ファイルが見つかりません"
    exit 1
fi

# 環境変数の確認（デバッグ用）
echo "SUPABASE_URL長: ${#SUPABASE_URL}文字"
echo "SUPABASE_KEY長: ${#SUPABASE_KEY}文字"
echo "OPENAI_API_KEY長: ${#OPENAI_API_KEY}文字"
echo "SUPABASE_URL: ${SUPABASE_URL:0:40}..."
echo "SUPABASE_KEY: ${SUPABASE_KEY:0:40}..."
echo "OPENAI_API_KEY: ${OPENAI_API_KEY:0:40}..."

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ] || [ -z "$OPENAI_API_KEY" ]; then
    echo "エラー: SUPABASE_URL, SUPABASE_KEY, または OPENAI_API_KEY が設定されていません"
    exit 1
fi

PROJECT_ID="consummate-yew-479020-u2"
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
  --set-env-vars "SUPABASE_URL=${SUPABASE_URL},SUPABASE_KEY=${SUPABASE_KEY},OPENAI_API_KEY=${OPENAI_API_KEY}"

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
