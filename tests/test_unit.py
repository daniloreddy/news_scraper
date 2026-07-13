"""Level 1 — pure unit tests: no HTTP, no mocked I/O."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.config import config
from app.main import ArticleRequest, ScrapeRequest, _validate_url, verify_token
from app.scraper import ArticlesList, _preprocess_html

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
        with patch.dict(config._cache, {"API_AUTH_TOKEN": ""}):
            result = verify_token(None)
        assert result is None

    def test_no_token_configured_any_credentials_allowed(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="anything")
        with patch.dict(config._cache, {"API_AUTH_TOKEN": ""}):
            result = verify_token(creds)
        assert result == "anything"

    def test_token_configured_valid_credentials(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret123")
        with patch.dict(config._cache, {"API_AUTH_TOKEN": "secret123"}):
            result = verify_token(creds)
        assert result == "secret123"

    def test_token_configured_wrong_credentials_raises_401(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
        with patch.dict(config._cache, {"API_AUTH_TOKEN": "secret123"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(creds)
        assert exc_info.value.status_code == 401

    def test_token_configured_no_credentials_raises_401(self):
        with patch.dict(config._cache, {"API_AUTH_TOKEN": "secret123"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_token(None)
        assert exc_info.value.status_code == 401

    def test_401_includes_www_authenticate_header(self):
        with patch.dict(config._cache, {"API_AUTH_TOKEN": "secret"}):
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


# ---------------------------------------------------------------------------
# _validate_url (SSRF guard)
# ---------------------------------------------------------------------------


class TestValidateUrl:
    def test_https_url_allowed(self):
        assert _validate_url("https://example.com/news") == "https://example.com/news"

    def test_http_url_allowed(self):
        assert _validate_url("http://example.com/news") == "http://example.com/news"

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="http or https"):
            _validate_url("file:///etc/passwd")

    def test_data_scheme_rejected(self):
        with pytest.raises(ValueError, match="http or https"):
            _validate_url("data:text/html,<script>alert(1)</script>")

    def test_localhost_rejected(self):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("http://localhost/admin")

    def test_loopback_ip_rejected(self):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("http://127.0.0.1/secret")

    def test_private_class_c_rejected(self):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("http://192.168.1.100/")

    def test_private_class_a_rejected(self):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("http://10.0.0.1/")

    def test_empty_host_rejected(self):
        with pytest.raises(ValueError):
            _validate_url("https:///path")


# ---------------------------------------------------------------------------
# ScrapeRequest / ArticleRequest model validation
# ---------------------------------------------------------------------------


class TestScrapeRequestModel:
    def test_default_url_and_max_articles(self):
        req = ScrapeRequest()
        assert req.max_articles == 1
        assert "diabloimmortal" in req.url

    def test_max_articles_10_allowed(self):
        req = ScrapeRequest(url="https://example.com", max_articles=10)
        assert req.max_articles == 10

    def test_max_articles_0_rejected(self):
        with pytest.raises(ValidationError):
            ScrapeRequest(url="https://example.com", max_articles=0)

    def test_max_articles_11_rejected(self):
        with pytest.raises(ValidationError):
            ScrapeRequest(url="https://example.com", max_articles=11)

    def test_ssrf_file_url_rejected(self):
        with pytest.raises(ValidationError):
            ScrapeRequest(url="file:///etc/passwd")

    def test_ssrf_private_ip_rejected(self):
        with pytest.raises(ValidationError):
            ScrapeRequest(url="http://192.168.1.100/")

    def test_ssrf_localhost_rejected(self):
        with pytest.raises(ValidationError):
            ScrapeRequest(url="http://localhost/admin")


class TestArticleRequestModel:
    def test_valid_url_accepted(self):
        req = ArticleRequest(url="https://example.com/article/1")
        assert req.url == "https://example.com/article/1"

    def test_ssrf_file_url_rejected(self):
        with pytest.raises(ValidationError):
            ArticleRequest(url="file:///etc/passwd")

    def test_ssrf_private_ip_rejected(self):
        with pytest.raises(ValidationError):
            ArticleRequest(url="http://10.0.0.1/")


# ---------------------------------------------------------------------------
# _sanitize_markdown
# ---------------------------------------------------------------------------


class TestSanitizeMarkdown:
    def test_strips_script_tag(self):
        from app.scraper import _sanitize_markdown

        result = _sanitize_markdown("Before <script>alert('xss')</script> After")
        assert "<script>" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_multiline_script(self):
        from app.scraper import _sanitize_markdown

        result = _sanitize_markdown("A\n<script>\nevil();\n</script>\nB")
        assert "evil" not in result
        assert "A" in result
        assert "B" in result

    def test_strips_iframe(self):
        from app.scraper import _sanitize_markdown

        result = _sanitize_markdown("Text <iframe src='evil.com'></iframe> End")
        assert "<iframe" not in result
        assert "Text" in result

    def test_replaces_data_uri(self):
        from app.scraper import _sanitize_markdown

        result = _sanitize_markdown("img: data:image/png;base64,abc123==")
        assert "data:image/png" not in result
        assert "[DATA_URI_REMOVED]" in result

    def test_strips_javascript_protocol(self):
        from app.scraper import _sanitize_markdown

        result = _sanitize_markdown("[Click](javascript:alert(1))")
        assert "javascript:" not in result

    def test_clean_markdown_unchanged(self):
        from app.scraper import _sanitize_markdown

        text = "# Heading\n\nSome [link](https://example.com) text."
        assert _sanitize_markdown(text) == text


# ---------------------------------------------------------------------------
# _save_debug_file (debug mode)
# ---------------------------------------------------------------------------


class TestDebugMode:
    def test_debug_true_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from app.scraper import _save_debug_file, config

        with patch.object(config, "get_bool", return_value=True):
            _save_debug_file("output.md", "Hello debug")
        debug_file = tmp_path / "debug" / "output.md"
        assert debug_file.exists()
        assert debug_file.read_text(encoding="utf-8") == "Hello debug"

    def test_debug_false_no_file_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from app.scraper import _save_debug_file, config

        with patch.object(config, "get_bool", return_value=False):
            _save_debug_file("output.md", "Should not appear")
        assert not (tmp_path / "debug").exists()


# ---------------------------------------------------------------------------
# _scrape_article_page — content truncation and title parsing
# ---------------------------------------------------------------------------


class TestScrapeArticlePage:
    def _run(self, page, url="https://example.com"):
        from app.scraper import _scrape_article_page

        return asyncio.run(_scrape_article_page(page, url))

    def _make_page(self, content: str, title: str = "Title", thumbnail=None):
        page = AsyncMock()
        page.content.return_value = "<html><body>x</body></html>"
        page.title.return_value = title
        page.evaluate.return_value = thumbnail
        mock_result = MagicMock()
        mock_result.text_content = content
        return page, mock_result

    def test_content_truncated_at_8000_chars(self):
        from app.scraper import md_converter

        page, mock_result = self._make_page("A" * 15000)
        with patch.object(md_converter, "convert_stream", return_value=mock_result):
            result = self._run(page)
        assert len(result["content"]) == 8000

    def test_content_truncation_respects_llm_max_prompt_chars_config(self):
        from app.scraper import config, md_converter

        page, mock_result = self._make_page("B" * 15000)
        with patch.dict(config._cache, {"LLM_MAX_PROMPT_CHARS": "10000"}):
            with patch.object(md_converter, "convert_stream", return_value=mock_result):
                result = self._run(page)
        assert len(result["content"]) == 10000

    def test_short_content_not_truncated(self):
        from app.scraper import md_converter

        page, mock_result = self._make_page("Short content")
        with patch.object(md_converter, "convert_stream", return_value=mock_result):
            result = self._run(page)
        assert result["content"] == "Short content"

    def test_triple_newlines_collapsed(self):
        from app.scraper import md_converter

        page, mock_result = self._make_page("Para1\n\n\n\n\nPara2")
        with patch.object(md_converter, "convert_stream", return_value=mock_result):
            result = self._run(page)
        assert "\n\n\n" not in result["content"]
        assert "Para1" in result["content"]
        assert "Para2" in result["content"]

    def test_title_stripped_at_em_dash(self):
        from app.scraper import md_converter

        page, mock_result = self._make_page("Content", title="Article Title — Site Name")
        with patch.object(md_converter, "convert_stream", return_value=mock_result):
            result = self._run(page)
        assert result["title"] == "Article Title"

    def test_title_without_em_dash_unchanged(self):
        from app.scraper import md_converter

        page, mock_result = self._make_page("Content", title="Plain Title")
        with patch.object(md_converter, "convert_stream", return_value=mock_result):
            result = self._run(page)
        assert result["title"] == "Plain Title"

    def test_published_date_always_none(self):
        from app.scraper import md_converter

        page, mock_result = self._make_page("Content")
        with patch.object(md_converter, "convert_stream", return_value=mock_result):
            result = self._run(page)
        assert result["published_date"] is None

    def test_thumbnail_url_from_page_evaluate(self):
        from app.scraper import md_converter

        page, mock_result = self._make_page("Content", thumbnail="https://example.com/thumb.jpg")
        with patch.object(md_converter, "convert_stream", return_value=mock_result):
            result = self._run(page)
        assert result["thumbnail_url"] == "https://example.com/thumb.jpg"


# ---------------------------------------------------------------------------
# _call_llm_api / _extract_articles_with_llm — retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    @staticmethod
    def _llm_response(content: str) -> MagicMock:
        resp = MagicMock()
        resp.is_success = True
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        return resp

    def test_retries_on_transient_error_and_succeeds(self):
        from app.scraper import _call_llm_api

        call_count = [0]

        async def flaky(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise httpx.TimeoutException("Transient failure")
            return self._llm_response('{"articles": []}')

        with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
            with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=flaky)):
                result = asyncio.run(_call_llm_api([{"role": "user", "content": "test"}]))

        assert call_count[0] == 3
        assert result.content == '{"articles": []}'
        assert result.prompt_tokens == 5
        assert result.completion_tokens == 2

    def test_raises_original_error_after_max_attempts(self):
        from app.scraper import _call_llm_api

        async def always_fail(*args, **kwargs):
            raise httpx.ConnectError("LLM unavailable")

        with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
            with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=always_fail)):
                with pytest.raises(httpx.ConnectError):
                    asyncio.run(_call_llm_api([{"role": "user", "content": "test"}]))

    def test_does_not_retry_non_transient_error(self):
        from app.scraper import _call_llm_api

        call_count = [0]

        async def always_400(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.is_success = False
            resp.status_code = 400
            resp.json.return_value = {"error": {"message": "bad request"}}
            resp.text = '{"error": {"message": "bad request"}}'
            resp.raise_for_status.side_effect = httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=resp)
            return resp

        with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
            with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=always_400)):
                with pytest.raises(httpx.HTTPStatusError):
                    asyncio.run(_call_llm_api([{"role": "user", "content": "test"}]))

        assert call_count[0] == 1

    def test_extract_articles_returns_empty_after_retry_exhausted(self):
        from app.scraper import _extract_articles_with_llm

        async def always_fail(*args, **kwargs):
            raise httpx.ConnectError("LLM unavailable")

        with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
            with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=always_fail)):
                result = asyncio.run(
                    _extract_articles_with_llm(
                        "# Test\n[Article](https://example.com)",
                        "https://example.com",
                        1,
                    )
                )

        assert result == ([], 0, 0)
