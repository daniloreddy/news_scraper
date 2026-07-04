import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

# Must be set before app.scraper is imported; AsyncOpenAI raises on empty api_key
os.environ.setdefault("LLM_API_KEY", "test-dummy")


@pytest.fixture
def client():
    from app.main import app

    # Prevent lifespan from actually running `playwright install`.
    # Patch API_AUTH_TOKEN to None so local .env values don't bleed into
    # non-auth tests; auth-specific tests patch it themselves per test.
    with patch("subprocess.run"):
        with patch("app.main.API_AUTH_TOKEN", None):
            # client=("127.0.0.1", ...) simulates the documented default deployment
            # (reverse proxy / Cloudflare Tunnel on localhost) so forwarded-IP
            # headers set by tests are trusted, per TRUSTED_PROXIES default.
            with TestClient(app, client=("127.0.0.1", 123)) as c:
                yield c
