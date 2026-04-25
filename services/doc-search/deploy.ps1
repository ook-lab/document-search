Set-Location "C:\Users\ookub\document-management-system"

if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -match '^[A-Z_]+=.+' } | ForEach-Object {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
    }
    Write-Host "env loaded"
} else {
    Write-Warning ".env not found"
}

$PROJECT_ID = "consummate-yew-479020-u2"
$SERVICE_NAME = "mail-doc-search-system"
$IMAGE_NAME = "doc-search"
$REGION = "asia-northeast1"
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$IMAGE_NAME`:latest"

Write-Host "=== 1. Build ==="
gcloud builds submit --region=$REGION --config=services/doc-search/cloudbuild.yaml .

if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed"
    exit 1
}

Write-Host "=== 2. Deploy ==="
gcloud run deploy $SERVICE_NAME `
    --image $IMAGE `
    --region $REGION `
    --memory 4Gi `
    --cpu 2 `
    --timeout 300 `
    --allow-unauthenticated `
    --update-env-vars "GOOGLE_AI_API_KEY=$env:GOOGLE_AI_API_KEY" `
    --update-env-vars "ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY" `
    --update-env-vars "OPENAI_API_KEY=$env:OPENAI_API_KEY" `
    --update-env-vars "SUPABASE_URL=$env:SUPABASE_URL" `
    --update-env-vars "SUPABASE_KEY=$env:SUPABASE_KEY" `
    --update-env-vars "SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY" `
    --update-env-vars "LOG_LEVEL=INFO" `
    --update-env-vars "RERANK_ENABLED=true"

if ($LASTEXITCODE -eq 0) {
    Write-Host "=== Deploy OK ==="
    gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
} else {
    Write-Error "Deploy failed"
    exit 1
}
