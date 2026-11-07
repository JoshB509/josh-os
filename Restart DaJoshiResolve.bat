@echo off
rem Restart DaJoshiResolve: stops the background server named in Data\runtime.json (only if it is Python), then reopens the app.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$r = Get-Content -Raw 'Data\runtime.json' | ConvertFrom-Json; $p = Get-Process -Id $r.pid -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -like 'python*') { Stop-Process -Id $p.Id -Force; Write-Host 'Stopped old server' $p.Id } else { Write-Host 'No old server running' }"
timeout /t 2 /nobreak >nul
start "" "%~dp0DaJoshiResolve.exe"
