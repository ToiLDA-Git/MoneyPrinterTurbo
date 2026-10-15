@echo off
setlocal
chcp 65001 >nul
title Tarot Reels Studio
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Cannot find the project Python environment: .venv\Scripts\python.exe
    echo Install the project dependencies before starting.
    pause
    exit /b 1
)

powershell -NoProfile -NonInteractive -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 2; if ($response.StatusCode -eq 200 -and $response.Content.Trim() -eq 'ok') { exit 0 } } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo Tarot Reels Studio is already running.
    start "" "http://127.0.0.1:8501"
    exit /b 0
)

echo Starting Tarot Reels Studio at http://127.0.0.1:8501
echo Keep this window open. Press Ctrl+C to stop the application.
echo Select Tarot Reels Studio in the workspace selector.
echo.
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" -X utf8 -m streamlit run webui/Main.py --server.address=127.0.0.1 --server.port=8501 --server.headless=false --browser.serverAddress=127.0.0.1 --browser.gatherUsageStats=false --server.showEmailPrompt=false
if errorlevel 1 (
    echo.
    echo Startup failed. Review the error above.
    pause
    exit /b 1
)
endlocal
