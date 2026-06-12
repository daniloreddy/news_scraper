"""
news-scraper — FastAPI microservice per scraping news da Diablo Immortal
Invocabile da n8n via HTTP POST /scrape
"""

import os
import logging
import asyncio
import secrets
import ipaddress
import sys
import threading
from urllib.parse import urlparse

# Fix per Windows: Playwright richiede ProactorEventLoop per gestire i sottoprocessi
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from .scraper import scrape_latest_news, scrape_article

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN")
SCRAPE_TIMEOUT = float(os.getenv("SCRAPE_TIMEOUT", "300"))
RATE_LIMIT = os.getenv("RATE_LIMIT", "20/minute")

# Limits concurrent Playwright browser launches to prevent OOM
BROWSER_SEMAPHORE = threading.Semaphore(3)


def get_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")  # Cloudflare
    if cf_ip:
        return cf_ip
    real_ip = request.headers.get("X-Real-IP")  # nginx
    if real_ip:
        return real_ip
    forwarded_for = request.headers.get("X-Forwarded-For")  # Apache / nginx
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=get_client_ip)


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Verifica la validità del Bearer Token se impostato in ambiente."""
    if API_AUTH_TOKEN:
        if credentials is None or not secrets.compare_digest(
            credentials.credentials, API_AUTH_TOKEN
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
        pass  # domain name, not an IP address — allowed
    else:
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
        ):
            raise ValueError("URL hostname not allowed")
    return v


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-installazione di Playwright Chromium all'avvio
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
            f"Installazione automatica Playwright fallita (continua comunque): {e}"
        )
    yield


app = FastAPI(
    title="news-scraper",
    description="Scraping news Diablo Immortal per n8n",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]


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


@app.get("/health")
@limiter.limit("100/minute")
async def health(request: Request):
    return {"status": "ok"}


@app.post("/scrape", response_model=list[ArticleResult])
@limiter.limit(RATE_LIMIT)
def scrape(
    request: Request,
    req: ScrapeRequest,
    token: Optional[str] = Depends(verify_token),
):
    """
    Scrapa la pagina principale e restituisce le ultime N notizie
    (default: solo l'ultima). Il contenuto grezzo è pronto per
    essere passato all'LLM node di n8n per il riassunto.
    """
    logger.info(f"Scraping: {req.url} (max {req.max_articles})")

    with BROWSER_SEMAPHORE:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            articles = loop.run_until_complete(
                asyncio.wait_for(
                    scrape_latest_news(req.url, req.max_articles),
                    timeout=SCRAPE_TIMEOUT,
                )
            )
            if not articles:
                raise HTTPException(status_code=404, detail="Nessuna news trovata")
            return articles
        except HTTPException:
            raise
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Scraping timeout")
        except Exception as e:
            logger.error(f"Errore scraping: {e}")
            raise HTTPException(status_code=500, detail="Internal server error") from e
        finally:
            loop.close()


@app.post("/scrape/article", response_model=ArticleResult)
@limiter.limit(RATE_LIMIT)
def scrape_single(
    request: Request,
    req: ArticleRequest,
    token: Optional[str] = Depends(verify_token),
):
    """
    Scrapa un singolo articolo dato il suo URL.
    Utile per test o per riprocessare un articolo già noto.
    """
    with BROWSER_SEMAPHORE:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            return loop.run_until_complete(
                asyncio.wait_for(scrape_article(req.url), timeout=SCRAPE_TIMEOUT)
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Scraping timeout")
        except Exception as e:
            logger.error(f"Errore scraping articolo: {e}")
            raise HTTPException(status_code=500, detail="Internal server error") from e
        finally:
            loop.close()
