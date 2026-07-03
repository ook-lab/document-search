@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { .\Deploy-Server.ps1 2>&1 | Tee-Object -FilePath deploy_debug.log }"
pause
