$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
Set-Location "C:\Users\ookub\document-management-system\apps\nas-sync-client"

Write-Host "Building SugarSync..." -ForegroundColor Cyan
npm run tauri build

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$src = "C:\Users\ookub\document-management-system\apps\nas-sync-client\src-tauri\target\release\nas-sync-client.exe"

# Stop any running copy first. Overwriting a running exe's file silently fails
# on Windows (file locked), leaving the old build in place while this script
# reports success -- so the running process must be gone before copying.
Get-Process -Name "SugarSync" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

Copy-Item $src "$env:USERPROFILE\Desktop\SugarSync.exe" -Force
Copy-Item $src "$env:USERPROFILE\OneDrive\デスクトップ\SugarSync.exe" -Force -ErrorAction SilentlyContinue

$deployedTime = (Get-Item "$env:USERPROFILE\Desktop\SugarSync.exe").LastWriteTime
$builtTime = (Get-Item $src).LastWriteTime
if ($deployedTime -ne $builtTime) {
    Write-Host "Copy verification FAILED: Desktop exe timestamp does not match the build!" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Start-Process "$env:USERPROFILE\Desktop\SugarSync.exe"

Write-Host "Done! SugarSync.exe is on your Desktop and running." -ForegroundColor Green
Read-Host "Press Enter to close"
