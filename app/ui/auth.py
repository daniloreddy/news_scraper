"""AuthManager for the NiceGUI dashboard UI."""

import hashlib
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ..config import config
from ..net import resolve_client_ip

logger = logging.getLogger(__name__)


class _RateLimit:
    def __init__(
        self,
        per_ip_max: int = 5,
        per_ip_window: int = 300,
        global_max: int = 20,
        global_window: int = 60,
    ) -> None:
        self._ip: dict[str, tuple[int, float]] = {}
        self._global: tuple[int, float] = (0, time.time())
        self._per_ip_max = per_ip_max
        self._per_ip_win = per_ip_window
        self._global_max = global_max
        self._global_win = global_window
        self._lock = threading.Lock()

    def check(self, ip: str) -> Optional[str]:
        with self._lock:
            now = time.time()
            gc, gt = self._global
            if now - gt > self._global_win:
                gc, gt = 0, now
            gc += 1
            self._global = (gc, gt)
            if gc > self._global_max:
                return "Troppi tentativi — riprova tra un minuto"

            count, since = self._ip.get(ip, (0, now))
            if now - since > self._per_ip_win:
                count, since = 0, now
            count += 1
            self._ip[ip] = (count, since)
            if count > self._per_ip_max:
                return "Troppi tentativi dal tuo IP — riprova tra 5 minuti"
            return None

    def purge_expired(self) -> None:
        """Drop IP entries whose window has expired, so the dict doesn't grow forever."""
        with self._lock:
            now = time.time()
            expired = [
                ip
                for ip, (_, since) in self._ip.items()
                if now - since > self._per_ip_win
            ]
            for ip in expired:
                del self._ip[ip]


class AuthManager:
    def __init__(
        self,
        auth_file: Path,
        cookie_name: str = "ui_session",
        token_ttl: int = 7 * 24 * 3600,
    ) -> None:
        self._file = auth_file
        self.cookie_name = cookie_name
        self._ttl = token_ttl
        self._rl = _RateLimit()
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {}

    def _save(self, data: dict) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @property
    def _secret(self) -> str:
        if "secret" not in self._data:
            self._data["secret"] = secrets.token_hex(32)
            self._save(self._data)
        return self._data["secret"]

    def set_password(self, password: str) -> None:
        salt = secrets.token_hex(16)
        h = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=16384,
            r=8,
            p=1,
        ).hex()
        self._data["password_hash"] = f"{salt}:{h}"
        self._save(self._data)
        logger.info("UI password updated")

    def _verify_password(self, password: str) -> bool:
        ph = self._data.get("password_hash", "")
        if not ph or ":" not in ph:
            return False
        salt_hex, expected = ph.split(":", 1)
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=16384,
            r=8,
            p=1,
        ).hex()
        return secrets.compare_digest(actual, expected)

    def create_token(self) -> str:
        payload = {"exp": int(time.time()) + self._ttl, "iat": int(time.time())}
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def verify_token(self, token: str) -> bool:
        if not token:
            return False
        try:
            jwt.decode(token, self._secret, algorithms=["HS256"])
            return True
        except jwt.InvalidTokenError:
            return False

    def purge_expired_blocks(self) -> None:
        """Drop expired per-IP rate-limit entries. Call periodically from a background task."""
        self._rl.purge_expired()

    def _is_secure(self, request: Request) -> bool:
        if config.get_bool("AUTH_SECURE_COOKIE"):
            return True
        return request.headers.get("x-forwarded-proto") == "https"

    async def handle_login(self, request: Request) -> Response:
        form = await request.form()
        password = str(form.get("password", ""))
        ip = resolve_client_ip(request)

        block_msg = self._rl.check(ip)
        if block_msg:
            return JSONResponse(status_code=429, content={"detail": block_msg})

        if not self._data.get("password_hash"):
            logger.warning("UI auth: no password set — all logins rejected")
            return JSONResponse(
                status_code=401, content={"detail": "Password non configurata"}
            )

        if not self._verify_password(password):
            return JSONResponse(
                status_code=401, content={"detail": "Password non valida"}
            )

        token = self.create_token()
        resp = JSONResponse(content={"ok": True})
        resp.set_cookie(
            self.cookie_name,
            token,
            httponly=True,
            samesite="strict",
            secure=self._is_secure(request),
            max_age=self._ttl,
        )
        return resp

    def handle_logout(self, request: Request) -> Response:
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie(self.cookie_name)
        return resp
