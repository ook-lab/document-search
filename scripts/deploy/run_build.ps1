param(
    [string]$Service = ""  # 例: "doc-processor", "doc-search", "doc-review", "calendar-register", "kakeibo"
                           # 省略時は全サービスをビルド
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

$subs = "_GOOGLE_AI_API_KEY=$env:GOOGLE_AI_API_KEY,_ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY,_OPENAI_API_KEY=$env:OPENAI_API_KEY,_SUPABASE_URL=$env:SUPABASE_URL,_SUPABASE_KEY=$env:SUPABASE_KEY,_SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY,_DOC_PROCESSOR_API_KEY=$env:DOC_PROCESSOR_API_KEY,_CALENDAR_SYNC_USER_ID=$env:CALENDAR_SYNC_USER_ID"

if ($Service -eq "") {
    $config = "cloudbuild.yaml"
    Write-Host "全サービスをビルド・デプロイします..."
} else {
    $config = "services/$Service/cloudbuild.yaml"
    Write-Host "$Service のみをビルド・デプロイします..."
}

& gcloud builds submit --region=asia-northeast1 --config=$config --substitutions="$subs"

Write-Host "Done!"
