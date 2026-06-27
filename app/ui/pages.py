"""NiceGUI dashboard pages: monitoring + config."""

import datetime
from typing import Optional

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app as ng_app
from nicegui import ui

from .. import metrics as mdb
from ..config import config
from .router import auth


def _check_auth(request: Request) -> bool:
    return auth.verify_token(request.cookies.get(auth.cookie_name, ""))


def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M:%S")


def _metric_card(label: str, value: str, color: str = "primary") -> None:
    with ui.card().classes("q-pa-md").style("min-width:130px; flex:1;"):
        ui.label(label).classes("text-xs text-grey-6 uppercase tracking-widest")
        ui.label(value).classes(f"text-2xl font-bold text-{color}")


def _header() -> None:
    with ui.header().classes("items-center row").style("height:48px; padding:0 1rem;"):
        ui.label("News Scraper").classes("text-h6")
        ui.space()
        is_dark: bool = ng_app.storage.user.get("dark_mode", True)
        dark = ui.dark_mode(is_dark)

        def _toggle_theme() -> None:
            dark.toggle()
            ng_app.storage.user["dark_mode"] = dark.value

        ui.button(icon="light_mode", on_click=_toggle_theme).props("flat round dense color=white")
        ui.button(
            "Logout",
            on_click=lambda: ui.run_javascript("window.location.href='/auth/logout'"),
        ).props("flat dense no-caps color=white").classes("text-xs q-ml-sm")


def _sidebar(active: str) -> None:
    nav_items = [
        ("Dashboard",      "dashboard", "/"),
        ("Configurazione", "settings",  "/config"),
    ]
    with ui.column().style("width:180px; padding:1rem 0; min-height:calc(100vh - 48px); flex-shrink:0;"):
        ui.label("MONITORAGGIO").classes("text-xs text-grey-6 uppercase q-px-md q-mb-xs")
        for label, icon, path in nav_items:
            btn = (
                ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p))
                .props("flat align-left no-caps")
                .classes("full-width q-px-md")
            )
            if active == label:
                btn.props("color=primary")
        ui.space()
        ui.label("v1.1.0").classes("text-xs text-grey-6 q-px-md q-pb-sm")


@ui.page("/")
async def dashboard_page(request: Request) -> Optional[RedirectResponse]:
    if not _check_auth(request):
        return RedirectResponse("/login")

    _header()

    with ui.row().style("width:100%; gap:0; flex:1;"):
        _sidebar("Dashboard")

        with ui.column().style("flex:1; padding:1.25rem; gap:1rem; overflow:auto;"):
            ui.label("Dashboard").classes("text-h6")

            stats_row = ui.row().classes("q-gutter-sm items-stretch full-width")
            ui.separator()
            history_label = ui.label("").classes("text-subtitle2 text-grey-6")
            history_wrap = ui.column().style("width:100%;")
            refresh_label = ui.label("").classes("text-xs text-grey-6 q-mt-sm")

            async def refresh() -> None:
                stats = await mdb.get_stats(hours=24)
                history = await mdb.get_history(limit=50)

                stats_row.clear()
                with stats_row:
                    _metric_card("Richieste 24h", str(stats["total"]))
                    _metric_card("OK", str(stats["ok"]), "positive")
                    _metric_card("Errori", str(stats["errors"]), "negative")
                    _metric_card("Durata media", f"{stats['avg_duration_s']}s")
                    _metric_card("Token prompt", str(stats["prompt_tokens"]), "warning")
                    _metric_card("Token completion", str(stats["completion_tokens"]), "info")

                history_label.set_text(
                    f"Storico richieste ({len(history)} record più recenti)"
                )

                history_wrap.clear()
                with history_wrap:
                    cols = [
                        {"name": "ts",       "label": "Timestamp",  "field": "ts",       "align": "left"},
                        {"name": "endpoint", "label": "Endpoint",   "field": "endpoint", "align": "left"},
                        {"name": "url",      "label": "URL",        "field": "url",      "align": "left"},
                        {"name": "status",   "label": "Status",     "field": "status",   "align": "center"},
                        {"name": "duration", "label": "Durata (s)", "field": "duration", "align": "right"},
                        {"name": "tokens",   "label": "Tokens p/c", "field": "tokens",   "align": "right"},
                    ]
                    rows = []
                    for r in history:
                        raw_url = r["url"] or ""
                        short_url = (raw_url[:55] + "…") if len(raw_url) > 55 else raw_url
                        rows.append({
                            "ts":       _fmt_ts(r["ts"]),
                            "endpoint": r["endpoint"],
                            "url":      short_url,
                            "status":   r["status"],
                            "duration": f"{r['duration']:.1f}" if r["duration"] else "—",
                            "tokens":   f"{r['prompt_tokens'] or 0}/{r['completion_tokens'] or 0}",
                        })
                    tbl = ui.table(columns=cols, rows=rows).classes("w-full")
                    tbl.add_slot(
                        "body-cell-status",
                        """
                        <q-td :props="props">
                          <q-badge
                            :color="props.value === 'ok' ? 'positive' : props.value === 'error' ? 'negative' : 'warning'"
                            :label="props.value"
                          />
                        </q-td>
                        """,
                    )
                    tbl.run_method("$forceUpdate")

                now = datetime.datetime.now().strftime("%H:%M:%S")
                refresh_label.set_text(f"Aggiornato: {now} · auto-refresh 30s")

            await refresh()
            ui.timer(30, refresh)

    return None


@ui.page("/config")
async def config_page(request: Request) -> Optional[RedirectResponse]:
    if not _check_auth(request):
        return RedirectResponse("/login")

    _header()

    with ui.row().style("width:100%; gap:0; flex:1;"):
        _sidebar("Configurazione")

        with ui.column().style("flex:1; padding:1.25rem; gap:0.75rem; max-width:720px;"):
            ui.label("Configurazione").classes("text-h6")
            with ui.row().classes("items-center q-gutter-sm"):
                ui.label("Legenda:").classes("text-caption text-grey-6")
                ui.badge("hot-reload").props("color=positive")
                ui.badge("richiede restart").props("color=warning")
            ui.label(
                "Lascia vuoto i campi segreto per mantenere il valore esistente."
            ).classes("text-caption text-grey-6")

            cur = config.get_public()

            with ui.card().classes("q-pa-md w-full"):
                with ui.row().classes("items-center q-mb-xs"):
                    ui.label("LLM").classes("text-xs text-grey-6 uppercase tracking-widest")
                    ui.space()
                    ui.badge("hot-reload").props("color=positive")
                ui.separator().classes("q-mb-sm")

                inp_base_url = ui.input("Base URL", value=cur.get("LLM_BASE_URL", "")).classes("w-full")
                inp_api_key = ui.input(
                    "API Key", value="", placeholder="lascia vuoto per mantenere"
                ).classes("w-full").props("type=password")
                inp_model = ui.input("Modello", value=cur.get("LLM_MODEL", "")).classes("w-full")
                with ui.row().classes("w-full q-gutter-sm"):
                    inp_temp = ui.input("Temperature", value=cur.get("LLM_TEMPERATURE", "")).style("flex:1;")
                    inp_llm_timeout = ui.input("Timeout LLM (s)", value=cur.get("LLM_TIMEOUT", "")).style("flex:1;")
                inp_max_prompt = ui.input(
                    "Max prompt chars", value=cur.get("LLM_MAX_PROMPT_CHARS", "8000")
                ).classes("w-full").props('hint="Tronca il markdown inviato all\'LLM. Riduci se il server rifiuta con context exceeded."')

            with ui.card().classes("q-pa-md w-full"):
                with ui.row().classes("items-center q-mb-xs"):
                    ui.label("Scraping").classes("text-xs text-grey-6 uppercase tracking-widest")
                    ui.space()
                    ui.badge("hot-reload").props("color=positive")
                ui.separator().classes("q-mb-sm")

                inp_scrape_timeout = ui.input(
                    "Timeout Scraping (s)", value=cur.get("SCRAPE_TIMEOUT", "")
                ).classes("w-full")
                debug_switch = ui.switch(
                    "Debug mode (salva HTML/MD/JSON in debug/)",
                    value=cur.get("DEBUG", "false").lower() == "true",
                ).classes("q-mt-sm")

            with ui.card().classes("q-pa-md w-full"):
                with ui.row().classes("items-center q-mb-xs"):
                    ui.label("API").classes("text-xs text-grey-6 uppercase tracking-widest")
                    ui.space()
                    ui.badge("hot-reload").props("color=positive")
                ui.separator().classes("q-mb-sm")

                with ui.row().classes("items-center q-gutter-sm q-mb-xs"):
                    ui.label("Rate Limit").classes("text-caption text-grey-6")
                    ui.badge("richiede restart").props("color=warning")
                inp_rate_limit = ui.input("Rate Limit", value=cur.get("RATE_LIMIT", "")).classes("w-full")
                ui.label("Formato: N/second|minute|hour").classes("text-xs text-grey-6 q-mb-sm")
                inp_auth_token = ui.input(
                    "Auth Token API", value="", placeholder="lascia vuoto per mantenere"
                ).classes("w-full").props("type=password")

            async def save() -> None:
                config.update_many({
                    "LLM_BASE_URL":         inp_base_url.value,
                    "LLM_API_KEY":          inp_api_key.value,
                    "LLM_MODEL":            inp_model.value,
                    "LLM_TEMPERATURE":      inp_temp.value,
                    "LLM_TIMEOUT":          inp_llm_timeout.value,
                    "LLM_MAX_PROMPT_CHARS": inp_max_prompt.value,
                    "SCRAPE_TIMEOUT":       inp_scrape_timeout.value,
                    "DEBUG":                "true" if debug_switch.value else "false",
                    "RATE_LIMIT":           inp_rate_limit.value,
                    "API_AUTH_TOKEN":       inp_auth_token.value,
                })
                inp_api_key.set_value("")
                inp_auth_token.set_value("")
                ui.notify("Configurazione salvata", type="positive", position="top")

            ui.button("Salva", icon="save", on_click=save).props("color=primary").classes("q-mt-sm")

    return None
