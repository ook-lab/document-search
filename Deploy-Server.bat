@echo off
title SugarSync Server Deployer
echo ==================================================
echo  SugarSync Go Server Auto Deploy to NAS
echo ==================================================
echo.
echo [1/3] Uploading main.go to NAS...
scp -O -o StrictHostKeyChecking=no -i "C:\Users\ookub\.ssh\id_ed25519" "services/nas-sync/server/main.go" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/main.go"
if %errorlevel% neq 0 (
    echo.
    echo [FALLBACK] Failed with private key. Attempting password-based upload...
    scp -O -o StrictHostKeyChecking=no "services/nas-sync/server/main.go" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/main.go"
    if %errorlevel% neq 0 (
        echo [ERROR] Upload failed.
        pause
        exit /b %errorlevel%
    )
)

echo.
echo [2/3] Uploading dist assets to NAS...
scp -O -o StrictHostKeyChecking=no -i "C:\Users\ookub\.ssh\id_ed25519" -r "services/nas-sync/server/dist" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/"
if %errorlevel% neq 0 (
    echo.
    echo [FALLBACK] Failed with private key. Attempting password-based upload...
    scp -O -o StrictHostKeyChecking=no -r "services/nas-sync/server/dist" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/"
)

echo.
echo [3/3] Rebuilding Docker containers on NAS...
ssh -o StrictHostKeyChecking=no -i "C:\Users\ookub\.ssh\id_ed25519" -t "yoshinori@100.82.85.101" "cd /volume1/docker/nas-sync && (sudo env \"PATH=$PATH\" docker-compose down || sudo env \"PATH=$PATH\" docker compose down || sudo /usr/local/bin/docker compose down || sudo /usr/bin/docker compose down) && (sudo env \"PATH=$PATH\" docker-compose build --no-cache api-server || sudo env \"PATH=$PATH\" docker compose build --no-cache api-server || sudo /usr/local/bin/docker compose build --no-cache api-server || sudo /usr/bin/docker compose build --no-cache api-server) && (sudo env \"PATH=$PATH\" docker-compose up -d || sudo env \"PATH=$PATH\" docker compose up -d || sudo /usr/local/bin/docker compose up -d || sudo /usr/bin/docker compose up -d)"
if %errorlevel% neq 0 (
    echo.
    echo [FALLBACK] Failed with private key. Attempting password-based command...
    ssh -o StrictHostKeyChecking=no -t "yoshinori@100.82.85.101" "cd /volume1/docker/nas-sync && (sudo env \"PATH=$PATH\" docker-compose down || sudo env \"PATH=$PATH\" docker compose down || sudo /usr/local/bin/docker compose down || sudo /usr/bin/docker compose down) && (sudo env \"PATH=$PATH\" docker-compose build --no-cache api-server || sudo env \"PATH=$PATH\" docker compose build --no-cache api-server || sudo /usr/local/bin/docker compose build --no-cache api-server || sudo /usr/bin/docker compose build --no-cache api-server) && (sudo env \"PATH=$PATH\" docker-compose up -d || sudo env \"PATH=$PATH\" docker compose up -d || sudo /usr/local/bin/docker compose up -d || sudo /usr/bin/docker compose up -d)"
)

echo.
echo ==================================================
echo  Server Deploy Completed successfully!
echo ==================================================
echo.
pause
