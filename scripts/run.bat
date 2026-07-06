@echo off
SETLOCAL
SET "SCRIPTS_DIR=%~dp0"
SET "ROOT_DIR=%SCRIPTS_DIR%.."
CD /D "%ROOT_DIR%"

SET "APP_PORT=8088"
IF EXIST ".env" (
    FOR /F "usebackq tokens=1,* delims==" %%A IN (".env") DO (
        IF "%%A"=="APP_PORT" SET "APP_PORT=%%B"
    )
)

IF EXIST venv GOTO ACTIVATE

echo [INFO] Creating virtual environment...
python -m venv venv
CALL venv\Scripts\activate.bat
echo [INFO] Installing dependencies...
pip install -r requirements.txt
GOTO START

:ACTIVATE
echo [INFO] Activating virtual environment...
CALL venv\Scripts\activate.bat

:START
echo [INFO] Starting news-scraper on port %APP_PORT%...
uvicorn app.main:app --reload --port %APP_PORT% --loop asyncio

ENDLOCAL
