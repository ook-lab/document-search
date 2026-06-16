Set-Location "C:\Users\ookub\document-management-system"

# Automatically add gcloud SDK path if not on PATH
$gcloudPath = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin"
if (Test-Path $gcloudPath) {
    $env:Path += ";$gcloudPath"
}

$PROJECT_ID = "consummate-yew-479020-u2"
$SERVICE_NAME = "pdf-splitter"
$REGION = "asia-northeast1"

Write-Host "=== 1. Submitting Cloud Build ===" -ForegroundColor Cyan
gcloud builds submit --region=$REGION --config=services/pdf-splitter/cloudbuild.yaml .

if ($LASTEXITCODE -ne 0) {
    Write-Error "Build and Deploy failed"
    exit 1
}

Write-Host "=== Deploy Successful ===" -ForegroundColor Green
gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
