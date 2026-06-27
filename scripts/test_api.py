#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_VENV_DIR = _ROOT / ("venv" if sys.platform == "win32" else ".venv")
_VENV_PYTHON = _VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _bootstrap() -> None:
    if not _VENV_PYTHON.exists():
        print("Creating venv...")
        subprocess.run([sys.executable, "-m", "venv", str(_VENV_DIR)], check=True)
        subprocess.run(
            [str(_VENV_PYTHON), "-m", "pip", "install", "-r", str(_ROOT / "requirements.txt")],
            check=True,
        )
    if Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
        sys.exit(subprocess.run([str(_VENV_PYTHON), *sys.argv]).returncode)


_bootstrap()

import httpx  # noqa: E402
import json  # noqa: E402


def main() -> None:
    print("--- news-scraper API Tester ---")

    base_url = input(
        "Inserisci l'URL di base dell'API [http://localhost:8088]: "
    ).strip()
    if not base_url:
        base_url = "http://localhost:8088"
    base_url = base_url.rstrip("/")

    target_url = input(
        "Inserisci l'URL del sito da scrapare [default del server]: "
    ).strip()

    max_art_input = input(
        "Inserisci il numero massimo di articoli (max_articles) [1]: "
    ).strip()
    try:
        max_articles = int(max_art_input) if max_art_input else 1
    except ValueError:
        print("Errore: max_articles deve essere un numero intero.")
        sys.exit(1)

    payload: dict[str, object] = {"max_articles": max_articles}
    if target_url:
        payload["url"] = target_url

    endpoint = f"{base_url}/scrape"
    print(f"\n[INFO] Invio richiesta POST a {endpoint}...")
    print(f"[INFO] Payload: {payload}")

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(endpoint, json=payload)

        print(f"\n[STATUS] {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"\n[RISULTATO] Trovati {len(data)} articoli:\n")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(f"\n[ERRORE] Il server ha risposto con: {response.text}")

    except httpx.ConnectError:
        print(
            f"\n[ERRORE] Impossibile connettersi a {base_url}. Assicurati che il server sia attivo."
        )
    except Exception as e:
        print(f"\n[ERRORE IMPREVISTO] {e}")


if __name__ == "__main__":
    main()
