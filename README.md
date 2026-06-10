# news-scraper

Python/FastAPI microservice that scrapes news from any website and exposes a structured REST API.

## Architecture

The microservice utilizes a **Smart/LLM-based Scraping** approach that is completely agnostic to the source website's structure. The internal execution flow for the `/scrape` endpoint is structured as follows:

```
[Client (HTTP POST)] ──► [FastAPI Entrypoint (Synchronous Thread Pool)]
                              │
                              ▼
                [Playwright (Chromium Headless)]
                              │ (Rendering JavaScript)
                              ▼
                      [Dynamic HTML]
                              │
                              ▼
               [BeautifulSoup Pre-processing]
                              │ (Normalizing Web Components & URLs)
                              ▼
                 [MarkItDown (Microsoft)]
                              │ (Converting HTML -> Markdown)
                              ▼
                    [OpenAI API / Local LLM]
                              │ (Structured Extraction of Links)
                              ▼
               [Scraping Individual Articles]
                              │ (Markdown and Full Metadata)
                              ▼
                     [JSON Response]
```

### Key Components:
- **FastAPI**: Manages the HTTP routes and offloads execution to a dedicated thread pool (on Windows) to prevent conflicts with Uvicorn's event loop policy.
- **Playwright (Headless Chromium)**: Fully renders dynamic page content executed via JavaScript before parsing.
- **BeautifulSoup (Pre-processing)**: Automatically identifies and normalizes nested links or non-standard tags (such as `<blz-button>` containing `href`) into standard `<a>` tags and resolves relative paths into absolute URLs.
- **MarkItDown**: Cleans the resulting HTML by stripping script, CSS, navigation, and ads, converting it into a clean, structured, and token-efficient Markdown document.
- **OpenAI Client**: Submits the Markdown to the configured LLM and validates the structured output (JSON) to extract the article list.

## Deployment

### 1. Prerequisites

```bash
# Install Docker if not already present
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log in again to apply group membership
```

### 2. Build and Start

```bash
git clone <repo> news-scraper
cd news-scraper
docker compose up -d --build
```

The service will be available at `http://localhost:8088`.

### 3. Cloudflare Tunnel

Add the following to your tunnel's `config.yml`:

```yaml
ingress:
  - hostname: your.end.point   # Replace with your actual domain
    service: http://localhost:8088
  # ... other existing rules
```

Alternatively, configure it via the Cloudflare Tunnel dashboard by adding a public hostname.

### 4. Quick Test

```bash
# Health check
curl https://your.end.point/health

# Scrape the latest news (takes a few seconds for Playwright and LLM processing)
curl -X POST https://your.end.point/scrape \
  -H "Content-Type: application/json" \
  -d '{"max_articles": 1}'
```

---

## Extending to Other Sites

Because the scraper utilizes an LLM-driven, structure-agnostic approach, there is **no need to write CSS or Regex selectors** to support new websites. 

Simply invoke the `/scrape` endpoint and pass any target site's URL in the JSON body:

```json
{
  "url": "https://news.blizzard.com/diablo-immortal",
  "max_articles": 1
}
```

The system will automatically download, convert, and parse the target news page correctly using its LLM intelligence.

For integration instructions with n8n, WordPress, and Telegram, refer to [INTEGRATIONS.md](INTEGRATIONS.md).
