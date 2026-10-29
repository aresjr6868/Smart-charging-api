@echo off
title CharServer - Unity API Bridge
cd /d "%~dp0"

echo =======================================================
echo   Starting CharServer...
echo =======================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" server.py
) else (
    python server.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Server stopped with error code %ERRORLEVEL%.
)

pause
