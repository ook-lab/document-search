@echo off
echo ==========================================
echo   SSH Key Setup Script
echo ==========================================
echo [1/2] Copying public key to NAS...
scp -O -o StrictHostKeyChecking=no "C:\Users\ookub\.ssh\id_ed25519.pub" "yoshinori@100.82.85.101:/tmp/id_ed25519.pub"
if %errorlevel% neq 0 (
    echo Error copying key.
    exit /b %errorlevel%
)

echo [2/2] Registering public key on NAS...
ssh -o StrictHostKeyChecking=no -t "yoshinori@100.82.85.101" "mkdir -p ~/.ssh && cat /tmp/id_ed25519.pub >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && rm /tmp/id_ed25519.pub"
if %errorlevel% neq 0 (
    echo Error registering key.
    exit /b %errorlevel%
)

echo ==========================================
echo   SSH Key Setup Successful!
echo ==========================================
