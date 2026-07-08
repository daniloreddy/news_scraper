"""ConfigManager: `.env` is the single source of truth, with file-watch hot-reload."""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import dotenv_values, find_dotenv, set_key

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
    "TRUSTED_PROXIES": "127.0.0.1",
    "AUTH_SECURE_COOKIE": "false",
    "TZ": "UTC",
}

_LEGACY_OVERRIDE_FILE = Path("data/config.json")
_SECRET_KEYS = {"LLM_API_KEY", "API_AUTH_TOKEN"}
_MASK = "••••••"


def _resolve_env_path() -> Path:
    """Resolve the `.env` path. Precedence: ENV_FILE (set by docker-compose,
    since the Docker CMD invokes uvicorn directly and can't pass --env-file)
    > --env-file CLI flag (mirrors main.py's stage-1 parser, for local dev)
    > nearest .env found from cwd."""
    env_file_var = os.environ.get("ENV_FILE")
    if env_file_var:
        return Path(env_file_var)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", type=str, default=None)
    args, _ = parser.parse_known_args()
    if args.env_file:
        return Path(args.env_file)
    found = find_dotenv(usecwd=True)
    return Path(found) if found else Path(".env")


class ConfigManager:
    """Single source of truth for runtime config. Singleton."""

    _instance: Optional["ConfigManager"] = None
    _cache: dict[str, str]
    _env_path: Path
    _last_mtime: float

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache = {}
            cls._instance._env_path = _resolve_env_path()
            cls._instance._last_mtime = 0.0
            logger.info("Config: uso .env=%s", cls._instance._env_path)
            cls._instance._migrate_legacy_override_file()
            cls._instance._load()
        return cls._instance

    def _migrate_legacy_override_file(self) -> None:
        """One-shot migration for upgrades: fold values from the retired
        data/config.json override file into .env, then rename it so it isn't
        picked up again on the next boot."""
        if not _LEGACY_OVERRIDE_FILE.exists():
            return
        try:
            overrides: dict[str, Any] = json.loads(
                _LEGACY_OVERRIDE_FILE.read_text(encoding="utf-8")
            )
            for key, value in overrides.items():
                if value is not None:
                    set_key(str(self._env_path), key, str(value), quote_mode="never")
            migrated_path = _LEGACY_OVERRIDE_FILE.with_name("config.json.migrated")
            _LEGACY_OVERRIDE_FILE.rename(migrated_path)
        except (OSError, ValueError) as e:
            # Never let a migration hiccup (unwritable .env, malformed JSON, ecc.)
            # crash app boot — retried on the next start, legacy file untouched.
            logger.warning("Migrazione data/config.json fallita, riprovo al prossimo avvio: %s", e)
            return
        logger.warning(
            "Migrato override legacy %s in %s (rinominato in %s).",
            _LEGACY_OVERRIDE_FILE,
            self._env_path,
            migrated_path,
        )

    def _load(self) -> None:
        if not self._env_path.exists():
            logger.warning(
                "File .env non trovato in %s — uso solo i default hardcoded "
                "(controlla il bind-mount/ENV_FILE se questo gira in Docker).",
                self._env_path,
            )
        merged = dict(_DEFAULTS)
        for k, v in dotenv_values(str(self._env_path)).items():
            if v is not None:
                merged[k] = v
        self._cache = merged
        try:
            self._last_mtime = self._env_path.stat().st_mtime
        except OSError:
            self._last_mtime = 0.0

    def reload_if_stale(self) -> bool:
        """Reload from disk if `.env` changed since the last load. Returns True if reloaded."""
        try:
            mtime = self._env_path.stat().st_mtime
        except OSError:
            return False
        if mtime == self._last_mtime:
            return False
        self._load()
        logger.info("Config ricaricata da %s (hot-reload).", self._env_path)
        return True

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
        return self._cache.get(key, "false").strip().lower() in ("true", "1", "yes")

    def get_public(self) -> dict[str, str]:
        """Config dict with secrets replaced by mask for UI display."""
        result = dict(self._cache)
        for k in _SECRET_KEYS:
            if result.get(k):
                result[k] = _MASK
        return result

    def update_many(self, updates: dict[str, str]) -> None:
        """Persist updates directly to `.env`. Blank or mask value = keep existing secret."""
        for key, value in updates.items():
            stripped = value.strip()
            if not stripped or stripped == _MASK:
                continue
            set_key(str(self._env_path), key, stripped, quote_mode="never")
            self._cache[key] = stripped
        try:
            self._last_mtime = self._env_path.stat().st_mtime
        except OSError:
            pass


config = ConfigManager()
