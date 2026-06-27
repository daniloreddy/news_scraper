"""ConfigManager: .env baseline + data/config.json runtime overrides."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, str] = {
    "LLM_BASE_URL": "http://localhost:1234/v1",
    "LLM_API_KEY": "",
    "LLM_MODEL": "gpt-4o-mini",
    "LLM_TEMPERATURE": "0.1",
    "LLM_TIMEOUT": "60.0",
    "LLM_MAX_PROMPT_CHARS": "8000",
    "API_AUTH_TOKEN": "",
    "SCRAPE_TIMEOUT": "300",
    "RATE_LIMIT": "20/minute",
    "DEBUG": "false",
}

_OVERRIDE_FILE = Path("data/config.json")
_SECRET_KEYS = {"LLM_API_KEY", "API_AUTH_TOKEN"}
_HTTP_CLIENT_KEYS = {"LLM_BASE_URL", "LLM_API_KEY", "LLM_TIMEOUT"}
_MASK = "••••••"


class ConfigManager:
    """Single source of truth for runtime config. Singleton."""

    _instance: Optional["ConfigManager"] = None

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache: dict[str, str] = {}
            cls._instance._http_client: Optional[httpx.AsyncClient] = None
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        merged = dict(_DEFAULTS)
        # .env file (local dev)
        for k, v in dotenv_values().items():
            if v is not None:
                merged[k] = v
        # OS environment variables — takes precedence (Docker / systemd / shell exports)
        for k in _DEFAULTS:
            if k in os.environ:
                merged[k] = os.environ[k]
        if _OVERRIDE_FILE.exists():
            try:
                overrides: dict[str, Any] = json.loads(
                    _OVERRIDE_FILE.read_text(encoding="utf-8")
                )
                for k, v in overrides.items():
                    if v is not None:
                        merged[k] = str(v)
            except Exception as e:
                logger.warning("Cannot read config override: %s", e)
        self._cache = merged
        self._rebuild_http_client()

    def get(self, key: str, default: str = "") -> str:
        return self._cache.get(key, default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self._cache.get(key, str(default)))
        except ValueError:
            return default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._cache.get(key, str(default)))
        except ValueError:
            return default

    def get_bool(self, key: str) -> bool:
        return self._cache.get(key, "false").lower() == "true"

    def get_public(self) -> dict[str, str]:
        """Config dict with secrets replaced by mask for UI display."""
        result = dict(self._cache)
        for k in _SECRET_KEYS:
            if result.get(k):
                result[k] = _MASK
        return result

    def update_many(self, updates: dict[str, str]) -> None:
        """Persist updates to override file. Blank or mask value = keep existing secret."""
        _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        overrides: dict[str, Any] = {}
        if _OVERRIDE_FILE.exists():
            try:
                overrides = json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        needs_rebuild = False
        for key, value in updates.items():
            stripped = value.strip()
            if not stripped or stripped == _MASK:
                continue
            overrides[key] = stripped
            self._cache[key] = stripped
            if key in _HTTP_CLIENT_KEYS:
                needs_rebuild = True

        _OVERRIDE_FILE.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
        if needs_rebuild:
            self._rebuild_http_client()

    def _rebuild_http_client(self) -> None:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        api_key = self.get("LLM_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if self._http_client is not None:
            # Close old client without awaiting (sync context — best effort)
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    loop.create_task(self._http_client.aclose())
            except Exception:
                pass

        self._http_client = httpx.AsyncClient(
            base_url=self.get("LLM_BASE_URL") or "http://localhost:1234/v1",
            headers=headers,
            timeout=self.get_float("LLM_TIMEOUT", 60.0),
        )

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._rebuild_http_client()
        return self._http_client  # type: ignore[return-value]


config = ConfigManager()
