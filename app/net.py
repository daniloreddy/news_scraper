"""Shared client-IP resolution for rate limiting and auth (main.py + ui/auth.py)."""

import os

from fastapi import Request


def trusted_proxies() -> set[str]:
    raw = os.getenv("TRUSTED_PROXIES", "127.0.0.1")
    return {p.strip() for p in raw.split(",") if p.strip()}


def resolve_client_ip(request: Request) -> str:
    """Resolve the real client IP. Forwarded headers are only trusted when the
    direct connection comes from a known reverse proxy (TRUSTED_PROXIES), otherwise
    clients could spoof them to bypass per-IP rate limiting."""
    host = request.client.host if request.client else ""
    if host in trusted_proxies():
        for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
            v = request.headers.get(header, "")
            if v:
                return v.split(",")[0].strip()
    return host or "unknown"
