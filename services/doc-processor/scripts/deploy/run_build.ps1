param(
    [string]$Service = "", # 例: "doc-processor", "doc-search", "doc-review", "calendar-register", "kakeibo", "daily-report"
    [switch]$All           # 明示時のみ cloudbuild.yaml（全体ビルド）を実行
)

Set-Location "C:\Users\ookub\document-management-system"

# .env から環境変数を読み込み
$envContent = Get-Content .env
foreach ($line in $envContent) {
    if ($line -match "^([^#][^=]*)=(.*)$") {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
        Set-Item -Path "Env:$key" -Value $value
    }
}

$gcloudPath = "C:\Users\ookub\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin"
if ($env:PATH -notlike "*$gcloudPath*") {
    $env:PATH += ";$gcloudPath"
}

# .env ファイルがあれば読み込む
if (Test-Path ".env") {
    Write-Host ".env から環境変数を読み込んでいます..."
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^\s*([^#\s=+]+)\s*=\s*(.*)") {
            $name = $Matches[1].Trim()
            $value = $Matches[2].Trim().Trim("'").Trim('"')
            if ($name -and $value) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

$subs = "_GOOGLE_AI_API_KEY=$env:GOOGLE_AI_API_KEY,_ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY,_OPENAI_API_KEY=$env:OPENAI_API_KEY,_SUPABASE_URL=$env:SUPABASE_URL,_SUPABASE_KEY=$env:SUPABASE_KEY,_SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY,_DOC_PROCESSOR_API_KEY=$env:DOC_PROCESSOR_API_KEY,_CALENDAR_SYNC_USER_ID=$env:CALENDAR_SYNC_USER_ID,_GOOGLE_CLIENT_ID=$env:GOOGLE_CLIENT_ID,_GOOGLE_CLIENT_SECRET=$env:GOOGLE_CLIENT_SECRET,_NEXTAUTH_URL=$env:NEXTAUTH_URL,_NEXTAUTH_SECRET=$env:NEXTAUTH_SECRET,_DEFAULT_OWNER_ID=$env:DEFAULT_OWNER_ID,_WASEDA_PDF_FOLDER_ID=$env:WASEDA_PDF_FOLDER_ID,_GOOGLE_SERVICE_ACCOUNT_JSON=$env:GOOGLE_SERVICE_ACCOUNT_JSON,_WASEDA_LOGIN_ID=$env:WASEDA_LOGIN_ID,_WASEDA_PASSWORD=$env:WASEDA_PASSWORD"

# my-calendar-app, portal-app は services/ 配下ではなくルート直下
$rootServices = @("my-calendar-app", "portal-app")

if ($Service -eq "" -and -not $All) {
    Write-Host "エラー: 個別ビルド対象を指定してください。"
    Write-Host "例) .\scripts\deploy\run_build.ps1 -Service doc-processor"
    Write-Host "全体ビルドが必要な場合のみ -All を付けてください。"
    exit 1
} elseif ($All) {
    $config = "cloudbuild.yaml"
    Write-Host "全体ビルドを実行します（-All 指定）..."
} elseif ($rootServices -contains $Service) {
    $config = "$Service/cloudbuild.yaml"
    Write-Host "$Service のみをビルド・デプロイします..."
} else {
    $config = "services/$Service/cloudbuild.yaml"
    Write-Host "$Service のみをビルド・デプロイします..."
}

& gcloud builds submit --region=asia-northeast1 --config=$config --substitutions="$subs"

Write-Host "Done!"
