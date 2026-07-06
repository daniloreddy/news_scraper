# news-scraper

Python/FastAPI microservice that scrapes news from any website and exposes a structured REST API. Includes a NiceGUI monitoring dashboard at `/ui/` with config hot-reload.

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
                    [LLM (OpenAI-compatible endpoint)]
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
- **LLM (httpx direct POST)**: Submits the Markdown to any OpenAI-compatible endpoint (Ollama, LM Studio, OpenAI, etc.) via direct HTTP POST — no SDK dependency. Validates and parses the structured JSON response to extract the article list.

## Quick Start with Docker

> Requirement: **Docker** with the Compose plugin installed. No need to clone the project.
>
> Create an empty folder. You only need to add two files — everything else (`data/`, `debug/`) is created automatically.

**1. Download the Compose file**

```bash
curl -O https://raw.githubusercontent.com/daniloreddy/news_scraper/main/docker-compose.yml
```

**2. Create a `.env` file** in the same folder:

```env
# Host port the service listens on (change if 8088 is already taken)
APP_PORT=8088
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-api-key-here
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0.1
LLM_TIMEOUT=60.0
LLM_MAX_PROMPT_CHARS=8000
API_AUTH_TOKEN=your-super-secret-token-here
SCRAPE_TIMEOUT=300
RATE_LIMIT=20/minute
# Trusted reverse-proxy IPs allowed to set CF-Connecting-IP / X-Real-IP /
# X-Forwarded-For. Default 127.0.0.1 is correct for Cloudflare Tunnel pointing
# at uvicorn on the same host. Leave empty if exposed directly to the internet.
TRUSTED_PROXIES=127.0.0.1
DEBUG=false
```

**3. Start the container**

```bash
docker compose up -d
```

**4. Set the dashboard password** (first run only)

```bash
docker exec -it news-scraper python scripts/set_password.py
```

The service will be available at `http://localhost:8088` (or whatever `APP_PORT` you set).
The monitoring dashboard is at `http://localhost:8088/ui/`.

**Update image**

```bash
docker compose pull && docker compose up -d
```

**Stop**

```bash
docker compose down
```

---

## Local Development

> Only needed to modify the source code.

**Requirements**: Docker, Python 3.12+

```bash
git clone https://github.com/daniloreddy/news_scraper.git news-scraper
cd news-scraper
cp .env.example .env   # then edit .env with your values
docker compose -f docker-compose-dev.yml up --build
```

---

## Cloudflare Tunnel

Add the following to your tunnel's `config.yml`:

```yaml
ingress:
  - hostname: your.end.point   # Replace with your actual domain
    service: http://localhost:8088
  # ... other existing rules
```

Alternatively, configure it via the Cloudflare Tunnel dashboard by adding a public hostname.

## Quick Test

```bash
# Health check
curl https://your.end.point/health

# Scrape the latest news (takes a few seconds for Playwright and LLM processing)
curl -X POST https://your.end.point/scrape \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-super-secret-token-here" \
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
