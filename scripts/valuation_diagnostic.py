"""
valuation_diagnostic — calibración de la valoración sobre un universo amplio, sin LLM (post-H2-2).

Por qué existe: el veredicto se apoya en la valoración determinista. Este script ejecuta solo los nodos
deterministas (supervisor → financials → precio Polygon → valuation, con los defaults del checkpoint 2)
para un universo de empresas conocidas y guarda, por empresa, valor/precio, DCF/EPV, crecimiento
implícito, rentabilidad implícita frente al listón y los supuestos que lo explican. Coste LLM: 0.

Pruebas metamórficas (bloque B, ADR-044; criterios 4 y 5 de docs/research/2026-10-11-valuacion-por-
perfiles.md): con el precio un 40 % más bajo, ¿cuántas empresas de calidad quedarían en o por debajo del
precio de entrada? (si casi ninguna: el sistema es incapaz de ver oportunidades). Con el precio un 40 %
más alto, ¿alguna? (si sí: es laxo). El precio de entrada no depende del precio salvo por los pesos del
WACC, que se mantienen (aproximación). El objetivo NO es una banda de valor/precio: eso calibraría el
modelo contra el propio precio (revisión 2026-10-17, S2).

Uso (red: EDGAR + Polygon; Polygon gratis = 5 llamadas/min, por eso va pausado):
    PYTHONUTF8=1 uv run python scripts/valuation_diagnostic.py [TICKER ...]
Salida: data/evals/valuation_diag/<fecha>.json (universo completo) o <fecha>-custom.json (tickers sueltos).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

from due_diligence.agents.checkpoint import _build_checkpoint2_summary
from due_diligence.agents.financials import financials_node
from due_diligence.agents.market import fetch_risk_free_rate
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
QUALITY = UNIVERSE[:15]   # calidad + defensivas: el grupo de las pruebas metamórficas


_RF = None   # tipo sin riesgo: una sola lectura de FRED por ejecución (main lo rellena)


def _market(ticker: str) -> MarketReport:
    data = PolygonAdapter.fetch_market_data(ticker)
    as_of = data.get("as_of_date") or "unknown"

    def mv(v):
        return MetricValue(value=v, unit="USD", period=as_of, source="Polygon") if v is not None else None
    # A5 (ADR-043): mismo tipo sin riesgo vivo que market_agent; sin él el diagnóstico usaría el estático.
    return MarketReport(ticker=ticker, stub=False, price_current=mv(data.get("price_current")),
                        market_cap=mv(data.get("market_cap")), risk_free_rate=_RF)


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
        # A3 (ADR-043): rango del DCF con supuestos de la propia historia.
        **{f"{sc.name}_to_price": (sc.per_share / price if sc.per_share and price else None)
           for sc in v.dcf_scenarios},
        # Bloque B (ADR-044): rentabilidad implícita frente al listón y precio de entrada.
        "hurdle": v.hurdle_rate, "implied_return": v.implied_return, "implied_return_note": v.implied_return_note,
        **{f"{sc.name}_return": sc.implied_return for sc in v.dcf_scenarios},
        "entry_price": v.entry_price, "entry_basis": v.entry_basis,
        "entry_to_price": v.entry_price / price if v.entry_price and price else None,
        "structural_break": v.growth_structural_break is not None,
        "cyclicality_assessed": getattr(cyc, "assessed", None), "loss_making": getattr(cyc, "loss_making", None),
    }


def metamorphic_summary(rows: list[dict]) -> dict:
    """Pruebas con el precio ±40 %: elegibles = precio × factor ≤ precio de entrada."""
    def eligible(r: dict, factor: float) -> bool:
        return bool(r.get("entry_price") and r.get("price") and r["price"] * factor <= r["entry_price"])
    valid = [r for r in rows if not r.get("error") and r.get("price")]
    quality = [r for r in valid if r["ticker"] in QUALITY]
    return {
        "quality_n": len(quality),
        "quality_eligible_now": sum(eligible(r, 1.0) for r in quality),
        "quality_eligible_price_down_40": sum(eligible(r, 0.6) for r in quality),
        "all_eligible_now": sum(eligible(r, 1.0) for r in valid),
        "all_eligible_price_up_40": sum(eligible(r, 1.4) for r in valid),
        "all_n": len(valid),
    }


def main(argv: list[str]) -> int:
    global _RF
    tickers = [t.upper() for t in argv] or UNIVERSE
    _RF = fetch_risk_free_rate()
    print(f"risk-free rate: {_RF.value}% ({_RF.source} {_RF.period})" if _RF else
          "risk-free rate: FRED unavailable → static default", flush=True)
    rows = []
    for i, t in enumerate(tickers):
        if i:
            time.sleep(POLYGON_PAUSE_S)
        try:
            rows.append(diagnose(t))
            r = rows[-1]
            ir = r["implied_return"]
            print(f"{t:6} price={r['price']} dcf/p={r['dcf_to_price'] and round(r['dcf_to_price'], 2)} "
                  f"epv/p={r['epv_to_price'] and round(r['epv_to_price'], 2)} wacc={r['wacc_pct']} "
                  f"g={r['near_term_growth_pct']} ({r['growth_source']}) "
                  f"return={f'{ir:.1%}' if ir is not None else r['implied_return_note']} "
                  f"entry/p={r['entry_to_price'] and round(r['entry_to_price'], 2)}", flush=True)
        except Exception as e:      # datos externos: anotar y seguir
            rows.append({"ticker": t, "error": f"{type(e).__name__}: {e}"})
            print(f"{t:6} FAILED {type(e).__name__}: {e}", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Una ejecución con tickers sueltos no pisa la del universo completo del mismo día.
    out = OUT_DIR / f"{date.today().isoformat()}{'' if tickers == UNIVERSE else '-custom'}.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"metamorphic (price ±40%): {metamorphic_summary(rows)}")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
