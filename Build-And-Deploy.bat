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
copy /y "c:\Users\ookub\document-management-system\apps\nas-sync-client\src-tauri\target\release\nas-sync-client.exe" "%USERPROFILE%\Desktop\SugarSync.exe" >nul 2>nul
copy /y "c:\Users\ookub\document-management-system\apps\nas-sync-client\src-tauri\target\release\nas-sync-client.exe" "%USERPROFILE%\OneDrive\Desktop\SugarSync.exe" >nul 2>nul
copy /y "c:\Users\ookub\document-management-system\apps\nas-sync-client\src-tauri\target\release\nas-sync-client.exe" "%USERPROFILE%\OneDrive\繝・せ繧ｯ繝医ャ繝予SugarSync.exe" >nul 2>nul
echo ==================================================
echo  Success!
echo.
echo  SugarSync.exe has been placed on your Desktop.
echo  Double-click it to run without terminal!
echo ==================================================
echo.
pause
