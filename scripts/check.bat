@echo off
setlocal

set VENV=.venv\Scripts

echo [1/4] ruff format ...
%VENV%\ruff format app tests
if %ERRORLEVEL% neq 0 ( echo FAILED: ruff format & exit /b %ERRORLEVEL% )

echo [2/4] ruff check ...
%VENV%\ruff check app tests --fix
if %ERRORLEVEL% neq 0 ( echo FAILED: ruff check & exit /b %ERRORLEVEL% )

echo [3/4] mypy ...
%VENV%\mypy app
if %ERRORLEVEL% neq 0 ( echo FAILED: mypy & exit /b %ERRORLEVEL% )

echo [4/4] pytest ...
%VENV%\pytest tests -v
if %ERRORLEVEL% neq 0 ( echo FAILED: pytest & exit /b %ERRORLEVEL% )

echo.
echo All checks passed.
endlocal
