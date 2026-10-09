"""
Script one-shot para fetchear y cachear fixtures XBRL de los arquetipos de test.

Ejecutar UNA VEZ con conexión a internet y EDGAR credentials:
    uv run python scripts/fetch_archetype_fixtures.py

Output: tests/fixtures/{ticker}_xbrl_raw.json para cada arquetipo.
AAPL ya existe — se puede sobreescribir para mantenerlo fresco pasando --all.
Los fixtures resultantes son estáticos — no requieren red en los tests normales.

Arquetipos:
    BBW  — small cap con income statement simplificado (sin OperatingIncomeLoss en XBRL)
    AZO  — buyback-heavy con equity negativo (IC fallback a assets-cash)
    PRDO — asset-light services con capex ~0 (FCF = OCF sin capex significativo)
    CALM — cíclica de materias primas (márgenes con picos de 30+ pp por la gripe aviar);
           cierre fiscal 52/53 semanas a finales de mayo
"""

import json
import sys
from pathlib import Path

# Añadir src/ al path para importar due_diligence sin instalar el paquete
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from due_diligence.tools.edgar import EdgarAdapter  # noqa: E402

ARCHETYPES = [
    ("AAPL", "large_cap_baseline"),  # H1 T14: AAPL se regenera también (antes se guardaba aparte)
    ("BBW",  "small_cap_simplified_income_statement"),
    ("AZO",  "buyback_heavy_negative_equity"),
    ("PRDO", "asset_light_zero_capex"),
    ("CALM", "commodity_cyclical"),
]

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


def fetch_and_save(ticker: str, label: str) -> None:
    out_path = FIXTURES_DIR / f"{ticker.lower()}_xbrl_raw.json"
    print(f"Fetching {ticker} ({label})...", end=" ", flush=True)
    raw = EdgarAdapter.fetch_raw(ticker)
    out_path.write_text(json.dumps(raw, indent=2))
    print(f"OK -> {out_path.name}")


if __name__ == "__main__":
    fetch_all = "--all" in sys.argv
    targets = ARCHETYPES

    for ticker, label in targets:
        out_path = FIXTURES_DIR / f"{ticker.lower()}_xbrl_raw.json"
        if out_path.exists() and not fetch_all:
            print(f"Skipping {ticker} — fixture already exists (use --all to overwrite)")
            continue
        fetch_and_save(ticker, label)

    print("\nDone. Commit the fixtures:")
    print("  git add tests/fixtures/aapl_xbrl_raw.json tests/fixtures/bbw_xbrl_raw.json tests/fixtures/azo_xbrl_raw.json tests/fixtures/prdo_xbrl_raw.json tests/fixtures/calm_xbrl_raw.json"
)
