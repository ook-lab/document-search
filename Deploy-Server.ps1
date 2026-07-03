Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " SugarSync Go Server Auto Deploy to NAS (PowerShell)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] Uploading main.go to NAS..." -ForegroundColor Yellow
scp -O -o StrictHostKeyChecking=no -i "C:\Users\ookub\.ssh\id_ed25519" "services/nas-sync/server/main.go" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/main.go"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FALLBACK] Failed with private key. Attempting password-based upload..." -ForegroundColor Magenta
    scp -O -o StrictHostKeyChecking=no "services/nas-sync/server/main.go" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/main.go"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Upload failed."
        exit 1
    }
}

Write-Host "[2/3] Uploading dist assets to NAS..." -ForegroundColor Yellow
scp -O -o StrictHostKeyChecking=no -i "C:\Users\ookub\.ssh\id_ed25519" -r "services/nas-sync/server/dist" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FALLBACK] Failed with private key. Attempting password-based upload..." -ForegroundColor Magenta
    scp -O -o StrictHostKeyChecking=no -r "services/nas-sync/server/dist" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/"
}

Write-Host "[3/3] Rebuilding Docker containers on NAS..." -ForegroundColor Yellow
ssh -o StrictHostKeyChecking=no -i "C:\Users\ookub\.ssh\id_ed25519" -t "yoshinori@100.82.85.101" "cd /volume1/docker/nas-sync && (sudo env `"PATH=$PATH`" docker-compose down || sudo env `"PATH=$PATH`" docker compose down || sudo /usr/local/bin/docker compose down || sudo /usr/bin/docker compose down) && (sudo env `"PATH=$PATH`" docker-compose build --no-cache api-server || sudo env `"PATH=$PATH`" docker compose build --no-cache api-server || sudo /usr/local/bin/docker compose build --no-cache api-server || sudo /usr/bin/docker compose build --no-cache api-server) && (sudo env `"PATH=$PATH`" docker-compose up -d || sudo env `"PATH=$PATH`" docker compose up -d || sudo /usr/local/bin/docker compose up -d || sudo /usr/bin/docker compose up -d)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FALLBACK] Failed with private key. Attempting password-based command..." -ForegroundColor Magenta
    ssh -o StrictHostKeyChecking=no -t "yoshinori@100.82.85.101" "cd /volume1/docker/nas-sync && (sudo env `"PATH=$PATH`" docker-compose down || sudo env `"PATH=$PATH`" docker compose down || sudo /usr/local/bin/docker compose down || sudo /usr/bin/docker compose down) && (sudo env `"PATH=$PATH`" docker-compose build --no-cache api-server || sudo env `"PATH=$PATH`" docker compose build --no-cache api-server || sudo /usr/local/bin/docker compose build --no-cache api-server || sudo /usr/bin/docker compose build --no-cache api-server) && (sudo env `"PATH=$PATH`" docker-compose up -d || sudo env `"PATH=$PATH`" docker compose up -d || sudo /usr/local/bin/docker compose up -d || sudo /usr/bin/docker compose up -d)"
}

Write-Host "==================================================" -ForegroundColor Green
Write-Host " Server Deploy process ended." -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
