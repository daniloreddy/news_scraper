"""
news-scraper — FastAPI microservice per scraping news
Invocabile da n8n via HTTP POST /scrape
Dashboard di monitoraggio su /ui/
"""

import argparse
import asyncio
import ipaddress
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlparse

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from nicegui import ui
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# Stage 1 — lightweight parse at import time, before any other env var is read.
# Required because uvicorn re-imports app.main on every reload-worker import,
# so .env must be (re)loaded on every import, not only inside __main__.
_env_parser = argparse.ArgumentParser(add_help=False)
_env_parser.add_argument("--env-file", type=str, default=None)
_env_args, _ = _env_parser.parse_known_args()
load_dotenv(_env_args.env_file)

# Fix per Windows: Playwright richiede ProactorEventLoop per gestire i sottoprocessi
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Must run before any app-internal import below: importing .config triggers
# ConfigManager()'s singleton __new__ as a side effect, which logs at INFO/WARNING
# level — without basicConfig() first, the root logger has no level/handler yet
# and those messages are silently dropped (observed in production: the "using
# .env=..." startup log never appeared, even though the config was loading fine).
logging.basicConfig(level=logging.INFO)

from redberry_webkit.auth import client_ip, purge_loop  # noqa: E402

from .config import config  # noqa: E402 — must follow stage-1 load_dotenv() above
from . import metrics  # noqa: E402
from .metrics import RequestRecord  # noqa: E402
from .scraper import scrape_latest_news, scrape_article, ScrapeResult  # noqa: E402
from .ui.router import (  # noqa: E402
    router as ui_router,
    auth as ui_auth,
    TRUSTED_PROXIES,
)
from .logging_filters import CredentialFilter  # noqa: E402

for _handler in logging.getLogger().handlers:
    _handler.addFilter(CredentialFilter())
logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# Limits concurrent Playwright browser launches to prevent OOM
BROWSER_SEMAPHORE = threading.Semaphore(3)

def _rate_limit_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return client_ip(request.headers, host, TRUSTED_PROXIES)


limiter = Limiter(key_func=_rate_limit_key)


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Verifica la validità del Bearer Token se impostato in configurazione.
    Read fresh from ConfigManager on every call so UI changes take effect
    immediately, without requiring a process restart."""
    token = config.get("API_AUTH_TOKEN") or None
    if token:
        if credentials is None or not secrets.compare_digest(
            credentials.credentials, token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API Auth Token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return credentials.credentials if credentials else None


def _validate_url(v: str) -> str:
    """Block non-http(s) schemes and private/loopback IP ranges (SSRF guard)."""
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL must have a valid hostname")
    if host.lower() in ("localhost", "0.0.0.0"):
        raise ValueError("URL hostname not allowed")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
        ):
            raise ValueError("URL hostname not allowed")
    return v


async def _purge_old_metrics_periodically() -> None:
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            await metrics.purge_old(config.get_int("METRICS_RETENTION_DAYS", 30))
        except Exception:
            logger.exception("Purge periodica delle metriche fallita")


async def _reload_config_periodically() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            config.reload_if_stale()
        except Exception:
            logger.exception("Reload periodico della configurazione fallito")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await metrics.init_db()
    purge_task = asyncio.create_task(purge_loop(ui_auth))
    metrics_purge_task = asyncio.create_task(_purge_old_metrics_periodically())
    config_reload_task = asyncio.create_task(_reload_config_periodically())

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
        logger.warning(
            "Installazione automatica Playwright fallita (continua comunque): %s", e
        )
    yield
    purge_task.cancel()
    metrics_purge_task.cancel()
    config_reload_task.cancel()


app = FastAPI(
    title="news-scraper",
    description="Scraping news per n8n · Dashboard su /ui/",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]


# --- Auth middleware for /ui/* ---

_UI_SOCKET_PREFIX = "/ui/socket.io"


@app.middleware("http")
async def ui_auth_gate(
    request: Request, call_next: Callable[[Request], Coroutine[Any, Any, Any]]
) -> Any:
    path = request.url.path
    if not path.startswith("/ui"):
        return await call_next(request)
    # Allow NiceGUI WebSocket/socket.io — auth is enforced at page level
    if path.startswith(_UI_SOCKET_PREFIX):
        return await call_next(request)
    token = request.cookies.get(ui_auth.cookie_name, "")
    if ui_auth.verify_token(token):
        return await call_next(request)
    if "websocket" in request.headers.get("upgrade", "").lower():
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return RedirectResponse(url="/login", status_code=302)


# --- Static files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- UI login/logout router ---
app.include_router(ui_router)


# --- Pydantic models ---


class ScrapeRequest(BaseModel):
    url: str = "https://diabloimmortal.blizzard.com/en-us#news"
    max_articles: int = Field(1, ge=1, le=10)

    @field_validator("url")
    @classmethod
    def url_must_be_safe(cls, v: str) -> str:
        return _validate_url(v)


class ArticleResult(BaseModel):
    title: str
    url: str
    published_date: Optional[str]
    content: str
    thumbnail_url: Optional[str]


class ArticleRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def url_must_be_safe(cls, v: str) -> str:
        return _validate_url(v)


# --- Scraping endpoints ---


@app.get("/health")
@limiter.limit("100/minute")
async def health(request: Request):
    return {"status": "ok"}


def _run_scrape_sync(
    endpoint: str,
    url: str,
    coro: Coroutine[Any, Any, ScrapeResult],
    extract: Callable[[ScrapeResult], Any],
    empty_error: Optional[str] = None,
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
            result: Optional[ScrapeResult] = None
            status_str = "ok"
            error_msg: Optional[str] = None
            http_exc: Optional[HTTPException] = None

            try:
                result = await asyncio.wait_for(
                    coro, timeout=config.get_float("SCRAPE_TIMEOUT", 300)
                )
                if empty_error and not result.articles:
                    status_str = "error"
                    error_msg = empty_error
                    http_exc = HTTPException(status_code=404, detail=empty_error)
            except asyncio.TimeoutError:
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
    token: Optional[str] = Depends(verify_token),
):
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
    token: Optional[str] = Depends(verify_token),
):
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
    default_dev = os.getenv("DEV", "false").lower() in ("true", "1", "yes")

    parser = argparse.ArgumentParser(
        description="news-scraper — FastAPI microservice per scraping news"
    )
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--host", type=str, default=default_host)
    parser.add_argument("--dev", action="store_true", default=default_dev)
    parser.add_argument("--env-file", type=str, default=None)
    args = parser.parse_args()

    # loop="asyncio" preserves the WindowsProactorEventLoopPolicy set above —
    # required for Playwright subprocess support. Do not change to "auto"/"uvloop".
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        loop="asyncio",
    )
