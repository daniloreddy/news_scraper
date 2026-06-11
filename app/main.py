"""
news-scraper — FastAPI microservice per scraping news da Diablo Immortal
Invocabile da n8n via HTTP POST /scrape
"""

import os
import logging
import asyncio
import sys

# Fix per Windows: Playwright richiede ProactorEventLoop per gestire i sottoprocessi
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from .scraper import scrape_latest_news, scrape_article

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN")
SCRAPE_TIMEOUT = float(os.getenv("SCRAPE_TIMEOUT", "300"))


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Verifica la validità del Bearer Token se impostato in ambiente."""
    if API_AUTH_TOKEN:
        if credentials is None or credentials.credentials != API_AUTH_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API Auth Token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return credentials.credentials if credentials else None


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
)


class ScrapeRequest(BaseModel):
    url: str = "https://diabloimmortal.blizzard.com/en-us#news"
    # In futuro: altri siti possono avere config diversa
    max_articles: int = 1


class ArticleResult(BaseModel):
    title: str
    url: str
    published_date: Optional[str]
    content: str  # testo estratto (per LLM)
    thumbnail_url: Optional[str]


class ArticleRequest(BaseModel):
    url: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/scrape", response_model=list[ArticleResult])
def scrape(req: ScrapeRequest, token: Optional[str] = Depends(verify_token)):
    """
    Scrapa la pagina principale e restituisce le ultime N notizie
    (default: solo l'ultima). Il contenuto grezzo è pronto per
    essere passato all'LLM node di n8n per il riassunto.
    """
    logger.info(f"Scraping: {req.url} (max {req.max_articles})")

    # Esegue in un thread separato: creiamo un nuovo loop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        articles = loop.run_until_complete(
            asyncio.wait_for(
                scrape_latest_news(req.url, req.max_articles), timeout=SCRAPE_TIMEOUT
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
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        loop.close()


@app.post("/scrape/article", response_model=ArticleResult)
def scrape_single(req: ArticleRequest, token: Optional[str] = Depends(verify_token)):
    """
    Scrapa un singolo articolo dato il suo URL.
    Utile per test o per riprocessare un articolo già noto.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        return loop.run_until_complete(
            asyncio.wait_for(scrape_article(req.url), timeout=SCRAPE_TIMEOUT)
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Scraping timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        loop.close()
