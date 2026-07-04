"""FastAPI router for UI login/logout endpoints and shared auth instance."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from .auth import AuthManager

_AUTH_FILE = Path("data/auth.json")
auth = AuthManager(auth_file=_AUTH_FILE, cookie_name="news_scraper_ui")

router = APIRouter()


@router.get("/login")
async def login_page() -> Response:
    return FileResponse("static/login.html")


@router.post("/auth/login")
async def do_login(request: Request) -> Response:
    return await auth.handle_login(request)


@router.get("/auth/logout")
async def do_logout(request: Request) -> Response:
    return auth.handle_logout(request)
