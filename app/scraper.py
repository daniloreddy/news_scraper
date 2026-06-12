"""
Logica di scraping con Playwright.
- scrape_latest_news: apre la homepage DI, estrae i link delle news
- scrape_article: apre un articolo, estrae titolo, data, testo pulito
"""

import os
import re
import json
import logging
import tempfile
from typing import Optional, List
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page
from markitdown import MarkItDown
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Carica variabili d'ambiente da .env
load_dotenv()

# Configurazione LLM
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
LLM_API_KEY = os.getenv("LLM_API_KEY") or ""
LLM_MODEL = os.getenv("LLM_MODEL") or "gpt-4o-mini"

temp_val = os.getenv("LLM_TEMPERATURE")
LLM_TEMPERATURE = float(temp_val) if temp_val and temp_val.strip() else 0.1

timeout_val = os.getenv("LLM_TIMEOUT")
LLM_TIMEOUT = float(timeout_val) if timeout_val and timeout_val.strip() else 60.0

debug_val = os.getenv("DEBUG")
DEBUG = debug_val.lower() == "true" if debug_val else False

# Inizializza client OpenAI asincrono con custom default headers
llm_client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    timeout=LLM_TIMEOUT,
    default_headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    },
)

# Inizializza MarkItDown
md_converter = MarkItDown()


def _sanitize_markdown(text: str) -> str:
    """Rimuove artefatti HTML residui dal Markdown per ridurre la superficie di prompt injection."""
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"<iframe[^>]*>.*?</iframe>", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"data:[^;]+;base64,[A-Za-z0-9+/=]+", "[DATA_URI_REMOVED]", text)
    text = re.sub(r"javascript:", "", text, flags=re.IGNORECASE)
    return text


def _save_debug_file(filename: str, content: str):
    """Salva il contenuto in una cartella di debug se DEBUG è attivo."""
    if not DEBUG:
        return
    try:
        os.makedirs("debug", exist_ok=True)
        filepath = os.path.join("debug", filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[DEBUG] File salvato: {filepath}")
    except Exception as e:
        logger.warning(f"Impossibile salvare il file di debug {filename}: {e}")


def _preprocess_html(html_content: str, base_url: str) -> str:
    """Pre-elabora l'HTML convertendo i tag custom con href in tag <a> standard e rendendo gli URL assoluti."""
    from urllib.parse import urlparse

    try:
        soup = BeautifulSoup(html_content, "html.parser")
        parsed_base = urlparse(base_url)
        base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

        for tag in soup.find_all(lambda t: t.has_attr("href")):
            href = str(tag["href"]).strip()
            # Rendi l'URL assoluto se è relativo
            if href.startswith("/"):
                href = f"{base_origin}{href}"
            tag["href"] = href

            # Se non è un tag 'a', lo convertiamo in 'a'
            if tag.name != "a":
                new_tag = soup.new_tag("a", href=href)
                new_tag.extend(tag.contents)
                tag.replace_with(new_tag)

        return str(soup)
    except Exception as e:
        logger.warning(f"Errore durante il preprocessing HTML: {e}")
        return html_content


class ArticleExtraction(BaseModel):
    title: str = Field(description="Titolo della notizia o dell'articolo.")
    url: str = Field(
        description="URL assoluto completo che porta all'articolo. Se è un path relativo, convertilo in assoluto."
    )
    published_date: Optional[str] = Field(
        description="Data di pubblicazione estratta, possibilmente in formato stringa leggibile.",
        default=None,
    )
    thumbnail_url: Optional[str] = Field(
        description="URL dell'immagine di copertina, se presente.", default=None
    )


class ArticlesList(BaseModel):
    articles: List[ArticleExtraction]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=20))
async def _call_llm_api(messages: list) -> Optional[str]:
    """Chiama l'API LLM con retry su errori transitori."""
    response = await llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=LLM_TEMPERATURE,
    )
    return response.choices[0].message.content


async def _extract_articles_with_llm(
    markdown_text: str, base_url: str, max_articles: int
) -> List[dict]:
    """Usa l'LLM per estrarre la lista di articoli dal Markdown."""
    if not LLM_API_KEY:
        logger.warning(
            "LLM_API_KEY non impostata. L'estrazione potrebbe fallire se il server richiede autenticazione."
        )

    prompt = f"""
    Analizza il seguente contenuto Markdown estratto da una pagina web ({base_url}).
    Il tuo compito è trovare i link che puntano agli articoli di notizie (news).
    Estrai ESATTAMENTE i primi {max_articles} articoli più recenti o rilevanti che trovi.

    Per ogni articolo devi restituire:
    - title: Il titolo della notizia.
    - url: L'URL completo dell'articolo. Assicurati che sia assoluto (es. se trovi '/en-us/article/123', e la base è '{base_url}', l'URL completo potrebbe essere 'https://news.blizzard.com/en-us/article/123').
    - published_date: La data di pubblicazione, se presente nel testo vicino al link.
    - thumbnail_url: L'URL di una eventuale immagine associata, se presente nel markdown.

    Rispondi SOLO con i dati richiesti.

    Contenuto Markdown:
    {markdown_text[:15000]}
    """

    messages = [
        {
            "role": "system",
            "content": "Sei un assistente specializzato nell'estrazione di dati strutturati da pagine web Markdown. Rispondi SEMPRE e SOLO con un oggetto JSON valido.",
        },
        {
            "role": "user",
            "content": prompt
            + '\n\nRispondi con un JSON che abbia questa struttura: {"articles": [{"title": "...", "url": "...", "published_date": "...", "thumbnail_url": "..."}]}',
        },
    ]

    try:
        logger.info(
            f"Invocazione LLM ({LLM_MODEL}) per estrazione di {max_articles} articoli..."
        )

        content = await _call_llm_api(messages)
        if not content:
            return []

        _save_debug_file("llm_output.json", content)

        parsed = ArticlesList.model_validate(json.loads(content))
        return [a.model_dump() for a in parsed.articles[:max_articles]]

    except Exception as e:
        logger.error(f"Errore durante l'estrazione LLM: {e}")
        return []


async def scrape_latest_news(url: str, max_articles: int = 1) -> list[dict]:
    """
    1. Scarica l'HTML con Playwright.
    2. Lo converte in Markdown.
    3. Usa l'LLM per trovare i link agli articoli.
    4. Scrapa i singoli articoli.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            logger.info(f"Apertura homepage: {url}")
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Estrai HTML e converti
            html_content = await page.content()
            _save_debug_file("playwright_output.html", html_content)

            # Pre-processamento per normalizzare i tag e gli URL
            preprocessed_html = _preprocess_html(html_content, url)
            _save_debug_file("playwright_preprocessed.html", preprocessed_html)

            logger.info("Conversione HTML -> Markdown")
            with tempfile.NamedTemporaryFile(
                suffix=".html", delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                temp_file = tmp.name
                tmp.write(preprocessed_html)
            try:
                md_result = md_converter.convert(temp_file)
                markdown_text = md_result.text_content
                _save_debug_file("markitdown_output.md", markdown_text)
            finally:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

            # Estrai info base con LLM
            articles_meta = await _extract_articles_with_llm(
                _sanitize_markdown(markdown_text), url, max_articles
            )
            logger.info(f"L'LLM ha estratto {len(articles_meta)} link ad articoli.")

            results = []
            for meta in articles_meta:
                try:
                    # Vai a prendere il contenuto completo dell'articolo
                    article_data = await _scrape_article_page(page, meta["url"])

                    # Merge dei dati: se l'LLM ha trovato una thumbnail o data migliore, tienila,
                    # altrimenti usa quella presa dalla pagina specifica.
                    article_data["title"] = meta.get("title") or article_data["title"]
                    if not article_data.get("published_date") and meta.get(
                        "published_date"
                    ):
                        article_data["published_date"] = meta["published_date"]
                    if not article_data.get("thumbnail_url") and meta.get(
                        "thumbnail_url"
                    ):
                        article_data["thumbnail_url"] = meta["thumbnail_url"]

                    results.append(article_data)
                except Exception as e:
                    logger.warning(
                        f"Errore nello scraping del singolo articolo {meta['url']}: {e}"
                    )

            return results

        finally:
            await browser.close()


async def scrape_article(url: str) -> dict:
    """Entry point per scrapare un singolo articolo (usato dall'endpoint /scrape/article)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        try:
            return await _scrape_article_page(page, url)
        finally:
            await browser.close()


async def _scrape_article_page(page: Page, url: str) -> dict:
    """
    Logica interna: apre un articolo ed estrae il contenuto pulito.
    Usa MarkItDown per convertire l'HTML in Markdown.
    Estrazione di metadati minimi (titolo, immagine) via LLM o logica base.
    """
    logger.info(f"Scraping articolo: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # Estrai HTML e converti
    html_content = await page.content()

    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        temp_file = tmp.name
        tmp.write(html_content)
    try:
        md_result = md_converter.convert(temp_file)
        markdown_text = md_result.text_content
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)

    # Pulisci whitespace multipli
    content = re.sub(r"\n{3,}", "\n\n", markdown_text)

    # Raccogli metadati basici rimasti nella pagina
    title = await page.title()
    thumbnail_url = await page.evaluate("""() => {
        const og = document.querySelector("meta[property='og:image']");
        return og ? og.getAttribute('content') : null;
    }""")

    if len(content) > 8000:
        logger.warning(
            f"Contenuto articolo troncato a 8000 chars (originale: {len(content)})"
        )
    return {
        "title": title.split("—")[0].strip() if "—" in title else title.strip(),
        "url": url,
        "published_date": None,
        "content": content[:8000],
        "thumbnail_url": thumbnail_url,
    }
