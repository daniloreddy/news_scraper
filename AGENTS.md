# Agent Guide: news-scraper

## Architecture & Entrypoints
- **Framework:** FastAPI + Playwright (Headless Chromium) + MarkItDown + OpenAI API client.
- **Structure:** Source in `app/` directory (required for relative imports and Docker).
- **Entrypoint:** `app/main.py`.
- **Local Run:** `uvicorn app.main:app --reload` (from root).
- **Docker Run:** `docker compose up -d`. External port `8088`.

## Smart Scraping (LLM-based)
- **Concept:** No more fragile CSS/Regex selectors (`SITE_CONFIG` is removed). The homepage is converted to clean Markdown via Microsoft's `MarkItDown` and analyzed by the LLM to extract news links structurally.
- **LLM Configuration:** Configured in `.env` via `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_TIMEOUT`, and `DEBUG`.

## Development & Windows Quirks
- **Setup:** `pip install -r requirements.txt && playwright install chromium`.
- **Windows Event Loop Conflict:** Playwright and Uvicorn clash on Windows' default event loop. To resolve this, **FastAPI endpoints are defined as synchronous `def`** (not `async def`), so FastAPI offloads them to a background thread pool with a fresh, dedicated `ProactorEventLoop`. Keep them synchronous.
- **Debugging:** Setting `DEBUG=true` in `.env` saves `playwright_output.html`, `markitdown_output.md`, and `llm_output.json` into a local `/debug` folder.

## Key Constraints
- **Imports:** `main.py` uses `from .scraper`. Must be run as a module or from parent directory.
- **Docker shm:** Requires `shm_size: "512m"` in `docker-compose.yml` to prevent Chromium crashes.
- **n8n Integration:** Designed for POST requests. Returns list of articles with `title`, `url`, `published_date`, `content`, `thumbnail_url`.

## MANDATORY RULES
- DO NOT READ .env file. NEVER!