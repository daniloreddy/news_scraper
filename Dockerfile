# Usa l'immagine ufficiale Playwright che include già i browser
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codice applicazione
COPY app/ ./app/
COPY static/ ./static/

# Playwright: installa solo Chromium (più leggero)
RUN playwright install chromium --with-deps

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
