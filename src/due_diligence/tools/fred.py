"""
Adaptador FRED (Federal Reserve Bank of St. Louis): tipo del bono del Tesoro a 10 años (DGS10).

Por qué existe (bloque A, ADR-043): el tipo sin riesgo del CAPM estaba fijado a mano (4,2 %,
"as of 2026-05") y el 2026-10-08 el bono a 10 años cotizaba al 5,22 %. Un tipo desfasado mueve el
WACC punto por punto y con él toda la valoración. FRED es la fuente primaria oficial y su CSV no
necesita clave. Sigue el patrón de tools/: `fetch_raw` hace la red, `parse_latest_rate` es pura.
"""
import urllib.request
from datetime import date, timedelta

import structlog

from due_diligence.tools.xbrl_extract import DataUnavailableError

log = structlog.get_logger()

# cosd = fecha de inicio: solo los últimos días (revisión de código: sin él se descargaba la serie desde
# 1962, ~16.900 filas, para leer una). 30 días cubren festivos y huecos de publicación.
_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
_WINDOW_DAYS = 30
_TIMEOUT_S = 20


class FredAdapter:
    """Lectura de series diarias de FRED en formato CSV (sin clave de API)."""

    @staticmethod
    def fetch_raw(series_id: str = "DGS10") -> dict:
        """Descarga el CSV de los últimos _WINDOW_DAYS días de la serie. Red: los tests lo mockean (conftest)."""
        start = (date.today() - timedelta(days=_WINDOW_DAYS)).isoformat()
        req = urllib.request.Request(_FRED_CSV_URL.format(series_id=series_id, start=start),
                                     headers={"User-Agent": "due-diligence-system"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return {"series_id": series_id, "csv": resp.read().decode("utf-8")}

    @staticmethod
    def parse_latest_rate(raw: dict) -> dict:
        """Última observación numérica del CSV → {"value_pct", "date", "series_id"}.

        FRED marca con "." los días sin dato (festivos); se saltan. Sin ninguna observación
        numérica → DataUnavailableError (fail loud: el llamador decide el fallback visible).
        """
        lines = [ln.strip() for ln in raw["csv"].strip().splitlines()[1:] if ln.strip()]
        for line in reversed(lines):
            day, _, value = line.partition(",")
            try:
                return {"value_pct": float(value), "date": day, "series_id": raw["series_id"]}
            except ValueError:
                continue          # "." = sin dato ese día
        raise DataUnavailableError(f"FRED {raw['series_id']}: no numeric observation in CSV")
