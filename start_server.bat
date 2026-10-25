@echo off
echo ========================================================
echo   EDVEXA DATA QUALITY & ETL MONITOR
echo   Starting Backend API Server on Port 8010...
echo ========================================================

cd /d "%~dp0"
if exist .venv\Scripts\uvicorn.exe (
    echo Using virtual environment at .venv...
    .venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
) else (
    echo Virtual environment not found, falling back to system python...
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
)
pause
