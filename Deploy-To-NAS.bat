@echo off
echo ==========================================
echo   NAS Sync Server Auto Deploy Script
echo ==========================================

echo [1/3] Uploading main.go to NAS (Legacy SCP Mode)...
scp -O -o StrictHostKeyChecking=no "services/nas-sync/server/main.go" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/main.go"
if %errorlevel% neq 0 (
    echo Error uploading main.go
    exit /b %errorlevel%
)

echo [2/3] Uploading dist assets to NAS (Legacy SCP Mode)...
scp -O -o StrictHostKeyChecking=no -r "services/nas-sync/server/dist" "yoshinori@100.82.85.101:/volume1/docker/nas-sync/server/"
if %errorlevel% neq 0 (
    echo Error uploading dist assets
    exit /b %errorlevel%
)

echo [3/3] Rebuilding Docker containers on NAS...
ssh -o StrictHostKeyChecking=no -t "yoshinori@100.82.85.101" "export PATH=$PATH:/usr/local/bin && cd /volume1/docker/nas-sync && sudo docker-compose down && sudo docker-compose build --no-cache api-server && sudo docker-compose up -d"
if %errorlevel% neq 0 (
    echo Error rebuilding Docker containers
    exit /b %errorlevel%
)

echo ==========================================
echo   Deploy successful!
echo ==========================================
