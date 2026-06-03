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
$SERVICE_NAME = "quiz-maker"
$REGION = "asia-northeast1"
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$SERVICE_NAME`:latest"

# 3. Restore User Account Authentication
Write-Host "=== 1. GCP Authentication ==="
gcloud config set account ookubo.y@workspace-o.com
gcloud auth list

# 4. Run Cloud Build
Write-Host "=== 2. Cloud Build ==="
gcloud builds submit --region=$REGION --config=services/quiz-maker/cloudbuild.yaml .

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
    --allow-unauthenticated `
    --update-env-vars "SUPABASE_URL=$env:SUPABASE_URL" `
    --update-env-vars "SUPABASE_KEY=$env:SUPABASE_KEY" `
    --update-env-vars "SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY" `
    --update-env-vars "GEMINI_AI_API_KEY=$env:GEMINI_AI_API_KEY" `
    --update-env-vars "GOOGLE_API_KEY=AIzaSyDV-k7lhNbw49ucHpszG50aIPiYX0V6rjA" `
    --update-env-vars "GOOGLE_CLIENT_ID=983922127476-knufv74n37hk0t5k6q08g1cndv9imha4.apps.googleusercontent.com" `
    --update-env-vars "LOG_LEVEL=INFO"

if ($LASTEXITCODE -eq 0) {
    Write-Host "=== Deploy OK ==="
    gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
} else {
    Write-Error "Deploy failed"
    exit 1
}
