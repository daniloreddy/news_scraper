# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**First-run setup (set dashboard password):**
```
venv\Scripts\python scripts\set_password.py
```

**Run locally:**
```bat
scripts\run.bat            # Windows
bash scripts/run.sh        # Linux/macOS
```
Starts uvicorn on port 8088 with `--reload`. Auto-creates the venv and installs deps on first run.

**Run locally (manual):**
```
venv\Scripts\python -m uvicorn app.main:app --reload --port 8088 --loop asyncio
```

**Install deps (dev deps needed for quality checks):**
```
venv\Scripts\pip install -r requirements.txt -r requirements.dev.txt
venv\Scripts\playwright install chromium
```

**Test API interactively:**
```
venv\Scripts\python scripts/test_api.py
```

**Quality checks (ruff + mypy + pytest):**
```bat
scripts\checks.bat         # Windows
bash scripts/checks.sh     # Linux/macOS
```

**Docker (production, prebuilt GHCR image):**
```
docker compose up -d
```

**Docker (local dev, builds from source):**
```
docker compose -f docker-compose-dev.yml up --build
```
Both map host:8088 → container:8000. Requires 512MB shm for Chromium. Bind mounts: `./data:/app/data` (runtime data) and `./debug:/app/debug` (DEBUG artifacts).

## Architecture

FastAPI microservice with scraping API + NiceGUI monitoring dashboard:

- `app/main.py` — HTTP layer. Scraping endpoints, auth middleware, NiceGUI mount.
- `app/scraper.py` — Scraping logic. Playwright → BeautifulSoup → MarkItDown → LLM.
- `app/config.py` — ConfigManager singleton. `.env` baseline + `data/config.json` runtime overrides.
- `app/metrics.py` — MetricsDB. SQLite via aiosqlite. Records each request with token counts.
- `app/ui/auth.py` — AuthManager for dashboard (scrypt password, JWT cookie).
- `app/ui/router.py` — FastAPI routes: `/login`, `/auth/login`, `/auth/logout`.
- `app/ui/pages.py` — NiceGUI pages: dashboard (`/ui/`), config editor (`/ui/config`).

**Scrape flow (`/scrape`):**
1. Playwright renders full JS page → raw HTML
2. BeautifulSoup normalizes custom tags, resolves relative URLs
3. MarkItDown converts HTML → Markdown
4. HTTP POST to `{LLM_BASE_URL}/chat/completions` → article links as structured JSON
5. Each article URL is scraped with `_scrape_article_page()` → title, content, metadata
6. Returns JSON array + records metrics (duration, token counts) to SQLite

**LLM integration:** Direct `httpx` HTTP POST to `{LLM_BASE_URL}/chat/completions` (OpenAI-compatible). No openai SDK. Supports Ollama, LM Studio, any compatible endpoint. Config via `app/config.py` ConfigManager — hot-reload without restart.

**Config hot reload:** `data/config.json` overrides `.env` at runtime. LLM params are read from ConfigManager on every call (a fresh httpx client is created per request in `scraper._call_llm_api`), so changes apply immediately. `RATE_LIMIT` requires restart.

**Debug mode:** Set `DEBUG=true` in `.env` or via UI → saves HTML, Markdown, LLM responses to `debug/`.

## Key Constraints

- **Windows event loop:** ProactorEventLoop required for Playwright on Windows. Set via `asyncio.set_event_loop_policy` in `main.py` before app startup. Do not change this.
- **Sync endpoints:** FastAPI routes use `asyncio.new_event_loop()` + `loop.run_until_complete()` in worker threads to bridge sync HTTP handling with async Playwright. Don't convert endpoints to `async def`.
- **Module execution:** `app/main.py` uses relative imports. Must be run as `uvicorn app.main:app`, not `python app/main.py`.
- **No CSS selectors / XPath:** Scraping is LLM-driven by design. Don't add selector-based fallbacks.
- **No openai SDK:** LLM calls use `httpx` POST directly to the OpenAI-compatible endpoint. Do not use `from openai import ...`.
- **LLM response parsing:** Strip markdown code fences before `json.loads()` — some models wrap JSON in ```json blocks.
- **NiceGUI import order:** `from .ui import pages as _ui_pages` must be imported BEFORE `ui.run_with(app)` and must NOT use `import app.ui.pages` (absolute import shadows the `app = FastAPI(...)` variable).
- **NiceGUI navigation paths:** `ui.navigate.to()` prepends the mount path `/ui` automatically. Use paths relative to the NiceGUI root (e.g. `/config` not `/ui/config`). To navigate to FastAPI routes use `ui.run_javascript("window.location.href='/route'")`.
- **NiceGUI dark mode persistence:** Use `from nicegui import app as ng_app` and `ng_app.storage.user` — never `ui.dark_mode(True)` hardcoded (resets theme on every page load). `ui.storage` does not exist.
- **Temp files:** `temp_*.html` created at runtime in project root (gitignored).

## Runtime data (gitignored)

- `data/metrics.db` — SQLite request history
- `data/config.json` — runtime config overrides (priority over .env)
- `data/auth.json` — dashboard password hash + JWT secret

## Environment Variables

See `.env.example`. Key vars:
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_TIMEOUT`
- `LLM_MAX_PROMPT_CHARS` — truncates markdown sent to LLM (default 8000 chars ≈ 2700 tokens)
- `API_AUTH_TOKEN` — if set, all `/scrape*` endpoints require `Authorization: Bearer <token>`
- `SCRAPE_TIMEOUT` — global scraping timeout in seconds (default 300)
- `RATE_LIMIT` — per-IP rate limit (default `20/minute`), requires restart to change
- `DEBUG` — saves debug artifacts to `debug/`
- `TRUSTED_PROXIES` — comma-separated IPs allowed to set `CF-Connecting-IP`/`X-Real-IP`/`X-Forwarded-For` for client IP resolution (default `127.0.0.1`). Requires restart to change.
- `AUTH_SECURE_COOKIE` — set to `1` to force the `Secure` flag on the dashboard session cookie (auto-enabled behind HTTPS proxies via `X-Forwarded-Proto`).

Note: `ConfigManager._load()` calls `load_dotenv()`, so `.env` values are also exported to `os.environ` (without overriding existing vars) — required by `TRUSTED_PROXIES`/`AUTH_SECURE_COOKIE`, which are read via `os.getenv` outside ConfigManager.
