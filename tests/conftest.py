import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

# Must be set before app.scraper is imported; scraper.py reads LLM_API_KEY via
# ConfigManager at call time, but .env may be missing this key in CI.
os.environ.setdefault("LLM_API_KEY", "test-dummy")


@pytest.fixture
def client():
    from app.config import config
    from app.main import app

    # Prevent lifespan from actually running `playwright install`.
    # Patch API_AUTH_TOKEN to "" so local .env values don't bleed into
    # non-auth tests; auth-specific tests patch it themselves per test.
    with patch("subprocess.run"):
        with patch.dict(config._cache, {"API_AUTH_TOKEN": ""}):
            # client=("127.0.0.1", ...) simulates the documented default deployment
            # (reverse proxy / Cloudflare Tunnel on localhost) so forwarded-IP
            # headers set by tests are trusted, per TRUSTED_PROXIES default.
            with TestClient(app, client=("127.0.0.1", 123)) as c:
                yield c
