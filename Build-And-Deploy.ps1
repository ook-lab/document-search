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
Copy-Item $src "$env:USERPROFILE\Desktop\SugarSync.exe" -Force
Copy-Item $src "$env:USERPROFILE\OneDrive\デスクトップ\SugarSync.exe" -Force -ErrorAction SilentlyContinue

Write-Host "Done! SugarSync.exe is on your Desktop." -ForegroundColor Green
Read-Host "Press Enter to close"
