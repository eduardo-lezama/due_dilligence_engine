"""
Script one-shot: cachea las tablas de participaciones (beneficial ownership) de los DEF 14A
de los arquetipos como fixtures HTML para tests sin red (EC-93).

Por qué solo las tablas y no el proxy entero: un DEF 14A pesa 0,5-1 MB; la lógica a probar
(localizar la fila "as a group", leer acciones y %) solo necesita las <table> candidatas.
Se guardan TODAS las tablas que mencionan "as a group" o "beneficial", incluidas las de notas
al pie: son los señuelos contra los que el parser tiene que acertar.

Ejecutar con red y SEC_USER_AGENT configurado:
    PYTHONUTF8=1 uv run python scripts/fetch_def14a_fixtures.py
Output: tests/fixtures/{ticker}_def14a_ownership.html
"""
import sys
from pathlib import Path

import lxml.html

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from due_diligence.tools.edgar import EdgarManagementAdapter  # noqa: E402

TICKERS = ["CALM", "AAPL", "AZO", "BBW", "PRDO", "NUE", "PATH", "GOOGL", "BRK-B",
           "NKE", "NWSA", "HEI", "UHAL", "SPG", "F", "BF-B", "ACN"]  # multiclase / multi-columna / tabla de 1 fila
FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


def candidate_tables_html(html: str) -> str:
    """Concatena el HTML de las tablas que pueden contener la tabla de participaciones."""
    tree = lxml.html.fromstring(html.encode("utf-8"), parser=lxml.html.HTMLParser(encoding="utf-8"))
    kept = []
    for table in tree.iter("table"):
        text = " ".join(table.text_content().split()).lower()
        if "as a group" in text or "beneficial" in text:
            kept.append(lxml.html.tostring(table, encoding="unicode"))
    return "<html><body>\n" + "\n".join(kept) + "\n</body></html>\n"


if __name__ == "__main__":
    refetch = "--all" in sys.argv
    for ticker in TICKERS:
        out = FIXTURES_DIR / f"{ticker.lower()}_def14a_ownership.html"
        if out.exists() and not refetch:
            print(f"{ticker}: exists (use --all to refetch)")
            continue
        try:
            html, _filing_date = EdgarManagementAdapter.fetch_def14a_html(ticker)
        except Exception as e:  # timeouts de EDGAR: seguir con el resto y reintentar después
            print(f"{ticker}: FAILED {type(e).__name__}: {e}")
            continue
        if not html:
            print(f"{ticker}: no DEF 14A HTML")
            continue
        out.write_text(candidate_tables_html(html), encoding="utf-8")
        print(f"{ticker}: {out.name} ({out.stat().st_size // 1024} KB)")
