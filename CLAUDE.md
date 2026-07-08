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
Both map host:`PORT` (default 8088, set in `.env`) → container:8000. Requires 512MB shm for Chromium. Bind mounts: `.:/app/hostcfg` (the whole project folder, where `.env` sits next to `docker-compose.yml` — **not** a single-file mount, see below), `./data:/app/data` (runtime data) and `./debug:/app/debug` (DEBUG artifacts). `ENV_FILE=/app/hostcfg/.env` (set in `environment:`) tells `ConfigManager` where to find it inside the container.

Why a directory mount and not `./.env:/app/.env`: `ConfigManager.update_many()` (UI saves, legacy-config migration) writes via `python-dotenv`'s `set_key()`, which always does temp-file-then-`os.replace()` for atomicity — replacing a bind-mounted **single file** this way fails with `OSError: [Errno 16] Device or resource busy` (you can't rename over an active mount point), and crashes the app at boot if it happens during the legacy migration. Mounting the parent directory instead avoids this entirely — `.env` becomes a normal file inside a directory mount, where rename-in-place works fine. Config no longer arrives via `env_file`/OS env vars — the container reads `.env` directly off the mounted directory, so UI edits and hot-reload work identically in Docker and local dev.

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

**UI-editable vs manual-only settings:** a config key belongs in the `/config` editor (`app/ui/pages.py`) iff BOTH hold: (1) it's hot-reload capable (lives in `app/config.py`'s `_DEFAULTS`, applies without restart), AND (2) it isn't a trust-boundary/security control over the dashboard's own session. `PORT`/`HOST`/`DEV` fail (1) — restart-required, not even in `_DEFAULTS`. `TRUSTED_PROXIES` and `AUTH_SECURE_COOKIE` fail (2): both are hot-reload, but deliberately excluded from the UI — the panel itself sits behind the session these settings gate, so a compromised dashboard cookie must not be able to weaken the auth protecting it. Both stay manual-`.env`-edit-only. Secrets (`LLM_API_KEY`, `API_AUTH_TOKEN`) don't trip (2) — they're credentials toward external parties (LLM provider, API callers), not controls over the dashboard session — so they stay UI-editable via the existing write-only masked pattern (`type=password` input, blank = keep existing, masked on display via `config.get_public()`/`_SECRET_KEYS`). This rule is enforced by convention only (no code-level validation) — apply it when adding new config keys.

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
- **Timezone-aware timestamps:** Any code formatting a timestamp for display must use `zoneinfo.ZoneInfo(config.get("TZ", "UTC"))`, never bare `datetime.now()`/`datetime.fromtimestamp()` (those use the OS/container local time, which defaults to UTC and silently ignores the user's actual timezone). Stored timestamps stay as raw `time.time()` epoch floats (timezone-agnostic by definition) — only the display layer needs `zoneinfo`. Requires the `tzdata` package (in `requirements.txt`) since Windows has no built-in IANA tz database.
- **NiceGUI import order:** `from .ui import pages as _ui_pages` must be imported BEFORE `ui.run_with(app)` and must NOT use `import app.ui.pages` (absolute import shadows the `app = FastAPI(...)` variable).
- **NiceGUI navigation paths:** `ui.navigate.to()` prepends the mount path `/ui` automatically. Use paths relative to the NiceGUI root (e.g. `/config` not `/ui/config`). To navigate to FastAPI routes use `ui.run_javascript("window.location.href='/route'")`.
- **NiceGUI dark mode persistence:** Use `from nicegui import app as ng_app` and `ng_app.storage.user` — never `ui.dark_mode(True)` hardcoded (resets theme on every page load). `ui.storage` does not exist. Note this only persists the *value* correctly if `NICEGUI_STORAGE_PATH` itself survives a container recreate — see the Docker note below.
- **NiceGUI storage path in Docker:** `app.storage.user` (dark mode, etc.) is written to disk at `NICEGUI_STORAGE_PATH` (default `.nicegui/`, relative to cwd — a NiceGUI-internal setting, unrelated to `ConfigManager`). In Docker this defaults to an ephemeral path inside the container filesystem, wiped on every `docker compose up`/container recreate — set `NICEGUI_STORAGE_PATH=/app/data/.nicegui` (both compose files already do) so it lands under the bind-mounted `./data`, or the theme (and any other per-user UI state) silently resets to default on every restart.

## Runtime data (gitignored)

- `data/metrics.db` — SQLite request history
- `data/auth.json` — dashboard password hash + JWT secret
- `data/config.json.migrated` — present only after upgrading from the old JSON-override scheme; harmless, safe to delete once confirmed `.env` has the migrated values
- `data/.nicegui/` — NiceGUI's `app.storage.user`/`app.storage.general` persistence (dark mode, etc.), see `NICEGUI_STORAGE_PATH` above

## Environment Variables

See `.env.example`. Key vars:
- `PORT` — host listen port (default 8088). Used by `docker-compose*.yml` (`${PORT:-8088}` in the `ports:` mapping and Dockerfile `CMD`) and, for local dev, as the `--port` default in `app/main.py`'s `__main__` block (only when run via `python -m app.main`; `--port` CLI flag overrides it). Restart/re-`up` required to change.
- `HOST` — bare-metal-only bind address for local dev (default `127.0.0.1`, safe-by-default: reachable only from this machine). `--host` CLI flag overrides it. Set to `0.0.0.0` to expose on LAN. Not read in Docker (the container's `CMD` always hardcodes `--host 0.0.0.0` for Docker-networking reasons — see `BIND_HOST` below for the Docker-side exposure decision). Restart required.
- `BIND_HOST` — Docker-only: the address `docker-compose.yml`'s `ports:` mapping publishes on (default `127.0.0.1`, safe-by-default). Set to `0.0.0.0` to expose on LAN / behind a reverse proxy on another host / directly on the internet. Not used by `docker-compose-dev.yml` (intentionally open on all interfaces for local manual testing). Requires `docker compose up` to re-apply.
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_TIMEOUT`
- `LLM_MAX_PROMPT_CHARS` — truncates markdown sent to LLM (default 8000 chars ≈ 2700 tokens)
- `API_AUTH_TOKEN` — if set, all `/scrape*` endpoints require `Authorization: Bearer <token>`
- `SCRAPE_TIMEOUT` — global scraping timeout in seconds (default 300)
- `RATE_LIMIT` — per-IP rate limit (default `20/minute`), hot-reload (re-evaluated per-request), no restart needed
- `METRICS_RETENTION_DAYS` — days of request history kept in `data/metrics.db` before pruning (default 30), checked every 6h, no restart needed
- `DEBUG` — saves debug artifacts to `debug/`
- `TRUSTED_PROXIES` — comma-separated IPs allowed to set `CF-Connecting-IP`/`X-Real-IP`/`X-Forwarded-For` for client IP resolution (default `127.0.0.1`). Hot-reload, no restart needed.
- `AUTH_SECURE_COOKIE` — set to `1`/`true`/`yes` to force the `Secure` flag on the dashboard session cookie (auto-enabled behind HTTPS proxies via `X-Forwarded-Proto`). Hot-reload, no restart needed.
- `TZ` — IANA timezone name (e.g. `Europe/Rome`, default `UTC`) used to render dashboard timestamps (`app/ui/pages.py` via `zoneinfo.ZoneInfo`, falls back to UTC with a logged warning if invalid). Also passed to the Docker container's `environment:` for the container's own OS-level clock. App-side use is hot-reload; the container OS clock only picks up a changed `TZ` on container restart.
- `ENV_FILE` — Docker-only, **not** a `.env` entry itself: set in `docker-compose*.yml`'s `environment:` to `/app/hostcfg/.env`, tells `ConfigManager._resolve_env_path()` where to find `.env` inside the container (the Docker `CMD` invokes `uvicorn` directly, bypassing the `--env-file` CLI flag path used by local dev). Don't set this locally — leave `ConfigManager` to auto-discover `.env` via `--env-file`/nearest-file lookup.
- `NICEGUI_STORAGE_PATH` — Docker-only, a NiceGUI-internal setting (read by the `nicegui` package itself, not `ConfigManager`): set in `docker-compose*.yml`'s `environment:` to `/app/data/.nicegui` so `app.storage.user` (dark mode, etc.) survives container recreates via the bind-mounted `./data`. Don't set this locally — NiceGUI's own default (`.nicegui/` relative to cwd) already persists fine across local dev restarts.

Note: all config keys above are read exclusively through `ConfigManager` (`.env` + hardcoded defaults) — there is no OS-environment-variable override layer and no separate JSON override file. `TRUSTED_PROXIES`/`AUTH_SECURE_COOKIE` used to be read via raw `os.getenv` outside ConfigManager (frozen at boot); they were migrated onto `ConfigManager` so they hot-reload like everything else.
