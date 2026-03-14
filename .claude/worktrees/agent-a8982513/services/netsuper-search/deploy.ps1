# ネットスーパー横断検索アプリをCloud Runにデプロイ (PowerShell版)

$ErrorActionPreference = "Stop"

# プロジェクトルートの.envファイルから必要な環境変数を読み込む
$envPath = "..\\.env"
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        if ($_ -match '^SUPABASE_URL\s*=\s*(.+)$') {
            $env:SUPABASE_URL = $matches[1].Trim()
        }
        if ($_ -match '^SUPABASE_KEY\s*=\s*(.+)$') {
            $env:SUPABASE_KEY = $matches[1].Trim()
        }
        if ($_ -match '^SUPABASE_SERVICE_ROLE_KEY\s*=\s*(.+)$') {
            $env:SUPABASE_SERVICE_ROLE_KEY = $matches[1].Trim()
        }
        if ($_ -match '^OPENAI_API_KEY\s*=\s*(.+)$') {
            $env:OPENAI_API_KEY = $matches[1].Trim()
        }
    }
} else {
    Write-Host "エラー: .env ファイルが見つかりません" -ForegroundColor Red
    exit 1
}

# 環境変数の確認（デバッグ用）
Write-Host "SUPABASE_URL長: $($env:SUPABASE_URL.Length)文字"
Write-Host "SUPABASE_KEY長: $($env:SUPABASE_KEY.Length)文字"
Write-Host "OPENAI_API_KEY長: $($env:OPENAI_API_KEY.Length)文字"

if ([string]::IsNullOrEmpty($env:SUPABASE_URL) -or
    [string]::IsNullOrEmpty($env:SUPABASE_KEY) -or
    [string]::IsNullOrEmpty($env:OPENAI_API_KEY)) {
    Write-Host "エラー: SUPABASE_URL, SUPABASE_KEY, または OPENAI_API_KEY が設定されていません" -ForegroundColor Red
    exit 1
}

$PROJECT_ID = "consummate-yew-479020-u2"
$SERVICE_NAME = "netsuper-search"
$REGION = "asia-northeast1"

Write-Host "==================================" -ForegroundColor Green
Write-Host "ネットスーパー検索アプリデプロイ" -ForegroundColor Green
Write-Host "==================================" -ForegroundColor Green
Write-Host ""

# Cloud Buildでビルド＆デプロイ
Write-Host "Cloud Buildでビルド＆デプロイ中..." -ForegroundColor Yellow

gcloud run deploy $SERVICE_NAME `
  --source . `
  --platform managed `
  --region $REGION `
  --project $PROJECT_ID `
  --allow-unauthenticated `
  --memory 512Mi `
  --cpu 1 `
  --max-instances 10 `
  --set-env-vars "SUPABASE_URL=$($env:SUPABASE_URL),SUPABASE_KEY=$($env:SUPABASE_KEY),SUPABASE_SERVICE_ROLE_KEY=$($env:SUPABASE_SERVICE_ROLE_KEY),OPENAI_API_KEY=$($env:OPENAI_API_KEY)"

Write-Host ""
Write-Host "==================================" -ForegroundColor Green
Write-Host "✅ デプロイ完了！" -ForegroundColor Green
Write-Host "==================================" -ForegroundColor Green
Write-Host ""
Write-Host "アプリURL:"

gcloud run services describe $SERVICE_NAME `
  --region $REGION `
  --project $PROJECT_ID `
  --format 'value(status.url)'
