#!/bin/bash
# Cloud Runへのデプロイスクリプト（doc-processor専用）

set -e  # エラーが発生したら終了

echo "================================"
echo "doc-processor のデプロイを開始"
echo "================================"

# スクリプトのディレクトリを取得
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# .envファイルから環境変数を読み込む
ENV_FILE="$PROJECT_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "エラー: .envファイルが見つかりません ($ENV_FILE)"
    exit 1
fi

# .envファイルから必要な環境変数を抽出
echo "環境変数を読み込んでいます..."
set -a  # 自動的にexport
source "$ENV_FILE" 2>/dev/null || true  # エラーを無視
set +a

# 必須環境変数のチェック
if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ]; then
    echo "エラー: SUPABASE_URLまたはSUPABASE_KEYが設定されていません"
    exit 1
fi

echo "✓ 環境変数の読み込み完了"

# Cloud Runにデプロイ
echo ""
echo "Cloud Runにデプロイしています..."
# プロジェクトルートに移動（Dockerfileがプロジェクトルートからのパスを使用するため）
cd "$PROJECT_ROOT"
gcloud run deploy doc-processor \
  --source . \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --timeout 3600 \
  --memory 4Gi \
  --cpu 2 \
  --set-env-vars "SUPABASE_URL=$SUPABASE_URL" \
  --set-env-vars "SUPABASE_KEY=$SUPABASE_KEY" \
  --set-env-vars "GOOGLE_AI_API_KEY=$GOOGLE_AI_API_KEY" \
  --set-env-vars "OPENAI_API_KEY=$OPENAI_API_KEY" \
  --set-env-vars "LOG_LEVEL=${LOG_LEVEL:-INFO}" \
  --set-env-vars "RERANK_ENABLED=${RERANK_ENABLED:-true}"

echo ""
echo "================================"
echo "✓ デプロイが完了しました！"
echo "================================"
echo ""
echo "確認コマンド:"
echo "  curl https://doc-processor-983922127476.asia-northeast1.run.app/processing"
echo "  curl https://doc-processor-983922127476.asia-northeast1.run.app/api/health"
echo ""
