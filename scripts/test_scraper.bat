@echo off
REM news-scraper API test script (Windows)
REM Usage: set TOKEN=xxx && scripts\test_scraper.bat
REM        set HOST=myserver && set PORT=8088 && scripts\test_scraper.bat
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
if "%TOKEN%"=="" set TOKEN=
if "%HOST%"=="" set HOST=localhost
if "%PORT%"=="" set PORT=8088
if "%SCRAPE_URL%"=="" set SCRAPE_URL=https://www.acn.gov.it/portale/csirt-italia/alert-e-bollettini
if "%MAX_ARTICLES%"=="" set MAX_ARTICLES=1

echo === news-scraper API Test ===
echo Host: %HOST%
echo Port: %PORT%
echo Scrape URL: %SCRAPE_URL%
echo Max articles: %MAX_ARTICLES%
echo.

echo [1/2] Health Check...
curl -s http://%HOST%:%PORT%/health | python -m json.tool
if errorlevel 1 echo Health check failed
echo.

echo [2/2] Scraping news...
curl -s -X POST "http://%HOST%:%PORT%/scrape" ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer %TOKEN%" ^
  -d "{\"url\": \"%SCRAPE_URL%\", \"max_articles\": %MAX_ARTICLES%}" | python -m json.tool

echo.
echo === Test Complete ===
ENDLOCAL
