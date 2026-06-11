import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

# Must be set before app.scraper is imported; AsyncOpenAI raises on empty api_key
os.environ.setdefault("LLM_API_KEY", "test-dummy")


@pytest.fixture
def client():
    from app.main import app

    # Prevent lifespan from actually running `playwright install`
    with patch("subprocess.run"):
        with TestClient(app) as c:
            yield c
