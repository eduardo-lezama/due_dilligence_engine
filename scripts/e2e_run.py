"""
Verificación de punta a punta de H1 (T15 Step 5): corre el grafo completo sin CLI interactiva.

Reanuda checkpoint 1 sin peers ni respuestas, y checkpoint 2 con los defaults que propone el
propio nodo (payload del interrupt), hasta el archiver. Imprime lo que T15 manda comprobar.
Uso: PYTHONUTF8=1 uv run python scripts/e2e_run.py BBW
"""
import sqlite3
import sys

from langgraph.types import Command

from due_diligence.agents.archiver import collect_failures, report_filename
from due_diligence.config import settings
from due_diligence.graph.checkpointer import make_checkpointer
from due_diligence.graph.graph import build_graph
from due_diligence.llm.client import get_client


def pending_payload(graph, config):
    st = graph.get_state(config)
    for task in st.tasks:
        for itr in getattr(task, "interrupts", ()) or ():
            return itr.value
    return None


def run(ticker: str) -> None:
    client = get_client()
    client.reset_run_stats()
    graph = build_graph(checkpointer=make_checkpointer())
    config = {"configurable": {"thread_id": f"e2e-{ticker}"}}
    graph.invoke({"ticker": ticker}, config=config)
    graph.invoke(Command(resume={"proceed": True, "peers": "", "answers": {}}), config=config)
    p2 = pending_payload(graph, config) or {}
    defaults = dict(p2.get("defaults") or {})
    print("CP2 context:", {k: v for k, v in (p2.get("context") or {}).items()})
    print("CP2 defaults:", defaults)
    assumptions = {k: v for k, v in defaults.items() if isinstance(v, (int, float, bool))}
    graph.invoke(Command(resume=assumptions or {"wacc_pct": 9.0}), config=config)
    state = graph.get_state(config).values

    syn, val, fin = state.get("synthesis"), state.get("valuation"), state.get("financials")
    print("\n== ", ticker)
    print("failures:", collect_failures(state))
    print("verdict:", getattr(syn, "verdict", None), "conf:", getattr(syn, "verdict_confidence", None),
          "sizing:", getattr(syn, "suggested_sizing_pct", None), "model_used:", getattr(syn, "model_used", None))
    if val is not None:
        b = val.equity_bridge
        print("valuation error:", val.error, "| basis:", val.valuation_basis)
        print("DCF/sh:", val.dcf_intrinsic_per_share and val.dcf_intrinsic_per_share.value,
              "upside:", val.dcf_upside_pct, "| EPV/sh:", val.epv_per_share and val.epv_per_share.value,
              "| reverse g:", val.reverse_dcf_implied_growth, "| TV share:", val.terminal_value_share)
        if b is not None:
            print("bridge: EV", round(b.enterprise_value / 1e6), "M net_claims", round(b.net_claims / 1e6),
                  "M cash", round(b.cash / 1e6), "STI", round(b.short_term_investments / 1e6), "missing", b.components_missing)
        print("wacc used:", val.assumptions and val.assumptions.wacc_pct, "| suggested:", val.suggested_wacc_pct,
              "|", val.suggested_wacc_breakdown)
        print("note:", (val.dcf_reliability_note or "")[:600])
    for key in ("mgmt", "filings", "competitive"):
        rep = state.get(key)
        cits = getattr(rep, "citations", []) or []
        print(f"{key}: stub={getattr(rep, 'stub', None)} err={getattr(rep, 'error', None)} "
              f"citations verified={sum(1 for c in cits if c.verified)}/{len(cits)}")
    print("quarterly:", state.get("quarterly_snapshot"))
    print("data_quality_flags:", getattr(fin, "data_quality_flags", None))
    print("run stats:", client.get_run_stats())
    from datetime import date
    print("report file:", report_filename(date.today().isoformat(), ticker, syn))
    con = sqlite3.connect(settings.db_path) if hasattr(settings, "db_path") else sqlite3.connect("data/analyses.db")
    row = con.execute("select ticker, verdict, status, models_used, n_verified_citations, methodology_version "
                      "from analyses order by id desc limit 1").fetchone()
    print("sqlite last row:", row)


if __name__ == "__main__":
    for t in sys.argv[1:]:
        run(t.upper())
