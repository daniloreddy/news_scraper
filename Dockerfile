# Usa l'immagine ufficiale Playwright che include già i browser
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codice applicazione
COPY app/ ./app/
COPY static/ ./static/
COPY scripts/ ./scripts/

# Playwright: installa solo Chromium (più leggero)
RUN playwright install chromium --with-deps

# Porta di ascolto effettiva, sovrascrivibile a runtime via env APP_PORT
# (stesso var usato da scripts/run.sh|bat e da docker-compose*.yml).
ENV APP_PORT=8088
EXPOSE 8088

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-8088}"]
