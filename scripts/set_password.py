#!/usr/bin/env python3
"""CLI to set the dashboard UI password before first run."""
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_VENV_DIR = _ROOT / ("venv" if sys.platform == "win32" else ".venv")
_VENV_PYTHON = _VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _bootstrap() -> None:
    # If deps are already importable (e.g. inside Docker), skip venv entirely.
    sys.path.insert(0, str(_ROOT))
    try:
        import app.ui.router  # noqa: F401

        return
    except ImportError:
        sys.path.pop(0)

    if not _VENV_PYTHON.exists():
        print(
            f"Errore: venv non trovato in {_VENV_DIR}. "
            f"Esegui prima scripts/run.{'bat' if sys.platform == 'win32' else 'sh'} "
            "per crearlo, poi rilancia questo script."
        )
        sys.exit(1)
    if Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
        sys.exit(subprocess.run([str(_VENV_PYTHON), *sys.argv]).returncode)


_bootstrap()

import getpass  # noqa: E402

sys.path.insert(0, str(_ROOT))

from redberry_webkit.auth import AuthManager  # noqa: E402


def main() -> None:
    print("=== News Scraper — Imposta password dashboard ===")
    auth = AuthManager(
        auth_file=Path("data/auth.json"),
        cookie_name="news_scraper_ui",
        token_ttl=7 * 24 * 3600,
    )
    pw1 = getpass.getpass("Nuova password: ")
    pw2 = getpass.getpass("Conferma password: ")
    if pw1 != pw2:
        print("Le password non corrispondono.")
        sys.exit(1)
    if len(pw1) < 8:
        print("La password deve essere di almeno 8 caratteri.")
        sys.exit(1)
    auth.set_password(pw1)
    print("Password impostata correttamente.")


if __name__ == "__main__":
    main()
