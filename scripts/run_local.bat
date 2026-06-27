@echo off
SETLOCAL
SET "SCRIPTS_DIR=%~dp0"
SET "ROOT_DIR=%SCRIPTS_DIR%.."
CD /D "%ROOT_DIR%"

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
echo [INFO] Starting news-scraper...
uvicorn app.main:app --reload --port 8088 --loop asyncio

ENDLOCAL
