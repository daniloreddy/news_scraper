"""FastAPI router for UI login/logout endpoints and shared auth instance."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from redberry_webkit.auth import AuthManager, client_ip, is_secure_context

from ..config import config

TRUSTED_PROXIES = {
    ip.strip() for ip in config.get("TRUSTED_PROXIES", "127.0.0.1").split(",") if ip.strip()
}

auth = AuthManager(
    auth_file=Path("data/auth.json"),
    cookie_name="news_scraper_ui",
    token_ttl=7 * 24 * 3600,
)

router = APIRouter()


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return client_ip(request.headers, host, TRUSTED_PROXIES)


@router.get("/login")
async def login_page() -> Response:
    return FileResponse("static/login.html")


@router.post("/auth/login")
async def do_login(request: Request) -> Response:
    form = await request.form()
    password = str(form.get("password", ""))
    ip = _client_ip(request)

    if auth.is_global_limited():
        return JSONResponse(
            status_code=429, content={"detail": "Troppi tentativi — riprova tra un minuto"}
        )
    if auth.is_ip_blocked(ip):
        return JSONResponse(
            status_code=429,
            content={"detail": "Troppi tentativi dal tuo IP — riprova tra 5 minuti"},
        )
    if not auth.has_password():
        return JSONResponse(
            status_code=401, content={"detail": "Password non configurata"}
        )

    success = auth.verify_password(password)
    auth.record_attempt(ip, success=success)
    if not success:
        return JSONResponse(status_code=401, content={"detail": "Password non valida"})

    token = auth.create_token()
    resp = JSONResponse(content={"ok": True})
    resp.set_cookie(
        auth.cookie_name,
        token,
        httponly=True,
        samesite="strict",
        secure=is_secure_context(request.headers),
        max_age=auth.token_ttl,
    )
    return resp


@router.get("/auth/logout")
async def do_logout() -> Response:
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(auth.cookie_name)
    return resp
