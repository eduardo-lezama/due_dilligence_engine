"""
valuation_diagnostic — calibración de la valoración sobre un universo amplio, sin LLM (post-H2-2).

Por qué existe: todo el veredicto H2 (margen de seguridad, precio de entrada) se apoya en el DCF y el EPV,
y en los casos congelados ambos métodos discrepan hasta 3–4× y las empresas de calidad salen "caras" por
mucho. Antes de construir más encima hay que saber si la valoración está bien calibrada. Este script
ejecuta solo los nodos deterministas (supervisor → financials → precio Polygon → valuation, con los
defaults del checkpoint 2) para un universo de empresas conocidas y guarda, por empresa, valor/precio,
DCF/EPV, crecimiento implícito y los supuestos que lo explican. Coste LLM: 0.

Uso (red: EDGAR + Polygon; Polygon gratis = 5 llamadas/min, por eso va pausado):
    PYTHONUTF8=1 uv run python scripts/valuation_diagnostic.py [TICKER ...]
Salida: data/evals/valuation_diag/<fecha>.json (+ tabla en consola).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

from due_diligence.agents.checkpoint import _build_checkpoint2_summary
from due_diligence.agents.financials import financials_node
from due_diligence.agents.supervisor import supervisor_node
from due_diligence.agents.valuation import valuation_agent
from due_diligence.schemas.base import MetricValue
from due_diligence.schemas.reports import MarketReport
from due_diligence.tools.polygon import PolygonAdapter

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "evals" / "valuation_diag"
POLYGON_PAUSE_S = 40          # 3 llamadas por ticker con un límite de 5/min

# Universo: calidad (compounders), defensivas, industriales/cíclicas, problemáticas y los casos del dataset.
# Se excluyen bancos y aseguradoras: el FCFF no es la métrica adecuada para ellos.
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "V", "MA", "COST", "ORLY", "AZO", "ADP", "MCD",       # calidad
    "KO", "PEP", "PG", "JNJ", "HD",                                               # defensivas
    "CAT", "DE", "NUE", "XOM", "CVX", "MOS",                                      # cíclicas
    "NKE", "TGT", "INTC", "PFE", "VZ",                                            # en dificultades / maduras
    "BBW", "PRDO", "KBR", "QLYS", "CALM", "PATH", "PLUG",                         # dataset de evals
]


def _market(ticker: str) -> MarketReport:
    data = PolygonAdapter.fetch_market_data(ticker)
    as_of = data.get("as_of_date") or "unknown"

    def mv(v):
        return MetricValue(value=v, unit="USD", period=as_of, source="Polygon") if v is not None else None
    return MarketReport(ticker=ticker, stub=False, price_current=mv(data.get("price_current")),
                        market_cap=mv(data.get("market_cap")))


def diagnose(ticker: str) -> dict:
    """Valoración determinista con los defaults del checkpoint 2 (WACC sugerido)."""
    state: dict = {"ticker": ticker}
    state.update(supervisor_node(state))
    state.update(financials_node(state))
    state["market"] = _market(ticker)
    defaults = _build_checkpoint2_summary(state)["defaults"]
    state["checkpoint2_user_input"] = {k: defaults[k] for k in ("wacc_pct", "terminal_growth_pct", "projection_years",
                                                                 "mid_year_convention")}
    state["checkpoint2_user_input"]["valuation_date"] = date.today().isoformat()
    v = valuation_agent(state)["valuation"]
    price = state["market"].price_current.value if state["market"].price_current else None
    dcf = v.dcf_intrinsic_per_share.value if v.dcf_intrinsic_per_share else None
    epv = v.epv_per_share.value if v.epv_per_share else None
    cyc = getattr(v, "cyclicality", None)
    return {
        "ticker": ticker, "price": price, "dcf_ps": dcf, "epv_ps": epv,
        "dcf_to_price": dcf / price if dcf and price else None,
        "epv_to_price": epv / price if epv and price else None,
        "dcf_to_epv": dcf / epv if dcf and epv and epv > 0 else None,
        "wacc_pct": defaults["wacc_pct"], "near_term_growth_pct": v.near_term_growth_used_pct,
        "growth_source": v.near_term_growth_source, "terminal_value_share": v.terminal_value_share,
        "reverse_dcf_growth": v.reverse_dcf_implied_growth, "dcf_base_source": v.dcf_base_source,
        "is_cyclical": getattr(cyc, "is_cyclical", None), "error": v.error,
    }


def main(argv: list[str]) -> int:
    tickers = [t.upper() for t in argv] or UNIVERSE
    rows = []
    for i, t in enumerate(tickers):
        if i:
            time.sleep(POLYGON_PAUSE_S)
        try:
            rows.append(diagnose(t))
            r = rows[-1]
            print(f"{t:6} price={r['price']} dcf/p={r['dcf_to_price'] and round(r['dcf_to_price'], 2)} "
                  f"epv/p={r['epv_to_price'] and round(r['epv_to_price'], 2)} wacc={r['wacc_pct']} "
                  f"g={r['near_term_growth_pct']} ({r['growth_source']})", flush=True)
        except Exception as e:      # datos externos: anotar y seguir
            rows.append({"ticker": t, "error": f"{type(e).__name__}: {e}"})
            print(f"{t:6} FAILED {type(e).__name__}: {e}", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{date.today().isoformat()}.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
