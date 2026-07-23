"""NiceGUI dashboard pages: monitoring + config."""

import datetime
import logging
import re
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nicegui import app as ng_app
from nicegui import ui

from .. import metrics as mdb
from ..config import config

logger = logging.getLogger(__name__)

_RATE_LIMIT_RE = re.compile(r"^\d+/(second|minute|hour|day)$")

_APP_NAME: str = "News Scraper"
_NAV_ITEMS: list[tuple[str, str, str]] = [
    ("Dashboard", "dashboard", "/"),
    ("Configurazione", "settings", "/config"),
]


def _get_tz() -> ZoneInfo:
    tz_name = config.get("TZ", "UTC")
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("TZ '%s' non valido, uso UTC.", tz_name)
        return ZoneInfo("UTC")


def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=_get_tz()).strftime("%d/%m %H:%M:%S")


def _metric_card(label: str, value: str, color: str = "primary") -> None:
    with ui.card().classes("q-pa-md").style("min-width:130px; flex:1;"):
        ui.label(label).classes("text-caption text-grey-6 text-uppercase")
        ui.label(value).classes(f"text-h5 text-weight-bold text-{color}")


def _page_setup(section_title: str) -> Any:
    ui.page_title(f"{section_title} — {_APP_NAME}")
    return ui.dark_mode(value=ng_app.storage.user.get("dark_mode", True))


def _logout_action() -> None:
    ui.button(
        icon="logout",
        on_click=lambda: ui.run_javascript("window.location.href='/auth/logout'"),
    ).props("flat color=white round").tooltip("Esci")


def _header(
    page_title: str,
    nav_items: list[tuple[str, str, str]],
    current: str = "",
    *,
    dark: Any = None,
    extra_actions: Callable[[], None] | None = None,
) -> None:
    with ui.header().classes("bg-primary text-white items-center q-px-md q-gutter-sm"):
        ui.label(page_title).classes("text-h6 text-weight-bold col")

        for label, icon, path in nav_items:
            if label.lower() != current.lower():
                ui.button(icon=icon, on_click=lambda p=path: ui.navigate.to(p)).props("flat color=white round").tooltip(
                    label
                )

        if extra_actions is not None:
            extra_actions()

        if dark is not None:

            def _toggle_dark() -> None:
                dark.toggle()
                ng_app.storage.user["dark_mode"] = dark.value

            ui.button(icon="contrast", on_click=_toggle_dark).props("flat round dense color=white").tooltip(
                "Tema chiaro/scuro"
            )

        ui.label(_APP_NAME).classes("text-body2").style("opacity:0.6")


def _footer() -> None:
    with ui.footer().classes("bg-primary text-white q-px-md q-py-xs row items-center"):
        ui.label(_APP_NAME).classes("col text-caption").style("opacity:0.6")


@ui.page("/")
async def dashboard_page() -> None:
    dark = _page_setup("Dashboard")
    _header(
        "Dashboard",
        _NAV_ITEMS,
        current="Dashboard",
        dark=dark,
        extra_actions=_logout_action,
    )

    with ui.column().style("width:100%; padding:1.25rem; gap:1rem; overflow:auto;"):
        ui.label("Dashboard").classes("text-h6")

        stats_row = ui.row().classes("q-gutter-sm items-stretch full-width")
        ui.separator()
        history_label = ui.label("").classes("text-subtitle2 text-grey-6")
        history_wrap = ui.column().style("width:100%;")
        refresh_label = ui.label("").classes("text-caption text-grey-6").style("text-align:right; width:100%")

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

            history_label.set_text(f"Storico richieste ({len(history)} record più recenti)")

            history_wrap.clear()
            with history_wrap:
                cols = [
                    {
                        "name": "ts",
                        "label": "Timestamp",
                        "field": "ts",
                        "align": "left",
                    },
                    {
                        "name": "endpoint",
                        "label": "Endpoint",
                        "field": "endpoint",
                        "align": "left",
                    },
                    {"name": "url", "label": "URL", "field": "url", "align": "left"},
                    {
                        "name": "status",
                        "label": "Status",
                        "field": "status",
                        "align": "center",
                    },
                    {
                        "name": "duration",
                        "label": "Durata (s)",
                        "field": "duration",
                        "align": "right",
                    },
                    {
                        "name": "tokens",
                        "label": "Tokens p/c",
                        "field": "tokens",
                        "align": "right",
                    },
                ]
                rows = []
                for r in history:
                    raw_url = r["url"] or ""
                    short_url = (raw_url[:55] + "…") if len(raw_url) > 55 else raw_url
                    rows.append(
                        {
                            "ts": _fmt_ts(r["ts"]),
                            "endpoint": r["endpoint"],
                            "url": short_url,
                            "status": r["status"],
                            "duration": f"{r['duration']:.1f}" if r["duration"] else "—",
                            "tokens": f"{r['prompt_tokens'] or 0}/{r['completion_tokens'] or 0}",
                        }
                    )
                tbl = ui.table(columns=cols, rows=rows).classes("full-width")
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

            now = datetime.datetime.now(_get_tz()).strftime("%H:%M:%S")
            interval = config.get_int("REFRESH_INTERVAL", 30)
            refresh_label.set_text(f"Aggiornato: {now} · auto-refresh {interval}s")

        await refresh()
        if config.get_bool("REFRESH_ENABLED"):
            ui.timer(config.get_int("REFRESH_INTERVAL", 30), refresh)
        else:
            refresh_label.set_text("auto-refresh disabilitato")

    _footer()


@ui.page("/config")
async def config_page() -> None:
    dark = _page_setup("Configurazione")
    _header(
        "Configurazione",
        _NAV_ITEMS,
        current="Configurazione",
        dark=dark,
        extra_actions=_logout_action,
    )

    with ui.column().style("width:100%; max-width:720px; margin:0 auto; padding:1.25rem; gap:0.75rem;"):
        ui.label("Configurazione").classes("text-h6")
        with ui.row().classes("items-center q-gutter-sm"):
            ui.label("Legenda:").classes("text-caption text-grey-6")
            ui.badge("hot-reload").props("color=positive")
        ui.label("Lascia vuoto i campi segreto per mantenere il valore esistente.").classes("text-caption text-grey-6")
        ui.label("Le modifiche vengono scritte direttamente in .env ed effettive entro ~5s, senza restart.").classes(
            "text-caption text-grey-6"
        )

        cur = config.get_public()

        with ui.card().classes("q-pa-md full-width"):
            with ui.row().classes("items-center q-mb-xs"):
                ui.label("LLM").classes("text-caption text-grey-6 text-uppercase")
                ui.space()
                ui.badge("hot-reload").props("color=positive")
            ui.separator().classes("q-mb-sm")

            inp_base_url = ui.input("Base URL", value=cur.get("LLM_BASE_URL", "")).classes("full-width")
            inp_api_key = (
                ui.input("API Key", value="", placeholder="lascia vuoto per mantenere")
                .classes("full-width")
                .props("type=password")
            )
            inp_model = ui.input("Modello", value=cur.get("LLM_MODEL", "")).classes("full-width")
            with ui.row().classes("full-width q-gutter-sm"):
                inp_temp = ui.input("Temperature", value=cur.get("LLM_TEMPERATURE", "")).style("flex:1;")
                inp_llm_timeout = ui.input("Timeout LLM (s)", value=cur.get("LLM_TIMEOUT", "")).style("flex:1;")
            inp_max_prompt = (
                ui.input("Max prompt chars", value=cur.get("LLM_MAX_PROMPT_CHARS", "8000"))
                .classes("full-width")
                .props('hint="Tronca il markdown inviato all\'LLM. Riduci se il server rifiuta con context exceeded."')
            )

        with ui.card().classes("q-pa-md full-width"):
            with ui.row().classes("items-center q-mb-xs"):
                ui.label("Scraping").classes("text-caption text-grey-6 text-uppercase")
                ui.space()
                ui.badge("hot-reload").props("color=positive")
            ui.separator().classes("q-mb-sm")

            inp_scrape_timeout = ui.input("Timeout Scraping (s)", value=cur.get("SCRAPE_TIMEOUT", "")).classes(
                "full-width"
            )
            debug_switch = ui.switch(
                "Debug mode (salva HTML/MD/JSON in debug/)",
                value=cur.get("DEBUG", "false").lower() == "true",
            ).classes("q-mt-sm")

        with ui.card().classes("q-pa-md full-width"):
            with ui.row().classes("items-center q-mb-xs"):
                ui.label("Interfaccia").classes("text-caption text-grey-6 text-uppercase")
                ui.space()
                ui.badge("hot-reload").props("color=positive")
            ui.separator().classes("q-mb-sm")

            refresh_switch = ui.switch(
                "Auto-refresh abilitato",
                value=cur.get("REFRESH_ENABLED", "true").lower() == "true",
            ).classes("q-mb-sm")
            inp_refresh = (
                ui.input(
                    "Intervallo auto-refresh (s)",
                    value=cur.get("REFRESH_INTERVAL", "30"),
                )
                .classes("full-width")
                .props('hint="Secondi tra un aggiornamento automatico e il successivo"')
            )
            inp_timezone = (
                ui.input("Timezone", value=cur.get("TZ", "UTC"))
                .classes("full-width")
                .props('hint="Nome IANA, es. Europe/Rome. Usato per gli orari mostrati in dashboard."')
            )

        with ui.card().classes("q-pa-md full-width"):
            with ui.row().classes("items-center q-mb-xs"):
                ui.label("API").classes("text-caption text-grey-6 text-uppercase")
                ui.space()
                ui.badge("hot-reload").props("color=positive")
            ui.separator().classes("q-mb-sm")

            ui.label("Rate Limit").classes("text-caption text-grey-6 q-mb-xs")
            inp_rate_limit = ui.input("Rate Limit", value=cur.get("RATE_LIMIT", "")).classes("full-width")
            ui.label("Formato: N/second|minute|hour").classes("text-caption text-grey-6 q-mb-sm")
            inp_auth_token = (
                ui.input("Auth Token API", value="", placeholder="lascia vuoto per mantenere")
                .classes("full-width")
                .props("type=password")
            )

        with ui.card().classes("q-pa-md full-width"):
            with ui.row().classes("items-center q-mb-xs"):
                ui.label("Metriche").classes("text-caption text-grey-6 text-uppercase")
                ui.space()
                ui.badge("hot-reload").props("color=positive")
            ui.separator().classes("q-mb-sm")

            inp_retention = (
                ui.input(
                    "Retention metriche (giorni)",
                    value=cur.get("METRICS_RETENTION_DAYS", "30"),
                )
                .classes("full-width")
                .props(
                    'hint="Giorni di storico richieste mantenuti in data/metrics.db prima della pulizia automatica."'
                )
            )

        async def save() -> None:
            rate_limit = inp_rate_limit.value.strip()
            if rate_limit and not _RATE_LIMIT_RE.match(rate_limit):
                ui.notify("Rate limit non valido (formato atteso: 20/minute)", type="negative", position="top")
                return
            try:
                config.update_many(
                    {
                        "LLM_BASE_URL": inp_base_url.value,
                        "LLM_API_KEY": inp_api_key.value,
                        "LLM_MODEL": inp_model.value,
                        "LLM_TEMPERATURE": inp_temp.value,
                        "LLM_TIMEOUT": inp_llm_timeout.value,
                        "LLM_MAX_PROMPT_CHARS": inp_max_prompt.value,
                        "SCRAPE_TIMEOUT": inp_scrape_timeout.value,
                        "DEBUG": "true" if debug_switch.value else "false",
                        "REFRESH_ENABLED": "true" if refresh_switch.value else "false",
                        "REFRESH_INTERVAL": inp_refresh.value,
                        "TZ": inp_timezone.value,
                        "RATE_LIMIT": rate_limit,
                        "API_AUTH_TOKEN": inp_auth_token.value,
                        "METRICS_RETENTION_DAYS": inp_retention.value,
                    }
                )
            except OSError as e:
                ui.notify(f"Errore scrittura .env: {e}", type="negative", position="top")
                return
            inp_api_key.set_value("")
            inp_auth_token.set_value("")
            ui.notify("Configurazione salvata", type="positive", position="top")

        ui.button("Salva", icon="save", on_click=save).props("color=primary").classes("q-mt-sm")

    _footer()
