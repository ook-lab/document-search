@echo off
cd /d "%~dp0"
echo Deploying PDF-Splitter via Cloud Build...
gcloud builds submit --config=cloudbuild.yaml .
pause
