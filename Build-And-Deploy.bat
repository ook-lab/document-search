@echo off
title SugarSync Builder
echo ==================================================
echo  SugarSync Client Auto Build And Deploy
echo ==================================================
echo.
echo [1/2] Compiling the application...
echo.
set PATH=%USERPROFILE%\.cargo\bin;%PATH%
cd /d "c:\Users\ookub\document-management-system\apps\nas-sync-client"
call npm run tauri build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Compilation failed.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo [2/2] Copying executable to Desktop...
echo.
set SRC=c:\Users\ookub\document-management-system\apps\nas-sync-client\src-tauri\target\release\nas-sync-client.exe
powershell.exe -NoProfile -Command "$src = '%SRC%'; $dst1 = \"$env:USERPROFILE\Desktop\SugarSync.exe\"; $dst2 = \"$env:USERPROFILE\OneDrive\デスクトップ\SugarSync.exe\"; Copy-Item $src $dst1 -Force -ErrorAction SilentlyContinue; Copy-Item $src $dst2 -Force -ErrorAction SilentlyContinue; Write-Host 'Copied to:'; if (Test-Path $dst1) { Write-Host \" $dst1\" }; if (Test-Path $dst2) { Write-Host \" $dst2\" }"
echo ==================================================
echo  Success!
echo.
echo  SugarSync.exe has been placed on your Desktop.
echo  Double-click it to run without terminal!
echo ==================================================
echo.
pause
