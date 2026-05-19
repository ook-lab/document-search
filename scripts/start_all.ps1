param(
    [string[]]$Name,
    [switch]$List,
    [switch]$All,
    [switch]$NoPrompt
)

# Local launcher: nothing starts unless you pass -All or -Name (explicit scope).
# ASCII / UTF-8 without BOM: safe for PowerShell 5.1 default encoding.

$ErrorActionPreference = "Continue"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot
$env:PYTHONPATH = $ProjectRoot

$services = @(
    @{ Name = "kakeibo-ui";         Path = "services/kakeibo/app.py";                 Port = 5000 },
    @{ Name = "doc-search";         Path = "services/doc-search/app.py";             Port = 5001 },
    @{ Name = "calendar-register";  Path = "services/calendar-register/app.py";      Port = 5003 },
    @{ Name = "data-ingestion";     Path = "services/data-ingestion/app.py";         Port = 5004 },
    @{ Name = "kakeibo-view";       Path = "services/kakeibo_view/app.py";           Port = 5005 },
    @{ Name = "reading-context";    Path = "services/reading-context-editor/app.py"; Port = 5006 },
    @{ Name = "pipeline-lab";       Path = "services/pipeline-lab/app.py";           Port = 5055 }
)

$streamlitServices = @(
    @{ Name = "netsuper-search"; Path = "services/netsuper-search/app.py"; Port = 8501 }
)

$allServiceNames = @($services | ForEach-Object { $_.Name }) + @($streamlitServices | ForEach-Object { $_.Name })

function Show-ServiceList {
    Write-Host "Flask ($($services.Count)):" -ForegroundColor Cyan
    foreach ($s in $services) {
        Write-Host ("  {0,-20} port {1}  {2}" -f $s.Name, $s.Port, $s.Path) -ForegroundColor Gray
    }
    Write-Host "Streamlit ($($streamlitServices.Count)):" -ForegroundColor Cyan
    foreach ($s in $streamlitServices) {
        Write-Host ("  {0,-20} port {1}  {2}" -f $s.Name, $s.Port, $s.Path) -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host ("Total defined: {0} processes (use -All to start all, or -Name to pick)" -f ($services.Count + $streamlitServices.Count)) -ForegroundColor Yellow
}

function Show-Usage {
    Write-Host "start_all.ps1 - local service launcher" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Services are NOT started by default. Specify scope explicitly." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  -List     List defined services (no launch)" -ForegroundColor White
    Write-Host "  -Name ..  Start only named services (repeat -Name or pass array)" -ForegroundColor White
    Write-Host "  -All      Start every Flask + Streamlit entry" -ForegroundColor White
    Write-Host "  -NoPrompt Skip 'Press any key' (use from scripts / Cursor)" -ForegroundColor White
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor Gray
    Write-Host "  .\scripts\start_all.ps1 -List" -ForegroundColor Gray
    Write-Host "  .\scripts\start_all.ps1 -Name pipeline-lab" -ForegroundColor Gray
    Write-Host "  .\scripts\start_all.ps1 -Name doc-search,pipeline-lab" -ForegroundColor Gray
    Write-Host "  .\scripts\start_all.ps1 -All" -ForegroundColor Gray
    Write-Host "  .\scripts\start_all.ps1 -Name pipeline-lab -NoPrompt   # CI / Cursor: no key wait" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Single service without this script:" -ForegroundColor Gray
    Write-Host "  python services/pipeline-lab/app.py" -ForegroundColor Gray
    Write-Host ""
}

if ($List) {
    Write-Host "=== Defined services ===" -ForegroundColor Cyan
    Write-Host ""
    Show-ServiceList
    exit 0
}

if ($All -and ($Name -and $Name.Count -gt 0)) {
    Write-Host "Cannot use -All together with -Name." -ForegroundColor Red
    exit 1
}

if (-not $All -and -not ($Name -and $Name.Count -gt 0)) {
    Show-Usage
    Write-Host "=== Defined services ===" -ForegroundColor Cyan
    Write-Host ""
    Show-ServiceList
    exit 1
}

$pickFlask = @()
$pickStreamlit = @()

if ($All) {
    $pickFlask = $services
    $pickStreamlit = $streamlitServices
} else {
    $pickFlask = @($services | Where-Object { $Name -contains $_.Name })
    $pickStreamlit = @($streamlitServices | Where-Object { $Name -contains $_.Name })
    $unknown = @($Name | Where-Object { $_ -notin $allServiceNames })
    if ($unknown.Count -gt 0) {
        Write-Host ("Unknown -Name: {0}" -f ($unknown -join ', ')) -ForegroundColor Red
        Write-Host ("Valid names: {0}" -f ($allServiceNames -join ', ')) -ForegroundColor Yellow
        exit 1
    }
    if ($pickFlask.Count -eq 0 -and $pickStreamlit.Count -eq 0) {
        Write-Host "No service matched -Name." -ForegroundColor Red
        exit 1
    }
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Document Management System - Launcher" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Project Root: $ProjectRoot" -ForegroundColor Gray
Write-Host "PYTHONPATH: $env:PYTHONPATH" -ForegroundColor Gray
if ($All) {
    Write-Host "Scope: -All (full stack)" -ForegroundColor Yellow
} else {
    Write-Host ("Scope: -Name {0}" -f ($Name -join ', ')) -ForegroundColor Yellow
}
Write-Host ""

Write-Host "Starting Flask..." -ForegroundColor Green
foreach ($service in $pickFlask) {
    $fullPath = Join-Path $ProjectRoot $service.Path
    if (Test-Path $fullPath) {
        Write-Host "  Starting $($service.Name) on port $($service.Port)..." -ForegroundColor Yellow
        Start-Process python -ArgumentList $service.Path -WorkingDirectory $ProjectRoot
    } else {
        Write-Host "  [SKIP] $($service.Name) - File not found: $($service.Path)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Starting Streamlit..." -ForegroundColor Green
foreach ($service in $pickStreamlit) {
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
Write-Host "  Launch commands issued." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Service URLs:" -ForegroundColor White
Write-Host "  - Kakeibo UI:         http://localhost:5000" -ForegroundColor Gray
Write-Host "  - Doc Search:         http://localhost:5001" -ForegroundColor Gray
Write-Host "  - Calendar Register:  http://localhost:5003" -ForegroundColor Gray
Write-Host "  - Data Ingestion UI:  http://localhost:5004" -ForegroundColor Gray
Write-Host "  - Kakeibo View:       http://localhost:5005" -ForegroundColor Gray
Write-Host "  - Reading context:    http://localhost:5006" -ForegroundColor Gray
Write-Host "  - Pipeline lab:       http://localhost:5055/" -ForegroundColor Gray
Write-Host "  - Netsuper Search:    http://localhost:8501" -ForegroundColor Gray
Write-Host ""

if (-not $NoPrompt -and [Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') {
    Write-Host "Press any key to close this window (services keep running)..."
    try {
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    } catch {
        # ISE / redirected stdin / non-interactive
    }
}
