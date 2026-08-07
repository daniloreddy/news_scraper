"""SSRF guard shared by app/main.py (request validation) and app/scraper.py
(post-redirect / LLM-extracted URL revalidation) -- kept in its own module so
scraper.py doesn't need to import from main.py (circular import)."""

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {"localhost", "0.0.0.0", "0"}


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    )


def validate_url(v: str) -> str:
    """Block non-http(s) schemes and requests targeting private/loopback/link-local
    networks (SSRF guard). Resolves the hostname via DNS and validates every address
    returned, not just literal-IP hosts -- this also catches numeric/hex IP strings
    that the OS resolver treats as literals (e.g. "2130706433", "0x7f000001") and
    DNS-rebinding hostnames, which a plain ipaddress.ip_address() parse misses.

    Callers that navigate with a browser (Playwright) MUST call this again on the
    post-navigation URL (page.url after page.goto()) -- validating only the
    initial URL doesn't stop a 3xx redirect to an internal target.
    """
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL must have a valid hostname")
    if host.lower() in _BLOCKED_HOSTNAMES:
        raise ValueError("URL hostname not allowed")

    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        addr = None

    if addr is not None:
        if _is_blocked_ip(addr):
            raise ValueError("URL hostname not allowed")
        return v

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve hostname: {host}") from e

    for info in infos:
        resolved_ip = info[4][0]
        try:
            resolved_addr = ipaddress.ip_address(resolved_ip)
        except ValueError:
            continue
        if _is_blocked_ip(resolved_addr):
            raise ValueError("URL hostname resolves to a disallowed address")

    return v
