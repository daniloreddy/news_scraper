@echo off
SETLOCAL
SET "SCRIPTS_DIR=%~dp0"
SET "ROOT_DIR=%SCRIPTS_DIR%.."
CD /D "%ROOT_DIR%"

IF NOT EXIST .venv GOTO NO_VENV

CALL .venv\Scripts\activate.bat
python scripts\test_api.py
GOTO :END

:NO_VENV
echo [ERROR] Virtual environment (.venv) not found.
pause
exit /b 1

:END
ENDLOCAL
pause
