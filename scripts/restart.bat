@echo off
REM Lean Signals — Server Restart Script (Windows CMD)
REM Usage: scripts\restart.bat
setlocal enabledelayedexpansion

set PROJECT_DIR=%~dp0..
cd /d "%PROJECT_DIR%"
set PORT=%1
if "%PORT%"=="" set PORT=8088

echo === Lean Signals Restart ===
echo Project: %PROJECT_DIR%
echo Port: %PORT%

echo [1/4] Stopping existing server...
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/4] Clearing bytecode caches...
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
del /s /q *.pyc 2>nul
echo   Caches cleared

echo [3/4] Starting server...
start /B python server.py > NUL 2>&1
echo   Server starting...

echo [4/4] Waiting for server to be ready...
for /L %%i in (1,1,15) do (
    curl -s "http://localhost:%PORT%/api/health" >nul 2>&1
    if !errorlevel! equ 0 (
        echo.
        echo ✅ Server is UP!
        echo    Dashboard: http://localhost:%PORT%
        echo    API:       http://localhost:%PORT%/api/status
        goto :done
    )
    timeout /t 2 /nobreak >nul
)

echo ⚠️  Server may still be starting...
:done
endlocal
