#!/usr/bin/env pwsh
<#
.SYNOPSIS
    data-ingestion サービスの Cloud Scheduler トリガーを作成/更新する

.DESCRIPTION
    各データソースに対して Cloud Scheduler ジョブを作成し、
    指定のスケジュールで data-ingestion サービスを自動実行します。

.EXAMPLE
    & .\scripts\deploy\create_schedulers.ps1
#>

Set-Location "C:\Users\ookub\document-management-system"

# ===== 設定 =====
$PROJECT_ID    = "consummate-yew-479020-u2"
$REGION        = "asia-northeast1"
$SERVICE_URL   = "https://data-ingestion-983922127476.asia-northeast1.run.app"
$TIME_ZONE     = "Asia/Tokyo"
# Cloud Run 呼び出し用のサービスアカウント（Cloud Run Invoker 権限が必要）
$SERVICE_ACCOUNT = "983922127476-compute@developer.gserviceaccount.com"

# gcloud PATH
$gcloudPath = "C:\Users\ookub\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin"
if ($env:PATH -notlike "*$gcloudPath*") { $env:PATH += ";$gcloudPath" }

# ===== スケジュール定義 =====
# cron 書式: 分 時 日 月 曜日（JST）
$schedulers = @(
    @{
        name        = "data-ingestion-waseda-daily"
        description = "早稲田アカデミー お知らせ自動取得（毎日 8:00）"
        schedule    = "0 8 * * *"
        source      = "waseda"
        extra_args  = @("--browser")
    },
    @{
        name        = "data-ingestion-gmail-dm-daily"
        description = "Gmail DM 取込（毎日 7:00）"
        schedule    = "0 7 * * *"
        source      = "gmail"
        extra_args  = @("--query", "label:DM", "--max-results", "50")
    },
    @{
        name        = "data-ingestion-daiei-daily"
        description = "ダイエー 商品情報取込（毎日 9:00）"
        schedule    = "0 9 * * *"
        source      = "daiei"
        extra_args  = @()
    },
    @{
        name        = "data-ingestion-rakuten-daily"
        description = "楽天西友 商品情報取込（毎日 9:30）"
        schedule    = "30 9 * * *"
        source      = "rakuten"
        extra_args  = @()
    }
)

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Cloud Scheduler トリガー作成" -ForegroundColor Cyan
Write-Host "  プロジェクト: $PROJECT_ID" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

foreach ($s in $schedulers) {
    $body = @{ extra_args = $s.extra_args } | ConvertTo-Json -Compress

    Write-Host "`n[$($s.name)]" -ForegroundColor Yellow
    Write-Host "  説明    : $($s.description)"
    Write-Host "  スケジュール: $($s.schedule) ($TIME_ZONE)"
    Write-Host "  POSTボディ : $body"

    # 既存ジョブの確認
    $existing = & gcloud scheduler jobs describe $s.name `
        --project=$PROJECT_ID `
        --location=$REGION 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  → 既存ジョブを更新..." -ForegroundColor Blue
        & gcloud scheduler jobs update http $s.name `
            --project=$PROJECT_ID `
            --location=$REGION `
            --schedule=$s.schedule `
            --time-zone=$TIME_ZONE `
            --uri="$SERVICE_URL/api/run/$($s.source)" `
            --message-body=$body `
            --headers="Content-Type=application/json" `
            --oidc-service-account-email=$SERVICE_ACCOUNT `
            --oidc-token-audience="$SERVICE_URL"
    } else {
        Write-Host "  → 新規ジョブを作成..." -ForegroundColor Green
        & gcloud scheduler jobs create http $s.name `
            --project=$PROJECT_ID `
            --location=$REGION `
            --schedule=$s.schedule `
            --time-zone=$TIME_ZONE `
            --uri="$SERVICE_URL/api/run/$($s.source)" `
            --message-body=$body `
            --headers="Content-Type=application/json" `
            --oidc-service-account-email=$SERVICE_ACCOUNT `
            --oidc-token-audience="$SERVICE_URL" `
            --description=$s.description
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ 完了" -ForegroundColor Green
    } else {
        Write-Host "  ✗ 失敗" -ForegroundColor Red
    }
}

Write-Host "`n==================================================" -ForegroundColor Cyan
Write-Host "  作成済みジョブ一覧:" -ForegroundColor Cyan
& gcloud scheduler jobs list --project=$PROJECT_ID --location=$REGION
Write-Host "==================================================" -ForegroundColor Cyan
