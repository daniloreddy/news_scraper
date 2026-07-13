from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import metrics
from app.config import config

# Must be set before app.scraper is imported; scraper.py reads LLM_API_KEY via
# ConfigManager at call time, but .env may be missing this key in CI.
# ConfigManager reads only from .env (no OS env override), so patch the cache
# directly rather than os.environ.
config._cache.setdefault("LLM_API_KEY", "test-dummy")


@pytest.fixture
def client(tmp_path):
    from app.main import app

    # Redirect metrics writes to a throwaway DB for this test only — the app's
    # lifespan calls metrics.init_db() on startup and every /scrape* request
    # records a row; without this, endpoint tests pollute the real
    # data/metrics.db shown on the production dashboard.
    test_db_path = tmp_path / "metrics.db"

    # Prevent lifespan from actually running `playwright install`.
    # Patch API_AUTH_TOKEN to "" so local .env values don't bleed into
    # non-auth tests; auth-specific tests patch it themselves per test.
    with patch("subprocess.run"):
        with patch.dict(config._cache, {"API_AUTH_TOKEN": ""}):
            with patch.object(metrics, "_DB_PATH", test_db_path):
                # client=("127.0.0.1", ...) simulates the documented default
                # deployment (reverse proxy / Cloudflare Tunnel on localhost)
                # so forwarded-IP headers set by tests are trusted, per
                # TRUSTED_PROXIES default.
                with TestClient(app, client=("127.0.0.1", 123)) as c:
                    yield c
