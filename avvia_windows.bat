@echo off
cd /d "%~dp0"
echo ============================================
echo   SLAW
echo ============================================
where python >nul 2>nul
if errorlevel 1 (
    echo [ERRORE] Python non trovato.
    echo Installalo da https://python.org/downloads
    echo Durante l'installazione spunta "Add Python to PATH"
    pause
    exit /b 1
)
echo.
echo Apro SLAW nel browser: http://localhost:8000
echo (per usarlo anche dal telefono avvia: python server.py --lan)
echo.
start "" http://localhost:8000
python server.py
pause
