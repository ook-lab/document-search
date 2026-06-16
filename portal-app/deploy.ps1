Set-Location "C:\Users\ookub\document-management-system"

# Automatically add gcloud SDK path if not on PATH
$gcloudPath = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin"
if (Test-Path $gcloudPath) {
    $env:Path += ";$gcloudPath"
}

$REGION = "asia-northeast1"

Write-Host "=== Submitting Cloud Build for Portal App ===" -ForegroundColor Cyan
gcloud builds submit --region=$REGION --config=portal-app/cloudbuild.yaml .

if ($LASTEXITCODE -ne 0) {
    Write-Error "Portal Deploy failed"
    exit 1
}

Write-Host "=== Portal Deploy Successful ===" -ForegroundColor Green
