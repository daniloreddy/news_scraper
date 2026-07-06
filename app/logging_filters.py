"""Logging filter: redacts configured secrets and neutralizes CR/LF in every log record."""

import logging
import re

from .config import config

_CRLF_RE = re.compile(r"[\r\n]+")
_SECRET_CONFIG_KEYS = ("LLM_API_KEY", "API_AUTH_TOKEN")


class CredentialFilter(logging.Filter):
    """Strips CR/LF (log injection via user-controlled input) and redacts secret
    config values from every formatted log message before it reaches a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = _CRLF_RE.sub(" ", record.getMessage())
        for key in _SECRET_CONFIG_KEYS:
            secret = config.get(key)
            if secret:
                message = message.replace(secret, "***REDACTED***")
        record.msg = message
        record.args = ()
        return True
