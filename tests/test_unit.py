"""Level 1 — pure unit tests: no HTTP, no mocked I/O."""

import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from unittest.mock import patch

from app.scraper import _preprocess_html, ArticlesList
from app.main import verify_token


# ---------------------------------------------------------------------------
# _preprocess_html
# ---------------------------------------------------------------------------


class TestPreprocessHtml:
    def test_relative_href_becomes_absolute(self):
        html = '<a href="/news/123">Article</a>'
        result = _preprocess_html(html, "https://example.com/page")
        assert 'href="https://example.com/news/123"' in result

    def test_absolute_href_unchanged(self):
        html = '<a href="https://other.com/news">Article</a>'
        result = _preprocess_html(html, "https://example.com")
        assert 'href="https://other.com/news"' in result

    def test_custom_tag_with_relative_href_becomes_anchor(self):
        html = '<blizzard-card href="/en-us/article/1">Title</blizzard-card>'
        result = _preprocess_html(html, "https://news.blizzard.com")
        assert '<a href="https://news.blizzard.com/en-us/article/1">' in result

    def test_custom_tag_preserves_text_content(self):
        html = '<custom-card href="/article/1">My Title</custom-card>'
        result = _preprocess_html(html, "https://example.com")
        assert "My Title" in result

    def test_multiple_relative_hrefs(self):
        html = '<a href="/a">A</a><a href="/b">B</a>'
        result = _preprocess_html(html, "https://example.com")
        assert 'href="https://example.com/a"' in result
        assert 'href="https://example.com/b"' in result

    def test_no_href_tags_pass_through(self):
        html = "<p>No links here</p>"
        result = _preprocess_html(html, "https://example.com")
        assert "No links here" in result
        assert "href" not in result

    def test_empty_html_returns_string(self):
        result = _preprocess_html("", "https://example.com")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


class TestVerifyToken:
    def test_no_token_configured_no_credentials(self):
        with patch("app.main.API_AUTH_TOKEN", None):
            result = verify_token(None)
        assert result is None

    def test_no_token_configured_any_credentials_allowed(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="anything")
        with patch("app.main.API_AUTH_TOKEN", None):
            result = verify_token(creds)
        assert result == "anything"

    def test_token_configured_valid_credentials(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret123")
        with patch("app.main.API_AUTH_TOKEN", "secret123"):
            result = verify_token(creds)
        assert result == "secret123"

    def test_token_configured_wrong_credentials_raises_401(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
        with patch("app.main.API_AUTH_TOKEN", "secret123"):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(creds)
        assert exc_info.value.status_code == 401

    def test_token_configured_no_credentials_raises_401(self):
        with patch("app.main.API_AUTH_TOKEN", "secret123"):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(None)
        assert exc_info.value.status_code == 401

    def test_401_includes_www_authenticate_header(self):
        with patch("app.main.API_AUTH_TOKEN", "secret"):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(None)
        assert "WWW-Authenticate" in exc_info.value.headers


# ---------------------------------------------------------------------------
# ArticlesList / ArticleExtraction Pydantic models
# ---------------------------------------------------------------------------


class TestArticlesListModel:
    def test_valid_full_article(self):
        data = {
            "articles": [
                {
                    "title": "Test News",
                    "url": "https://example.com/news/1",
                    "published_date": "2026-01-01",
                    "thumbnail_url": "https://example.com/thumb.jpg",
                }
            ]
        }
        result = ArticlesList.model_validate(data)
        assert len(result.articles) == 1
        assert result.articles[0].title == "Test News"
        assert result.articles[0].url == "https://example.com/news/1"

    def test_valid_minimal_article_optional_fields_default_none(self):
        data = {"articles": [{"title": "T", "url": "https://example.com"}]}
        result = ArticlesList.model_validate(data)
        assert result.articles[0].published_date is None
        assert result.articles[0].thumbnail_url is None

    def test_empty_articles_list(self):
        result = ArticlesList.model_validate({"articles": []})
        assert result.articles == []

    def test_multiple_articles(self):
        data = {
            "articles": [
                {"title": "A", "url": "https://example.com/a"},
                {"title": "B", "url": "https://example.com/b"},
            ]
        }
        result = ArticlesList.model_validate(data)
        assert len(result.articles) == 2

    def test_missing_title_raises_validation_error(self):
        data = {"articles": [{"url": "https://example.com"}]}
        with pytest.raises(ValidationError):
            ArticlesList.model_validate(data)

    def test_missing_url_raises_validation_error(self):
        data = {"articles": [{"title": "No URL"}]}
        with pytest.raises(ValidationError):
            ArticlesList.model_validate(data)

    def test_missing_articles_key_raises_validation_error(self):
        with pytest.raises(ValidationError):
            ArticlesList.model_validate({"data": []})

    def test_model_dump_roundtrip_matches_input(self):
        data = {
            "articles": [
                {
                    "title": "T",
                    "url": "https://example.com",
                    "published_date": "2026-01-01",
                    "thumbnail_url": None,
                }
            ]
        }
        result = ArticlesList.model_validate(data)
        dumped = result.articles[0].model_dump()
        assert dumped["title"] == "T"
        assert dumped["url"] == "https://example.com"
        assert dumped["published_date"] == "2026-01-01"
        assert dumped["thumbnail_url"] is None
