# Requires: gcloud auth login (user account with Cloud Build Editor or equivalent)
# Creates the regional GitHub trigger for youtube-search-links (idempotent: fails if name exists).
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "trigger_youtube_search_links.json"
if (-not (Test-Path $src)) {
    Write-Error "Missing: $src"
}
& gcloud beta builds triggers import --region=asia-northeast1 --source=$src
Write-Host "Done. Verify: gcloud builds triggers describe youtube-search-links --region=asia-northeast1"
