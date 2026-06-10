import httpx
import json
import sys


def main():
    print("--- news-scraper API Tester ---")

    # Input API Base URL
    base_url = input(
        "Inserisci l'URL di base dell'API [http://localhost:8088]: "
    ).strip()
    if not base_url:
        base_url = "http://localhost:8088"

    # Ensure no trailing slash
    base_url = base_url.rstrip("/")

    # Input Target URL to scrape
    target_url = input(
        "Inserisci l'URL del sito da scrapare [default del server]: "
    ).strip()

    # Input max_articles
    max_art_input = input(
        "Inserisci il numero massimo di articoli (max_articles) [1]: "
    ).strip()
    try:
        max_articles = int(max_art_input) if max_art_input else 1
    except ValueError:
        print("Errore: max_articles deve essere un numero intero.")
        sys.exit(1)

    payload = {"max_articles": max_articles}
    if target_url:
        payload["url"] = target_url

    endpoint = f"{base_url}/scrape"
    print(f"\n[INFO] Invio richiesta POST a {endpoint}...")
    print(f"[INFO] Payload: {payload}")

    try:
        # Timeout aumentato a 120s per l'estrazione LLM
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
