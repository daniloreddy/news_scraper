"""ConfigManager: .env baseline + data/config.json runtime overrides."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import dotenv_values, load_dotenv

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
    "METRICS_RETENTION_DAYS": "30",
    "DEBUG": "false",
    "REFRESH_ENABLED": "true",
    "REFRESH_INTERVAL": "30",
}

_OVERRIDE_FILE = Path("data/config.json")
_SECRET_KEYS = {"LLM_API_KEY", "API_AUTH_TOKEN"}
_MASK = "••••••"


class ConfigManager:
    """Single source of truth for runtime config. Singleton."""

    _instance: Optional["ConfigManager"] = None
    _cache: dict[str, str]

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache = {}
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        # Export .env into os.environ (no override) so vars read via os.getenv
        # outside ConfigManager (TRUSTED_PROXIES, AUTH_SECURE_COOKIE) work in local dev.
        load_dotenv()
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

        for key, value in updates.items():
            stripped = value.strip()
            if not stripped or stripped == _MASK:
                continue
            overrides[key] = stripped
            self._cache[key] = stripped

        _OVERRIDE_FILE.write_text(json.dumps(overrides, indent=2), encoding="utf-8")


config = ConfigManager()
