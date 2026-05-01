# start_all.ps1 - 全サービス一括起動スクリプト
# 実行方法: PowerShellでプロジェクトルートにて .\start_all.ps1

$ErrorActionPreference = "Continue"

# プロジェクトルートを設定
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

# PYTHONPATHを設定（shared フォルダを認識させる）
$env:PYTHONPATH = $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Document Management System - Launcher" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Project Root: $ProjectRoot" -ForegroundColor Gray
Write-Host "PYTHONPATH: $env:PYTHONPATH" -ForegroundColor Gray
Write-Host ""

# サービス定義
$services = @(
    @{ Name = "kakeibo-ui";         Path = "services/kakeibo/app.py";                        Port = 5000 },
    @{ Name = "doc-search";         Path = "services/doc-search/app.py";                    Port = 5001 },
    @{ Name = "document-hub";      Path = "services/doc-processor/run_server.py";            Port = 5002 },
    @{ Name = "calendar-register";  Path = "services/calendar-register/app.py";             Port = 5003 },
    @{ Name = "data-ingestion";     Path = "services/data-ingestion/app.py";               Port = 5004 },
    @{ Name = "kakeibo-view";       Path = "services/kakeibo_view/app.py";                  Port = 5005 }
)

# netsuper-search は Streamlit アプリ
$streamlitServices = @(
    @{ Name = "netsuper-search"; Path = "services/netsuper-search/app.py"; Port = 8501 }
)

Write-Host "Starting Flask services..." -ForegroundColor Green

foreach ($service in $services) {
    $fullPath = Join-Path $ProjectRoot $service.Path
    if (Test-Path $fullPath) {
        Write-Host "  Starting $($service.Name) on port $($service.Port)..." -ForegroundColor Yellow
        Start-Process python -ArgumentList $service.Path -WorkingDirectory $ProjectRoot
    } else {
        Write-Host "  [SKIP] $($service.Name) - File not found: $($service.Path)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Starting Streamlit services..." -ForegroundColor Green

foreach ($service in $streamlitServices) {
    $fullPath = Join-Path $ProjectRoot $service.Path
    if (Test-Path $fullPath) {
        Write-Host "  Starting $($service.Name) on port $($service.Port)..." -ForegroundColor Yellow
        Start-Process streamlit -ArgumentList "run", $service.Path, "--server.port", $service.Port -WorkingDirectory $ProjectRoot
    } else {
        Write-Host "  [SKIP] $($service.Name) - File not found: $($service.Path)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  All services are launching!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Service URLs:" -ForegroundColor White
Write-Host "  - Kakeibo UI:         http://localhost:5000" -ForegroundColor Gray
Write-Host "  - Doc Search:         http://localhost:5001" -ForegroundColor Gray
Write-Host "  - Doc Review:         http://localhost:5002" -ForegroundColor Gray
Write-Host "  - Calendar Register:  http://localhost:5003" -ForegroundColor Gray
Write-Host "  - Data Ingestion UI:  http://localhost:5004" -ForegroundColor Gray
Write-Host "  - Netsuper Search:    http://localhost:8501" -ForegroundColor Gray
Write-Host "  - Kakeibo View:       http://localhost:5005" -ForegroundColor Gray
Write-Host ""
Write-Host "Press any key to exit this launcher (services will continue running)..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
