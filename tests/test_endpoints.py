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
