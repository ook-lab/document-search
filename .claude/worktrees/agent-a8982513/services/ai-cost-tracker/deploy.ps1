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
$SERVICE_NAME = "ai-cost-tracker"
$REGION = "asia-northeast1"
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$SERVICE_NAME`:latest"

Write-Host "=== 1. Build ==="
gcloud builds submit --region=$REGION --config=services/ai-cost-tracker/cloudbuild.yaml .

if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed"
    exit 1
}

Write-Host "=== 2. Deploy ==="
gcloud run deploy $SERVICE_NAME `
    --image $IMAGE `
    --region $REGION `
    --memory 512Mi `
    --cpu 1 `
    --timeout 60 `
    --allow-unauthenticated `
    --set-env-vars "SUPABASE_URL=$env:SUPABASE_URL" `
    --set-env-vars "SUPABASE_KEY=$env:SUPABASE_KEY" `
    --set-env-vars "SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY" `
    --set-env-vars "LOG_LEVEL=INFO"

if ($LASTEXITCODE -eq 0) {
    Write-Host "=== Deploy OK ==="
    gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
} else {
    Write-Error "Deploy failed"
    exit 1
}
