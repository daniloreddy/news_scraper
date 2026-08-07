"""
Logica di scraping con Playwright.
- scrape_latest_news: apre la homepage, estrae i link delle news via LLM
- scrape_article: apre un articolo, estrae titolo, data, testo pulito
"""

import asyncio
import io
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markitdown import MarkItDown
from playwright.async_api import Page, async_playwright
from pydantic import BaseModel
from pydantic import Field as PydanticField
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import config
from .url_safety import validate_url

logger = logging.getLogger(__name__)

md_converter = MarkItDown()

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _html_to_markdown(html: str) -> str:
    """Converts HTML to Markdown in-memory (no temp file on disk)."""
    stream = io.BytesIO(html.encode("utf-8"))
    return md_converter.convert_stream(stream, file_extension=".html").text_content


@asynccontextmanager
async def _new_browser_page() -> AsyncIterator[Page]:
    """Launches a Chromium page with the shared user agent; closes the browser
    on exit regardless of what happens inside the `with` block."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=_USER_AGENT)
            page = await context.new_page()
            yield page
        finally:
            await browser.close()


@dataclass
class ScrapeResult:
    articles: list[dict[str, Any]]
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class _LLMResult:
    content: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _sanitize_markdown(text: str) -> str:
    """Rimuove artefatti HTML residui dal Markdown per ridurre la superficie di prompt injection."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<iframe[^>]*>.*?</iframe>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"data:[^;]+;base64,[A-Za-z0-9+/=]+", "[DATA_URI_REMOVED]", text)
    text = re.sub(r"javascript:", "", text, flags=re.IGNORECASE)
    return text


def _save_debug_file(filename: str, content: str) -> None:
    if not config.get_bool("DEBUG"):
        return
    try:
        os.makedirs("debug", exist_ok=True)
        filepath = os.path.join("debug", filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("[DEBUG] File salvato: %s", filepath)
    except OSError as e:
        logger.warning("Impossibile salvare il file di debug %s: %s", filename, e)


def _preprocess_html(html_content: str, base_url: str) -> str:
    """Converte tag custom con href in <a> standard e rende gli URL assoluti."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        parsed = urlparse(base_url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"

        for tag in soup.find_all(lambda t: t.has_attr("href")):
            href = str(tag["href"]).strip()
            if href.startswith("/"):
                href = f"{base_origin}{href}"
            tag["href"] = href

            if tag.name != "a":
                new_tag = soup.new_tag("a", href=href)
                new_tag.extend(tag.contents)
                tag.replace_with(new_tag)

        return str(soup)
    except Exception as e:
        logger.warning("Errore durante il preprocessing HTML: %s", e)
        return html_content


class ArticleExtraction(BaseModel):
    title: str = PydanticField(description="Titolo della notizia o dell'articolo.")
    url: str = PydanticField(description="URL assoluto completo che porta all'articolo.")
    published_date: str | None = PydanticField(description="Data di pubblicazione estratta, se presente.", default=None)
    thumbnail_url: str | None = PydanticField(description="URL dell'immagine di copertina, se presente.", default=None)


class ArticlesList(BaseModel):
    articles: list[ArticleExtraction]


def _is_transient_llm_error(exc: BaseException) -> bool:
    """Retry only on network-level failures and 5xx responses. A 4xx (bad request,
    auth failure, invalid model...) will fail identically on every attempt — retrying
    it just wastes time and, on repeated calls, tokens."""
    if isinstance(exc, httpx.TimeoutException | httpx.ConnectError | httpx.RemoteProtocolError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=20),
    retry=retry_if_exception(_is_transient_llm_error),
    reraise=True,
)
async def _call_llm_api(messages: list[dict[str, Any]]) -> _LLMResult:
    """POST a {LLM_BASE_URL}/chat/completions con retry su errori transitori."""
    payload = {
        "model": config.get("LLM_MODEL"),
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": config.get_float("LLM_TEMPERATURE", 0.1),
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = config.get("LLM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(
        base_url=config.get("LLM_BASE_URL"),
        timeout=config.get_float("LLM_TIMEOUT", 60.0),
    ) as client:
        resp = await client.post(
            "/chat/completions",
            json=payload,
            headers=headers,
        )

    if not resp.is_success:
        # Extract server-side error message for actionable logging
        try:
            err_body = resp.json()
            err_msg = err_body.get("error", {}).get("message") or resp.text
        except (ValueError, AttributeError):
            err_msg = resp.text[:300]
        logger.error("LLM %s %s: %s", resp.status_code, config.get("LLM_BASE_URL"), err_msg)
        resp.raise_for_status()

    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    usage = data.get("usage", {})
    return _LLMResult(
        content=content,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
    )


async def _extract_articles_with_llm(
    markdown_text: str, base_url: str, max_articles: int
) -> tuple[list[dict[str, Any]], int, int]:
    """Estrae articoli dal Markdown via LLM. Restituisce (articoli, prompt_tokens, completion_tokens)."""
    prompt = f"""
    Analizza il seguente contenuto Markdown estratto da una pagina web ({base_url}).
    Il tuo compito è trovare i link che puntano agli articoli di notizie (news).
    Estrai ESATTAMENTE i primi {max_articles} articoli più recenti o rilevanti che trovi.

    Per ogni articolo devi restituire:
    - title: Il titolo della notizia.
    - url: L'URL completo dell'articolo. Assicurati che sia assoluto (es. se trovi
      '/en-us/article/123', e la base è '{base_url}', l'URL completo potrebbe essere
      'https://news.blizzard.com/en-us/article/123').
    - published_date: La data di pubblicazione, se presente nel testo vicino al link.
    - thumbnail_url: L'URL di una eventuale immagine associata, se presente nel markdown.

    Rispondi SOLO con i dati richiesti.

    Contenuto Markdown:
    {markdown_text[:config.get_int("LLM_MAX_PROMPT_CHARS", 8000)]}
    """

    messages = [
        {
            "role": "system",
            "content": "Sei un assistente specializzato nell'estrazione di dati strutturati da pagine web Markdown. "  # noqa: E501
            "Rispondi SEMPRE e SOLO con un oggetto JSON valido.",
        },
        {
            "role": "user",
            "content": prompt + "\n\nRispondi con un JSON che abbia questa struttura: "
            '{"articles": [{"title": "...", "url": "...", "published_date": "...", "thumbnail_url": "..."}]}',
        },
    ]

    try:
        logger.info(
            "Invocazione LLM (%s) per estrazione di %d articoli...",
            config.get("LLM_MODEL"),
            max_articles,
        )

        result = await _call_llm_api(messages)
        if not result.content:
            return [], result.prompt_tokens, result.completion_tokens

        _save_debug_file("llm_output.json", result.content)

        raw = result.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```\s*$", "", raw.strip())
        parsed = ArticlesList.model_validate(json.loads(raw))
        articles = [a.model_dump() for a in parsed.articles[:max_articles]]
        return articles, result.prompt_tokens, result.completion_tokens

    except Exception as e:
        logger.error("Errore durante l'estrazione LLM: %s", e)
        return [], 0, 0


async def scrape_latest_news(url: str, max_articles: int = 1) -> ScrapeResult:
    """
    1. Scarica l'HTML con Playwright.
    2. Lo converte in Markdown.
    3. Usa l'LLM per trovare i link agli articoli.
    4. Scrapa i singoli articoli.
    """
    async with _new_browser_page() as page:
        logger.info("Apertura homepage: %s", url)
        await page.goto(url, wait_until="networkidle", timeout=30000)
        # Revalidate post-navigation: page.goto() follows 3xx redirects automatically,
        # and the initial URL check (done by the caller / pydantic validator) doesn't
        # see where a redirect actually lands (SSRF guard, see url_safety.py).
        await asyncio.to_thread(validate_url, page.url)

        html_content = await page.content()
        _save_debug_file("playwright_output.html", html_content)

        preprocessed_html = _preprocess_html(html_content, url)
        _save_debug_file("playwright_preprocessed.html", preprocessed_html)

        logger.info("Conversione HTML -> Markdown")
        markdown_text = _html_to_markdown(preprocessed_html)
        _save_debug_file("markitdown_output.md", markdown_text)

        (
            articles_meta,
            prompt_tokens,
            completion_tokens,
        ) = await _extract_articles_with_llm(_sanitize_markdown(markdown_text), url, max_articles)
        logger.info("L'LLM ha estratto %d link ad articoli.", len(articles_meta))

        results = []
        for meta in articles_meta:
            try:
                article_data = await _scrape_article_page(page, meta["url"])

                article_data["title"] = meta.get("title") or article_data["title"]
                if not article_data.get("published_date") and meta.get("published_date"):
                    article_data["published_date"] = meta["published_date"]
                if not article_data.get("thumbnail_url") and meta.get("thumbnail_url"):
                    article_data["thumbnail_url"] = meta["thumbnail_url"]

                results.append(article_data)
            except Exception as e:
                logger.warning(
                    "Errore nello scraping del singolo articolo %s: %s",
                    meta["url"],
                    e,
                )

        return ScrapeResult(
            articles=results,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


async def scrape_article(url: str) -> ScrapeResult:
    """Entry point per scrapare un singolo articolo (endpoint /scrape/article)."""
    async with _new_browser_page() as page:
        article = await _scrape_article_page(page, url)
        return ScrapeResult(articles=[article])


async def _scrape_article_page(page: Page, url: str) -> dict[str, Any]:
    """Apre un articolo ed estrae il contenuto pulito via MarkItDown."""
    # Article URLs reaching here can come straight from LLM extraction
    # (scrape_latest_news), which never passed through the pydantic request
    # validator -- validate here too, not just at the API boundary.
    await asyncio.to_thread(validate_url, url)
    logger.info("Scraping articolo: %s", url)
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Revalidate post-navigation: see the matching comment in scrape_latest_news.
    await asyncio.to_thread(validate_url, page.url)

    html_content = await page.content()
    markdown_text = _html_to_markdown(html_content)
    content = re.sub(r"\n{3,}", "\n\n", markdown_text)

    title = await page.title()
    thumbnail_url = await page.evaluate("""() => {
        const og = document.querySelector("meta[property='og:image']");
        return og ? og.getAttribute('content') : null;
    }""")

    max_chars = config.get_int("LLM_MAX_PROMPT_CHARS", 8000)
    if len(content) > max_chars:
        logger.warning(
            "Contenuto articolo troncato a %d chars (originale: %d)",
            max_chars,
            len(content),
        )
    return {
        "title": title.split("—")[0].strip() if "—" in title else title.strip(),
        "url": url,
        "published_date": None,
        "content": content[:max_chars],
        "thumbnail_url": thumbnail_url,
    }
