"""
news-scraper — FastAPI microservice per scraping news
Invocabile da n8n via HTTP POST /scrape
Dashboard di monitoraggio su /ui/
"""

import asyncio
import ipaddress
import logging
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlparse

# Fix per Windows: Playwright richiede ProactorEventLoop per gestire i sottoprocessi
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from nicegui import ui
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .config import config
from . import metrics
from .metrics import RequestRecord
from .net import resolve_client_ip
from .scraper import scrape_latest_news, scrape_article, ScrapeResult
from .ui.router import router as ui_router, auth as ui_auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# Limits concurrent Playwright browser launches to prevent OOM
BROWSER_SEMAPHORE = threading.Semaphore(3)

limiter = Limiter(key_func=resolve_client_ip)


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


async def _purge_ui_auth_blocks_periodically() -> None:
    while True:
        await asyncio.sleep(600)
        ui_auth.purge_expired_blocks()


async def _purge_old_metrics_periodically() -> None:
    while True:
        await asyncio.sleep(6 * 3600)
        await metrics.purge_old(config.get_int("METRICS_RETENTION_DAYS", 30))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await metrics.init_db()
    purge_task = asyncio.create_task(_purge_ui_auth_blocks_periodically())
    metrics_purge_task = asyncio.create_task(_purge_old_metrics_periodically())

    try:
        import subprocess

        logger.info("Verifica/Installazione automatica di Playwright Chromium...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        logger.info("Playwright Chromium pronto.")
    except Exception as e:
        logger.warning(
            "Installazione automatica Playwright fallita (continua comunque): %s", e
        )
    yield
    purge_task.cancel()
    metrics_purge_task.cancel()


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
async def ui_auth_gate(request: Request, call_next):
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

        async def _run():
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
ui.run_with(_fastapi_app, mount_path="/ui", storage_secret=ui_auth._secret + "_ng")
