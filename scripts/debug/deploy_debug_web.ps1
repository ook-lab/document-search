# Debug Pipeline Web UI - Cloud Run デプロイスクリプト
# 使い方: .\scripts\debug\deploy_debug_web.ps1

# プロジェクトルートに移動
$PROJECT_ROOT = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
Push-Location $PROJECT_ROOT

try {
    # .envファイルから環境変数を読み込み
    Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=.+' } | ForEach-Object {
        $parts = $_ -split '=', 2
        $name = $parts[0]
        $value = $parts[1]
        Set-Variable -Name $name -Value $value -Scope Script
    }

    $PROJECT_ID = "consummate-yew-479020-u2"
    $REGION = "asia-northeast1"
    $SERVICE_NAME = "debug-pipeline"
    $IMAGE_NAME = "$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$SERVICE_NAME`:latest"
    $SERVICE_ACCOUNT = "document-management-system@$PROJECT_ID.iam.gserviceaccount.com"

    Write-Host "============================================"
    Write-Host "1. Docker イメージをビルド"
    Write-Host "============================================"

    # ビルドコンテキストはプロジェクトルート（shared/ をコピーするため）
    gcloud builds submit `
        --region=$REGION `
        --tag $IMAGE_NAME `
        --dockerfile scripts/debug/Dockerfile `
        --timeout=1800

    if ($LASTEXITCODE -ne 0) {
        Write-Error "ビルド失敗"
        exit 1
    }

    Write-Host "============================================"
    Write-Host "2. Cloud Run にデプロイ"
    Write-Host "============================================"

    gcloud run deploy $SERVICE_NAME `
        --image $IMAGE_NAME `
        --region $REGION `
        --platform managed `
        --allow-unauthenticated `
        --service-account $SERVICE_ACCOUNT `
        --timeout 3600 `
        --memory 8Gi `
        --cpu 2 `
        --set-env-vars "GOOGLE_AI_API_KEY=$GOOGLE_AI_API_KEY" `
        --set-env-vars "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" `
        --set-env-vars "OPENAI_API_KEY=$OPENAI_API_KEY" `
        --set-env-vars "DEBUG_OUTPUT_DIR=/tmp/debug_output"

    if ($LASTEXITCODE -eq 0) {
        Write-Host "============================================"
        Write-Host "デプロイ完了"
        Write-Host "============================================"
        gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
    }
} finally {
    Pop-Location
}
