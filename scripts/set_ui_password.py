"""CLI to set the dashboard UI password before first run."""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.ui.auth import AuthManager

auth = AuthManager(auth_file=Path("data/auth.json"), cookie_name="news_scraper_ui")


def main() -> None:
    print("=== News Scraper — Imposta password dashboard ===")
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
