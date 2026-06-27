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
GOTO RUN

:ACTIVATE
CALL venv\Scripts\activate.bat

:RUN
python scripts\test_api.py

ENDLOCAL
pause
