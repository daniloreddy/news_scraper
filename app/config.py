"""ConfigManager: `.env` is the single source of truth, with file-watch hot-reload."""

import json
import logging
from pathlib import Path
from typing import Any

from dotenv import set_key
from redberry_webkit.config import ConfigManager
from redberry_webkit.env_resolver import resolve_env_path

logger = logging.getLogger(__name__)

# UI-editable vs manual-only rule (enforced by convention in app/ui/pages.py,
# not validated here): a key belongs in the /config editor iff BOTH hold —
# (1) hot-reload capable (applies without restart, i.e. lives in this dict), AND
# (2) it is not a trust-boundary/security control over the dashboard's own
#     session (TRUSTED_PROXIES, AUTH_SECURE_COOKIE are deliberately excluded
#     from the UI even though hot-reload: the panel sits behind that same
#     session, so a compromised dashboard cookie must not be able to weaken
#     the auth protecting it). Manual .env edit only for those two.
# PORT/HOST/DEV fail condition (1) entirely — read once in main.py's
# __main__ block, not part of this dict, restart required.
# Secrets (LLM_API_KEY, API_AUTH_TOKEN) don't trip condition (2): they're
# credentials toward external parties (LLM provider, API callers), not
# controls over the dashboard session itself — stay UI-editable via the
# write-only masked pattern in app/ui/pages.py.
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
_SECRET_KEYS = {"LLM_API_KEY", "API_AUTH_TOKEN"}

_LEGACY_OVERRIDE_FILE = Path("data/config.json")


def _migrate_legacy_override_file(env_path: Path) -> None:
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
                set_key(str(env_path), key, str(value), quote_mode="never")
        migrated_path = _LEGACY_OVERRIDE_FILE.with_name("config.json.migrated")
        _LEGACY_OVERRIDE_FILE.rename(migrated_path)
    except (OSError, ValueError) as e:
        # Never let a migration hiccup (unwritable .env, malformed JSON, ecc.)
        # crash app boot — retried on the next start, legacy file untouched.
        logger.warning(
            "Migrazione data/config.json fallita, riprovo al prossimo avvio: %s", e
        )
        return
    logger.warning(
        "Migrato override legacy %s in %s (rinominato in %s).",
        _LEGACY_OVERRIDE_FILE,
        env_path,
        migrated_path,
    )


_env_path = resolve_env_path()
_migrate_legacy_override_file(_env_path)

config = ConfigManager(defaults=_DEFAULTS, secret_keys=_SECRET_KEYS, env_path=_env_path)
