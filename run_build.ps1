Set-Location "C:\Users\ookub\document-management-system"

# Load .env manually
$envContent = Get-Content .env
foreach ($line in $envContent) {
    if ($line -match "^([^#][^=]*)=(.*)$") {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
        Set-Item -Path "Env:$key" -Value $value
    }
}

Write-Host "SUPABASE_URL = $env:SUPABASE_URL"

# Build command
$subs = "_GOOGLE_AI_API_KEY=$env:GOOGLE_AI_API_KEY,_ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY,_OPENAI_API_KEY=$env:OPENAI_API_KEY,_SUPABASE_URL=$env:SUPABASE_URL,_SUPABASE_KEY=$env:SUPABASE_KEY,_SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY"

Write-Host "Running gcloud builds submit..."
& gcloud builds submit --region=asia-northeast1 --config=cloudbuild.yaml --substitutions="$subs"

Write-Host "Build complete. Now deploying..."
& gcloud run deploy doc-processor --image asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor:latest --region asia-northeast1 --allow-unauthenticated --service-account document-management-system@consummate-yew-479020-u2.iam.gserviceaccount.com --timeout 3600 --memory 16Gi --cpu 4

Write-Host "Done!"
