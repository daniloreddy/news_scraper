# python:3.12-slim (non l'immagine Playwright-bundled: quella è tagged a una
# versione fissa di Playwright con Python 3.10, incompatibile con il
# Requires-Python >=3.11 di redberry-webkit). I browser vengono installati
# esplicitamente sotto con `playwright install chromium --with-deps`, che
# porta anche le librerie di sistema necessarie — pattern ufficiale Playwright
# su immagini Debian-based generiche.
FROM python:3.12-slim

WORKDIR /app

# Dipendenze Python (redberry-webkit installato da git+https, serve git)
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Codice applicazione
COPY app/ ./app/
COPY static/ ./static/
COPY scripts/ ./scripts/

# Playwright: installa solo Chromium (più leggero)
RUN playwright install chromium --with-deps

# Porta di ascolto effettiva, sovrascrivibile a runtime via env PORT
# (stesso var usato da scripts/run.sh|bat e da docker-compose*.yml).
ENV PORT=8088
EXPOSE 8088

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8088}"]
