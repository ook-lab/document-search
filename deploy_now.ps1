# Deploy to Cloud Run
Write-Host "Starting deployment..."
Set-Location C:\Users\ookub\document-management-system

# Load .env
Write-Host "Loading .env..."
Get-Content .env | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") {
        Set-Item -Path "Env:$($matches[1])" -Value $matches[2]
    }
}

Write-Host "SUPABASE_URL is: $env:SUPABASE_URL"

# Build
Write-Host "Building with Cloud Build..."
$result = gcloud builds submit --region=asia-northeast1 --config=cloudbuild.yaml --substitutions="_GOOGLE_AI_API_KEY=$env:GOOGLE_AI_API_KEY,_ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY,_OPENAI_API_KEY=$env:OPENAI_API_KEY,_SUPABASE_URL=$env:SUPABASE_URL,_SUPABASE_KEY=$env:SUPABASE_KEY,_SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY" 2>&1
Write-Host $result

# Deploy
Write-Host "Deploying to Cloud Run..."
$result2 = gcloud run deploy doc-processor --image asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor:latest --region asia-northeast1 --allow-unauthenticated --service-account document-management-system@consummate-yew-479020-u2.iam.gserviceaccount.com --timeout 3600 --memory 16Gi --cpu 4 2>&1
Write-Host $result2

Write-Host "Done!"
