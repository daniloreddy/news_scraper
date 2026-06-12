"""Level 2 — endpoint tests via FastAPI TestClient with mocked scraper functions."""

import asyncio
from unittest.mock import patch, AsyncMock


SAMPLE_ARTICLE = {
    "title": "Test Article",
    "url": "https://example.com/article/1",
    "published_date": "2026-01-01",
    "content": "Article content here.",
    "thumbnail_url": "https://example.com/thumb.jpg",
}


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /scrape
# ---------------------------------------------------------------------------


class TestScrapeEndpoint:
    def test_success_returns_article_list(self, client):
        with patch(
            "app.main.scrape_latest_news", new=AsyncMock(return_value=[SAMPLE_ARTICLE])
        ):
            response = client.post(
                "/scrape", json={"url": "https://example.com", "max_articles": 1}
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Article"
        assert data[0]["url"] == "https://example.com/article/1"

    def test_default_values_used_when_body_empty(self, client):
        with patch(
            "app.main.scrape_latest_news", new=AsyncMock(return_value=[SAMPLE_ARTICLE])
        ) as mock:
            response = client.post("/scrape", json={})
        assert response.status_code == 200
        mock.assert_called_once_with(
            "https://diabloimmortal.blizzard.com/en-us#news", 1
        )

    def test_no_articles_found_returns_404(self, client):
        with patch("app.main.scrape_latest_news", new=AsyncMock(return_value=[])):
            response = client.post("/scrape", json={"url": "https://example.com"})
        assert response.status_code == 404

    def test_scraper_exception_returns_500(self, client):
        with patch(
            "app.main.scrape_latest_news",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            response = client.post("/scrape", json={"url": "https://example.com"})
        assert response.status_code == 500

    def test_timeout_returns_504(self, client):
        with patch(
            "app.main.scrape_latest_news",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            response = client.post("/scrape", json={"url": "https://example.com"})
        assert response.status_code == 504

    def test_invalid_max_articles_type_returns_422(self, client):
        response = client.post("/scrape", json={"max_articles": "not_a_number"})
        assert response.status_code == 422

    def test_multiple_articles_returned(self, client):
        second = {
            **SAMPLE_ARTICLE,
            "title": "Second Article",
            "url": "https://example.com/2",
        }
        with patch(
            "app.main.scrape_latest_news",
            new=AsyncMock(return_value=[SAMPLE_ARTICLE, second]),
        ):
            response = client.post(
                "/scrape", json={"url": "https://example.com", "max_articles": 2}
            )
        assert response.status_code == 200
        assert len(response.json()) == 2


# ---------------------------------------------------------------------------
# POST /scrape/article
# ---------------------------------------------------------------------------


class TestScrapeArticleEndpoint:
    def test_success(self, client):
        with patch(
            "app.main.scrape_article", new=AsyncMock(return_value=SAMPLE_ARTICLE)
        ):
            response = client.post(
                "/scrape/article", json={"url": "https://example.com/article/1"}
            )
        assert response.status_code == 200
        assert response.json()["title"] == "Test Article"

    def test_scraper_exception_returns_500(self, client):
        with patch(
            "app.main.scrape_article", new=AsyncMock(side_effect=RuntimeError("fail"))
        ):
            response = client.post(
                "/scrape/article", json={"url": "https://example.com/article/1"}
            )
        assert response.status_code == 500

    def test_timeout_returns_504(self, client):
        with patch(
            "app.main.scrape_article", new=AsyncMock(side_effect=asyncio.TimeoutError())
        ):
            response = client.post(
                "/scrape/article", json={"url": "https://example.com/article/1"}
            )
        assert response.status_code == 504

    def test_missing_url_in_body_returns_422(self, client):
        response = client.post("/scrape/article", json={})
        assert response.status_code == 422

    def test_url_as_query_param_without_body_returns_422(self, client):
        # Old interface (pre-fix #4): query param is no longer accepted
        response = client.post("/scrape/article?url=https://example.com")
        assert response.status_code == 422

    def test_response_shape_matches_article_result_model(self, client):
        with patch(
            "app.main.scrape_article", new=AsyncMock(return_value=SAMPLE_ARTICLE)
        ):
            response = client.post(
                "/scrape/article", json={"url": "https://example.com"}
            )
        data = response.json()
        assert set(data.keys()) == {
            "title",
            "url",
            "published_date",
            "content",
            "thumbnail_url",
        }


# ---------------------------------------------------------------------------
# URL validation (SSRF) and input constraints
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_scrape_file_url_returns_422(self, client):
        response = client.post("/scrape", json={"url": "file:///etc/passwd"})
        assert response.status_code == 422

    def test_scrape_private_ip_returns_422(self, client):
        response = client.post("/scrape", json={"url": "http://192.168.1.1/"})
        assert response.status_code == 422

    def test_scrape_localhost_returns_422(self, client):
        response = client.post("/scrape", json={"url": "http://localhost/admin"})
        assert response.status_code == 422

    def test_scrape_article_file_url_returns_422(self, client):
        response = client.post("/scrape/article", json={"url": "file:///etc/passwd"})
        assert response.status_code == 422

    def test_scrape_article_private_ip_returns_422(self, client):
        response = client.post("/scrape/article", json={"url": "http://10.0.0.1/"})
        assert response.status_code == 422

    def test_max_articles_above_limit_returns_422(self, client):
        response = client.post(
            "/scrape", json={"url": "https://example.com", "max_articles": 11}
        )
        assert response.status_code == 422

    def test_max_articles_zero_returns_422(self, client):
        response = client.post(
            "/scrape", json={"url": "https://example.com", "max_articles": 0}
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_no_token_configured_request_without_header_allowed(self, client):
        with patch("app.main.API_AUTH_TOKEN", None):
            with patch(
                "app.main.scrape_latest_news",
                new=AsyncMock(return_value=[SAMPLE_ARTICLE]),
            ):
                response = client.post("/scrape", json={"url": "https://example.com"})
        assert response.status_code == 200

    def test_token_configured_request_without_header_returns_401(self, client):
        with patch("app.main.API_AUTH_TOKEN", "mysecret"):
            response = client.post("/scrape", json={"url": "https://example.com"})
        assert response.status_code == 401

    def test_token_configured_wrong_token_returns_401(self, client):
        with patch("app.main.API_AUTH_TOKEN", "mysecret"):
            response = client.post(
                "/scrape",
                json={"url": "https://example.com"},
                headers={"Authorization": "Bearer wrong"},
            )
        assert response.status_code == 401

    def test_token_configured_correct_token_allowed(self, client):
        with patch("app.main.API_AUTH_TOKEN", "mysecret"):
            with patch(
                "app.main.scrape_latest_news",
                new=AsyncMock(return_value=[SAMPLE_ARTICLE]),
            ):
                response = client.post(
                    "/scrape",
                    json={"url": "https://example.com"},
                    headers={"Authorization": "Bearer mysecret"},
                )
        assert response.status_code == 200

    def test_auth_enforced_on_article_endpoint_too(self, client):
        with patch("app.main.API_AUTH_TOKEN", "mysecret"):
            response = client.post(
                "/scrape/article", json={"url": "https://example.com"}
            )
        assert response.status_code == 401

    def test_auth_not_enforced_on_health(self, client):
        # /health is public — no auth dependency
        response = client.get("/health")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

# Use RFC 5737 documentation IPs as unique per-test keys so in-memory limiter
# state (module-level) does not bleed across tests within the same session.


class TestRateLimiting:
    _SCRAPE_IP = "203.0.113.1"
    _HEALTH_IP = "203.0.113.2"

    def test_scrape_returns_429_after_limit_exceeded(self, client):
        headers = {"X-Real-IP": self._SCRAPE_IP}
        with patch(
            "app.main.scrape_latest_news", new=AsyncMock(return_value=[SAMPLE_ARTICLE])
        ):
            for _ in range(20):
                client.post(
                    "/scrape", json={"url": "https://example.com"}, headers=headers
                )
            response = client.post(
                "/scrape", json={"url": "https://example.com"}, headers=headers
            )
        assert response.status_code == 429

    def test_health_returns_429_after_limit_exceeded(self, client):
        headers = {"X-Real-IP": self._HEALTH_IP}
        for _ in range(100):
            client.get("/health", headers=headers)
        response = client.get("/health", headers=headers)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# Published date field behaviour
# ---------------------------------------------------------------------------


class TestPublishedDate:
    def test_scrape_article_published_date_can_be_none(self, client):
        with patch(
            "app.main.scrape_article",
            new=AsyncMock(
                return_value={
                    **SAMPLE_ARTICLE,
                    "published_date": None,
                }
            ),
        ):
            response = client.post(
                "/scrape/article", json={"url": "https://example.com"}
            )
        assert response.status_code == 200
        assert response.json()["published_date"] is None

    def test_scrape_published_date_propagated_when_present(self, client):
        with patch(
            "app.main.scrape_latest_news",
            new=AsyncMock(
                return_value=[
                    {
                        **SAMPLE_ARTICLE,
                        "published_date": "2026-06-12",
                    }
                ]
            ),
        ):
            response = client.post("/scrape", json={"url": "https://example.com"})
        assert response.status_code == 200
        assert response.json()[0]["published_date"] == "2026-06-12"
