# deploy.ps1
# スクリプトがあるディレクトリ（pdf-splitter）をカレントディレクトリとして実行します
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "Cloud Buildを使用してPDF-Splitterをデプロイしています..."
# ディレクトリ自体をコンテキストとしてCloud Buildに送信します
gcloud builds submit --config=cloudbuild.yaml .
