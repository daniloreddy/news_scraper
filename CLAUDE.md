# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run locally (Windows):**
```bat
scripts\run_local.bat
```
Starts uvicorn on port 8088 with `--reload`.

**Run locally (manual):**
```
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8088 --loop asyncio
```

**Install deps:**
```
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

**Test API interactively:**
```
.venv\Scripts\python scripts/test_api.py
```

**Docker:**
```
docker-compose up --build
```
Maps host:8088 → container:8000. Requires 512MB shm for Chromium.

**No test suite exists.** No pytest config or `tests/` directory.

## Architecture

FastAPI microservice with two layers:

- `app/main.py` — HTTP layer. Defines `/scrape` and `/scrape/article` endpoints, optional Bearer token auth, lifespan installs Playwright Chromium on startup. Endpoints are **sync** (not async) to allow ThreadPool execution of Playwright.
- `app/scraper.py` — Scraping logic. All intelligence lives here.

**Scrape flow (`/scrape`):**
1. Playwright renders full JS page → raw HTML
2. BeautifulSoup normalizes custom tags, resolves relative URLs
3. MarkItDown converts HTML → Markdown
4. OpenAI-compatible LLM extracts article links as structured JSON (`ArticlesList` Pydantic model)
5. Each article URL is scraped with `_scrape_article_page()` → title, content, metadata
6. Returns JSON array

**LLM integration:** Uses `AsyncOpenAI` with `LLM_BASE_URL` override — supports local LLMs (Ollama, LM Studio, etc.) in addition to OpenAI. All LLM config via env vars.

**Debug mode:** Set `DEBUG=true` in `.env` → saves intermediate HTML, Markdown, and LLM responses to `debug/`.

## Key Constraints

- **Windows event loop:** ProactorEventLoop required for Playwright on Windows. Set via `asyncio.set_event_loop_policy` in `main.py` before app startup. Do not change this.
- **Sync endpoints:** FastAPI routes call `asyncio.run()` inside a ThreadPoolExecutor to bridge sync HTTP handling with async Playwright. Don't convert endpoints to `async def`.
- **Module execution:** `app/main.py` uses relative imports (`from .scraper import …`). Must be run as `uvicorn app.main:app`, not `python app/main.py`.
- **No CSS selectors / XPath:** Scraping is LLM-driven by design. Don't add selector-based fallbacks.
- **Temp files:** `temp_*.html` created at runtime in project root (gitignored).

## Environment Variables

See `.env.example`. Key vars:
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_TIMEOUT`
- `API_AUTH_TOKEN` — if set, all `/scrape*` endpoints require `Authorization: Bearer <token>`
- `DEBUG` — saves debug artifacts to `debug/`
