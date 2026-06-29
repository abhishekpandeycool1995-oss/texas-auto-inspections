@echo off
setlocal enabledelayedexpansion
title Texas 1st Auto Inspections

echo.
echo  =============================================
echo       Texas 1st Auto Inspections
echo  =============================================
echo.

set "ROOT=%~dp0"

set "GEMINI_API_KEY="
if exist "%ROOT%api_key.txt" (
    set /p GEMINI_API_KEY=<"%ROOT%api_key.txt"
    echo  [OK] API key loaded.
) else (
    echo  [WARN] No api_key.txt found. Running in TEST MODE.
)

echo.
echo  Starting servers...

cd /d "%ROOT%backend"
start /b python main.py > "%ROOT%backend_output.txt" 2>&1

cd /d "%ROOT%frontend"
start /b python -m http.server 3000 > "%ROOT%frontend_output.txt" 2>&1

timeout /t 4 /nobreak > nul

start "" "http://localhost:3000"

echo.
echo  =============================================
echo   Tool is running in your browser.
echo   Close this window to stop the servers.
echo  =============================================
echo.

pause > nul

echo  Shutting down servers...
taskkill /f /im python.exe > nul 2>&1
del "%ROOT%backend_output.txt" "%ROOT%frontend_output.txt" 2>nul
exit
