# .envファイルから環境変数を読み込み
Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=.+' } | ForEach-Object {
    $parts = $_ -split '=', 2
    $name = $parts[0]
    $value = $parts[1]
    Set-Variable -Name $name -Value $value -Scope Script
}

# Cloud Build実行
$substitutions = "_GOOGLE_AI_API_KEY=$GOOGLE_AI_API_KEY,_ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY,_OPENAI_API_KEY=$OPENAI_API_KEY,_SUPABASE_URL=$SUPABASE_URL,_SUPABASE_KEY=$SUPABASE_KEY,_SUPABASE_SERVICE_ROLE_KEY=$SUPABASE_SERVICE_ROLE_KEY,_DOC_PROCESSOR_API_KEY=$DOC_PROCESSOR_API_KEY"

Write-Host "============================================"
Write-Host "1. Docker イメージをビルド"
Write-Host "============================================"

gcloud builds submit --region=asia-northeast1 --config=cloudbuild.yaml --substitutions=$substitutions

if ($LASTEXITCODE -ne 0) {
    Write-Error "ビルド失敗"
    exit 1
}

Write-Host "============================================"
Write-Host "2. Cloud Run にデプロイ"
Write-Host "============================================"

$PROJECT_ID = "consummate-yew-479020-u2"
$REGION = "asia-northeast1"
$SERVICE_NAME = "doc-processor"
$IMAGE_NAME = "$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$SERVICE_NAME`:latest"
$SERVICE_ACCOUNT = "document-management-system@$PROJECT_ID.iam.gserviceaccount.com"

gcloud run deploy $SERVICE_NAME `
    --image $IMAGE_NAME `
    --region $REGION `
    --platform managed `
    --allow-unauthenticated `
    --service-account $SERVICE_ACCOUNT `
    --timeout 3600 `
    --memory 16Gi `
    --cpu 4 `
    --set-env-vars "GOOGLE_AI_API_KEY=$GOOGLE_AI_API_KEY" `
    --set-env-vars "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" `
    --set-env-vars "OPENAI_API_KEY=$OPENAI_API_KEY" `
    --set-env-vars "SUPABASE_URL=$SUPABASE_URL" `
    --set-env-vars "SUPABASE_KEY=$SUPABASE_KEY" `
    --set-env-vars "SUPABASE_SERVICE_ROLE_KEY=$SUPABASE_SERVICE_ROLE_KEY" `
    --set-env-vars "DOC_PROCESSOR_API_KEY=$DOC_PROCESSOR_API_KEY" `
    --set-env-vars "LOG_LEVEL=INFO"

if ($LASTEXITCODE -eq 0) {
    Write-Host "============================================"
    Write-Host "✅ デプロイ完了"
    Write-Host "============================================"
    gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
}
