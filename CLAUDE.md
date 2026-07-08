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
Runs `python -m app.main --dev`, which starts uvicorn with `--reload` on `PORT` from `.env` (default 8088 if unset/no `.env`). Auto-creates the venv and installs deps on first run.

**Run locally (manual):**
```
venv\Scripts\python -m app.main --dev --port 8088
venv\Scripts\python -m app.main --dev --env-file .env.staging   # custom env file
```
`app/main.py` has a two-stage argparse: a stage-1 parser (module import time) loads `--env-file` (default: nearest `.env`) before any other env var is read, and a stage-2 parser (`if __name__ == "__main__":`) resolves `--port`/`--host`/`--dev` with precedence CLI flag > env var (`PORT`/`HOST`/`DEV`) > hardcoded default, then calls `uvicorn.run(..., loop="asyncio")` — `loop="asyncio"` is required to preserve the `WindowsProactorEventLoopPolicy` set for Playwright; do not change it.

Invoking `uvicorn app.main:app --reload --port 8088 --loop asyncio` directly (bypassing `python -m app.main`) still works — the stage-1 parser tolerates unrecognized argv — but only the `python -m app.main` form honors `--env-file`/`--dev`/`--host` overrides.

`app/config.py`'s `ConfigManager` resolves `--env-file` independently (own lightweight argv parse, same default-to-nearest-`.env` fallback) so it agrees with `main.py` on which file to read regardless of import order — this is also the file it polls for hot-reload and writes to via the UI config editor.

**Install deps (dev deps needed for quality checks):**
```
venv\Scripts\pip install -r requirements.txt -r requirements.dev.txt
venv\Scripts\playwright install chromium
```

**Test API (health + scrape via curl):**
```bat
scripts\test_scraper.bat            # Windows
bash scripts/test_scraper.sh        # Linux/macOS
```
Env vars: `TOKEN`, `HOST` (default `localhost`), `PORT` (default `8088`), `SCRAPE_URL`, `MAX_ARTICLES`.

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
Both map host:`PORT` (default 8088, set in `.env`) → container:8000. Requires 512MB shm for Chromium. Bind mounts: `./.env:/app/.env` (config — same file read/written by `ConfigManager` as in local dev, see below), `./data:/app/data` (runtime data) and `./debug:/app/debug` (DEBUG artifacts). `.env` must exist on the host before `docker compose up` — Docker creates an empty directory instead of erroring if a bind-mounted file source is missing. Config no longer arrives via `env_file`/OS env vars — the container reads `.env` directly off the bind mount, so UI edits and hot-reload work identically in Docker and local dev.

## Architecture

FastAPI microservice with scraping API + NiceGUI monitoring dashboard:

- `app/main.py` — HTTP layer. Scraping endpoints (`/scrape`, `/scrape/article` share the `_run_scrape_sync()` helper), auth middleware, NiceGUI mount.
- `app/scraper.py` — Scraping logic. Playwright → BeautifulSoup → MarkItDown → LLM. Browser/page launch shared via `_new_browser_page()`; HTML→Markdown conversion in-memory via `_html_to_markdown()` (`markitdown.convert_stream`, no temp files).
- `app/config.py` — ConfigManager singleton. `.env` is the single source of truth (defaults + `.env` only, no OS env override, no separate JSON override file); resolves its own `--env-file` path (mirrors `main.py`'s stage-1 parser) and hot-reloads by polling the file's mtime.
- `app/metrics.py` — MetricsDB. SQLite via aiosqlite. Records each request with token counts; `purge_old()` prunes records past `METRICS_RETENTION_DAYS`.
- `app/net.py` — Shared client-IP resolution (`resolve_client_ip()`, `trusted_proxies()`), used by both `main.py` (rate limiting) and `ui/auth.py` (login rate limiting).
- `app/ui/auth.py` — AuthManager for dashboard (scrypt password, JWT cookie).
- `app/ui/router.py` — FastAPI routes: `/login`, `/auth/login`, `/auth/logout`.
- `app/ui/pages.py` — NiceGUI pages: dashboard (`/ui/`), config editor (`/ui/config`).

**Scrape flow (`/scrape`):**
1. Playwright renders full JS page → raw HTML
2. BeautifulSoup normalizes custom tags, resolves relative URLs
3. MarkItDown converts HTML → Markdown (in-memory, no temp files)
4. HTTP POST to `{LLM_BASE_URL}/chat/completions` → article links as structured JSON
5. Each article URL is scraped with `_scrape_article_page()` → title, content, metadata
6. Returns JSON array + records metrics (duration, token counts) to SQLite

`/scrape/article` runs the same pipeline from step 5 for a single given URL (no LLM link-extraction step).

**LLM integration:** Direct `httpx` HTTP POST to `{LLM_BASE_URL}/chat/completions` (OpenAI-compatible). No openai SDK. Supports Ollama, LM Studio, any compatible endpoint. Config via `app/config.py` ConfigManager — hot-reload without restart.

**Config hot reload:** the UI config editor (`/ui/config`) writes changes directly into `.env` via `python-dotenv`'s `set_key()` (preserves comments/order) and updates the in-process cache immediately. A background task in `main.py`'s lifespan polls `.env`'s mtime every 5s and reloads `ConfigManager` if it changed — this also picks up manual edits (e.g. `docker exec` + editor), no restart needed. LLM params are read from ConfigManager on every call (a fresh httpx client is created per request in `scraper._call_llm_api`), so changes apply immediately. `RATE_LIMIT` is also re-evaluated per-request (passed to slowapi as a dynamic lambda) — hot-reload, no restart. The only settings that still require a restart are `PORT`/`HOST`/`DEV`, since they're read once in `main.py`'s `__main__` block to bind the socket.

**Upgrading from `data/config.json`:** older deployments may have a `data/config.json` override file (pre-hot-reload). On first boot after upgrading, `ConfigManager` migrates its values into `.env` (via `set_key`) and renames the file to `data/config.json.migrated` so it isn't re-read. In Docker this writes to the bind-mounted host `.env` (see Commands above) — the file survives container restarts like any other bind mount.

**Debug mode:** Set `DEBUG=true` in `.env` or via UI → saves HTML, Markdown, LLM responses to `debug/`.

**Metrics retention:** `metrics.purge_old()` runs every 6h from a background task (started in `main.py` lifespan), deleting records older than `METRICS_RETENTION_DAYS` (default 30). No restart needed — picked up on the next purge cycle.

## Key Constraints

- **Windows event loop:** ProactorEventLoop required for Playwright on Windows. Set via `asyncio.set_event_loop_policy` in `main.py` before app startup. Do not change this.
- **Sync endpoints:** FastAPI routes use `asyncio.new_event_loop()` + `loop.run_until_complete()` in worker threads to bridge sync HTTP handling with async Playwright. Don't convert endpoints to `async def`.
- **Module execution:** `app/main.py` uses relative imports. Must be run as `uvicorn app.main:app` or `python -m app.main`, not `python app/main.py` (breaks relative imports).
- **No CSS selectors / XPath:** Scraping is LLM-driven by design. Don't add selector-based fallbacks.
- **No openai SDK:** LLM calls use `httpx` POST directly to the OpenAI-compatible endpoint. Do not use `from openai import ...`.
- **LLM response parsing:** Strip markdown code fences before `json.loads()` — some models wrap JSON in ```json blocks.
- **NiceGUI import order:** `from .ui import pages as _ui_pages` must be imported BEFORE `ui.run_with(app)` and must NOT use `import app.ui.pages` (absolute import shadows the `app = FastAPI(...)` variable).
- **NiceGUI navigation paths:** `ui.navigate.to()` prepends the mount path `/ui` automatically. Use paths relative to the NiceGUI root (e.g. `/config` not `/ui/config`). To navigate to FastAPI routes use `ui.run_javascript("window.location.href='/route'")`.
- **NiceGUI dark mode persistence:** Use `from nicegui import app as ng_app` and `ng_app.storage.user` — never `ui.dark_mode(True)` hardcoded (resets theme on every page load). `ui.storage` does not exist.

## Runtime data (gitignored)

- `data/metrics.db` — SQLite request history
- `data/auth.json` — dashboard password hash + JWT secret
- `data/config.json.migrated` — present only after upgrading from the old JSON-override scheme; harmless, safe to delete once confirmed `.env` has the migrated values

## Environment Variables

See `.env.example`. Key vars:
- `PORT` — host listen port (default 8088). Used by `docker-compose*.yml` (`${PORT:-8088}` in the `ports:` mapping and Dockerfile `CMD`) and, for local dev, as the `--port` default in `app/main.py`'s `__main__` block (only when run via `python -m app.main`; `--port` CLI flag overrides it). Restart/re-`up` required to change.
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_TIMEOUT`
- `LLM_MAX_PROMPT_CHARS` — truncates markdown sent to LLM (default 8000 chars ≈ 2700 tokens)
- `API_AUTH_TOKEN` — if set, all `/scrape*` endpoints require `Authorization: Bearer <token>`
- `SCRAPE_TIMEOUT` — global scraping timeout in seconds (default 300)
- `RATE_LIMIT` — per-IP rate limit (default `20/minute`), hot-reload (re-evaluated per-request), no restart needed
- `METRICS_RETENTION_DAYS` — days of request history kept in `data/metrics.db` before pruning (default 30), checked every 6h, no restart needed
- `DEBUG` — saves debug artifacts to `debug/`
- `TRUSTED_PROXIES` — comma-separated IPs allowed to set `CF-Connecting-IP`/`X-Real-IP`/`X-Forwarded-For` for client IP resolution (default `127.0.0.1`). Hot-reload, no restart needed.
- `AUTH_SECURE_COOKIE` — set to `1`/`true`/`yes` to force the `Secure` flag on the dashboard session cookie (auto-enabled behind HTTPS proxies via `X-Forwarded-Proto`). Hot-reload, no restart needed.

Note: all config keys above are read exclusively through `ConfigManager` (`.env` + hardcoded defaults) — there is no OS-environment-variable override layer and no separate JSON override file. `TRUSTED_PROXIES`/`AUTH_SECURE_COOKIE` used to be read via raw `os.getenv` outside ConfigManager (frozen at boot); they were migrated onto `ConfigManager` so they hot-reload like everything else.
