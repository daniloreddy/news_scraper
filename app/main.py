"""
news-scraper — FastAPI microservice per scraping news
Invocabile da n8n via HTTP POST /scrape
Dashboard di monitoraggio su /ui/
"""

from __future__ import annotations

from dotenv import load_dotenv
from redberry_webkit.env_resolver import resolve_env_path

_env_path = resolve_env_path()
load_dotenv(_env_path)

import argparse  # noqa: E402
import asyncio  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections.abc import AsyncIterator, Callable, Coroutine  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from logging.handlers import RotatingFileHandler  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from nicegui import ui  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402
from redberry_webkit.auth import client_ip, purge_loop  # noqa: E402
from slowapi import Limiter, _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402
from starlette.middleware.base import RequestResponseEndpoint  # noqa: E402

from . import metrics  # noqa: E402
from .config import config  # noqa: E402
from .logging_filters import CredentialFilter  # noqa: E402
from .metrics import RequestRecord  # noqa: E402
from .scraper import ScrapeResult, scrape_article, scrape_latest_news  # noqa: E402
from .ui.router import TRUSTED_PROXIES  # noqa: E402
from .ui.router import auth as ui_auth  # noqa: E402
from .ui.router import router as ui_router  # noqa: E402
from .url_safety import validate_url  # noqa: E402

# Fix per Windows: Playwright richiede ProactorEventLoop per gestire i sottoprocessi
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEV = os.getenv("DEV", "false").lower() in ("true", "1", "yes")
CONFIG_RELOAD_INTERVAL_S = 5

_stream_handler = logging.StreamHandler()
_file_handler = RotatingFileHandler(LOG_DIR / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
_credential_filter = CredentialFilter()
_stream_handler.addFilter(_credential_filter)
_file_handler.addFilter(_credential_filter)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[_stream_handler, _file_handler],
)
logger = logging.getLogger(__name__)
logger.info("Using .env=%s", _env_path)

security = HTTPBearer(auto_error=False)

# Limits concurrent Playwright browser launches to prevent OOM
BROWSER_SEMAPHORE = threading.Semaphore(3)


def _rate_limit_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return client_ip(request.headers, host, TRUSTED_PROXIES)


limiter = Limiter(key_func=_rate_limit_key)


def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str | None:
    """Verifica la validità del Bearer Token se impostato in configurazione.
    Read fresh from ConfigManager on every call so UI changes take effect
    immediately, without requiring a process restart."""
    token = config.get("API_AUTH_TOKEN") or None
    if token:
        if credentials is None or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API Auth Token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return credentials.credentials if credentials else None


async def _purge_old_metrics_periodically() -> None:
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            await metrics.purge_old(config.get_int("METRICS_RETENTION_DAYS", 30))
        except Exception:
            logger.exception("Purge periodica delle metriche fallita")


async def _config_reload_loop(interval_s: int) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            config.reload_if_stale()
        except Exception:
            logger.exception("Reload periodico della configurazione fallito")


def _crash_on_task_error(task: asyncio.Task[None]) -> None:
    # A background loop task (purge_loop, metrics purge, config reload) is only ever
    # supposed to end via .cancel() at shutdown. If it dies from an unhandled exception
    # instead, asyncio would otherwise just log "Task exception was never retrieved" and
    # keep the process alive with silently broken rate-limit purging / config hot-reload
    # — worse than crashing.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical("Background task %s died unexpectedly, exiting", task.get_name(), exc_info=exc)
        os._exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await metrics.init_db()
    purge_task = asyncio.create_task(purge_loop(ui_auth))
    purge_task.add_done_callback(_crash_on_task_error)
    metrics_purge_task = asyncio.create_task(_purge_old_metrics_periodically())
    metrics_purge_task.add_done_callback(_crash_on_task_error)
    config_task = asyncio.create_task(_config_reload_loop(CONFIG_RELOAD_INTERVAL_S))
    config_task.add_done_callback(_crash_on_task_error)

    try:
        logger.info("Verifica/Installazione automatica di Playwright Chromium...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        logger.info("Playwright Chromium pronto.")
    except (subprocess.CalledProcessError, OSError) as e:
        logger.warning("Installazione automatica Playwright fallita (continua comunque): %s", e)
    yield
    purge_task.cancel()
    metrics_purge_task.cancel()
    config_task.cancel()


app = FastAPI(
    title="news-scraper",
    description="Scraping news per n8n · Dashboard su /ui/",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if DEV else None,
    redoc_url="/redoc" if DEV else None,
    openapi_url="/openapi.json" if DEV else None,
)
app.state.limiter = limiter
# slowapi lacks precise stubs for this handler signature, hence the ignore below.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)


_UI_PREFIX = "/ui"
_LOGIN_PATHS = {"/login", "/auth/login", "/auth/logout"}
_UI_BYPASS_PREFIXES = (f"{_UI_PREFIX}/_nicegui",)


@app.middleware("http")
async def _auth_gate(request: Request, call_next: RequestResponseEndpoint) -> Response:
    path = request.url.path
    if path in _LOGIN_PATHS or any(path.startswith(p) for p in _UI_BYPASS_PREFIXES):
        return await call_next(request)
    if path == _UI_PREFIX or path.startswith(_UI_PREFIX + "/"):
        token = request.cookies.get(ui_auth.cookie_name, "")
        if ui_auth.verify_token(token):
            return await call_next(request)
        return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


# --- Static files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- UI login/logout router ---
app.include_router(ui_router)


@app.get("/health")
@limiter.limit("100/minute")
async def health(request: Request) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


# --- Pydantic models ---


class ScrapeRequest(BaseModel):
    url: str = "https://diabloimmortal.blizzard.com/en-us#news"
    max_articles: int = Field(1, ge=1, le=10)

    @field_validator("url")
    @classmethod
    def url_must_be_safe(cls, v: str) -> str:
        return validate_url(v)


class ArticleResult(BaseModel):
    title: str
    url: str
    published_date: str | None
    content: str
    thumbnail_url: str | None


class ArticleRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def url_must_be_safe(cls, v: str) -> str:
        return validate_url(v)


# --- Scraping endpoints ---


def _run_scrape_sync(
    endpoint: str,
    url: str,
    coro: Coroutine[Any, Any, ScrapeResult],
    extract: Callable[[ScrapeResult], Any],
    empty_error: str | None = None,
) -> Any:
    """Bridges a sync FastAPI endpoint with the async scraper coroutine: runs it
    in its own event loop bounded by BROWSER_SEMAPHORE, records metrics, and maps
    timeouts/errors/empty results to the right HTTPException. `empty_error`, if
    given, turns an empty `result.articles` into a 404 instead of calling `extract`.
    """
    with BROWSER_SEMAPHORE:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _run() -> Any:
            start = time.time()
            result: ScrapeResult | None = None
            status_str = "ok"
            error_msg: str | None = None
            http_exc: HTTPException | None = None

            try:
                result = await asyncio.wait_for(coro, timeout=config.get_float("SCRAPE_TIMEOUT", 300))
                if empty_error and not result.articles:
                    status_str = "error"
                    error_msg = empty_error
                    http_exc = HTTPException(status_code=404, detail=empty_error)
            except TimeoutError:
                status_str = "timeout"
            except Exception as e:
                status_str = "error"
                error_msg = str(e)
                logger.error("Errore scraping (%s): %s", endpoint, e)
            finally:
                await metrics.record(
                    RequestRecord(
                        endpoint=endpoint,
                        url=url,
                        status=status_str,
                        duration=time.time() - start,
                        error_msg=error_msg,
                        prompt_tokens=result.prompt_tokens if result else 0,
                        completion_tokens=result.completion_tokens if result else 0,
                    )
                )

            if status_str == "timeout":
                raise HTTPException(status_code=504, detail="Scraping timeout")
            if http_exc:
                raise http_exc
            if status_str == "error":
                raise HTTPException(status_code=500, detail="Internal server error")
            return extract(result)  # type: ignore[arg-type]

        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()


@app.post("/scrape", response_model=list[ArticleResult])
@limiter.limit(lambda: config.get("RATE_LIMIT", "20/minute"))
def scrape(
    request: Request,
    req: ScrapeRequest,
    token: str | None = Depends(verify_token),
) -> Any:
    """
    Scrapa la pagina principale e restituisce le ultime N notizie.
    """
    logger.info("Scraping: %s (max %d)", req.url, req.max_articles)
    return _run_scrape_sync(
        endpoint="/scrape",
        url=req.url,
        coro=scrape_latest_news(req.url, req.max_articles),
        extract=lambda r: r.articles,
        empty_error="Nessuna news trovata",
    )


@app.post("/scrape/article", response_model=ArticleResult)
@limiter.limit(lambda: config.get("RATE_LIMIT", "20/minute"))
def scrape_single(
    request: Request,
    req: ArticleRequest,
    token: str | None = Depends(verify_token),
) -> Any:
    """
    Scrapa un singolo articolo dato il suo URL.
    """
    return _run_scrape_sync(
        endpoint="/scrape/article",
        url=req.url,
        coro=scrape_article(req.url),
        extract=lambda r: r.articles[0],
    )


# --- NiceGUI mount ---
from .ui import pages as _ui_pages  # noqa: F401, E402 — registers @ui.page decorators; must follow app init

_fastapi_app = app  # keep explicit reference before ui.run_with shadows nothing
ui.run_with(_fastapi_app, mount_path="/ui", storage_secret=ui_auth.ui_storage_secret)


if __name__ == "__main__":
    default_port = int(os.getenv("PORT", "8088"))
    default_host = os.getenv("HOST", "127.0.0.1")

    parser = argparse.ArgumentParser(description="news-scraper — FastAPI microservice per scraping news")
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--host", type=str, default=default_host)
    parser.add_argument("--dev", action=argparse.BooleanOptionalAction, default=DEV)
    parser.add_argument("--env-file", type=str, default=None)
    args = parser.parse_args()

    # loop="asyncio" preserves the WindowsProactorEventLoopPolicy set above —
    # required for Playwright subprocess support. Do not change to "auto"/"uvloop".
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        reload_dirs=[str(PROJECT_ROOT / "app"), str(PROJECT_ROOT / "static")] if args.dev else None,
        loop="asyncio",
    )
