@echo off
SETLOCAL
SET "SCRIPTS_DIR=%~dp0"
SET "ROOT_DIR=%SCRIPTS_DIR%.."
CD /D "%ROOT_DIR%"

IF NOT EXIST .venv GOTO NO_VENV

echo [INFO] Activating virtual environment...
CALL .venv\Scripts\activate.bat

echo [INFO] Starting news-scraper...
uvicorn app.main:app --reload --port 8088 --loop asyncio

ENDLOCAL
