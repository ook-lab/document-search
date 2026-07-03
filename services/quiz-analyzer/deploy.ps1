Set-Location "C:\Users\ookub\document-management-system"

# 1. Load environment variables
if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -match '^[A-Z_]+=.+' } | ForEach-Object {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
    }
    Write-Host "env loaded"
}
if (-not (Test-Path ".env")) {
    Write-Warning ".env not found"
}

# 2. Add gcloud SDK to PATH
$env:PATH = "C:\Users\ookub\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin;" + $env:PATH

$PROJECT_ID = "consummate-yew-479020-u2"
$SERVICE_NAME = "quiz-analyzer"
$REGION = "asia-northeast1"
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$SERVICE_NAME`:latest"

# 3. Restore User Account Authentication
Write-Host "=== 1. GCP Authentication ==="
$token = & gcloud auth print-access-token 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Active account credentials expired. Trying service account..."
    & gcloud config set account "document-management-system@consummate-yew-479020-u2.iam.gserviceaccount.com"
}
gcloud auth list

# 4. Run Cloud Build
Write-Host "=== 2. Cloud Build ==="
gcloud builds submit --region=$REGION --config=services/quiz-analyzer/cloudbuild.yaml .

if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed"
    exit 1
}

# 5. Run Cloud Run Deploy with Environment Variables
Write-Host "=== 3. Cloud Run Deploy ==="
gcloud run deploy $SERVICE_NAME `
    --image $IMAGE `
    --region $REGION `
    --memory 512Mi `
    --cpu 1 `
    --timeout 60 `
    --port 5001 `
    --allow-unauthenticated `
    --service-account "document-management-system@$PROJECT_ID.iam.gserviceaccount.com" `
    --set-secrets "SUPABASE_URL=SUPABASE_URL:latest" `
    --set-secrets "SUPABASE_KEY=SUPABASE_KEY:latest" `
    --set-secrets "SUPABASE_SERVICE_ROLE_KEY=SUPABASE_SERVICE_ROLE_KEY:latest" `
    --update-env-vars "LOG_LEVEL=INFO"

if ($LASTEXITCODE -eq 0) {
    Write-Host "=== Deploy OK ==="
    gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
} else {
    Write-Error "Deploy failed"
    exit 1
}
