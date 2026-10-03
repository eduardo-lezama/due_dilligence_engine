"""
Financials specialist node — agente tool-lane del grafo LangGraph.

Qué hace:
    Lee ticker del estado, llama a EdgarAdapter para obtener datos XBRL
    reales de SEC EDGAR, y escribe un FinancialsReport validado en el estado.

Tool-lane (no LLM):
    Los datos financieros son 100% deterministas — vienen del filing XBRL
    de la SEC. El LLM nunca toca estos números (ADR-005: zero hallucination
    numérica). Este nodo es puramente fetch + parse.

Nota sobre el import a nivel de módulo:
    EdgarAdapter se importa en el top del archivo (no lazy) para que
    pytest-mock pueda interceptarlo con mocker.patch().
"""

from __future__ import annotations

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.tools.edgar import EdgarAdapter
from due_diligence.utils.periods import cagr_by_fiscal_years

log = structlog.get_logger()


def _compute_revenue_cagr_5y(revenue_series: list) -> tuple[float, int] | None:
    """
    CAGR de revenue en una ventana de hasta 5 AÑOS FISCALES reales (EC-83).

    Antes: índice 0 vs índice min(len-1, 4), asumiendo índice i = i años atrás. Con
    huecos (BBW sin FY2025) el exponente era erróneo; y con periods=4 el "5y" era de 3.
    Ahora la distancia es la diferencia de años fiscales y la ventana se acota a 5
    (con periods=10 la serie tiene hasta 10 años: sin tope sería un CAGR a 9 años).

    Retorna (cagr, n_years) o None si hay < 2 puntos o el inicio/fin es <= 0.
    El n_years real importa: el Expert debe saber si cita un CAGR de 3 o de 5 años.
    """
    return cagr_by_fiscal_years(revenue_series, max_years=5)


def financials_node(state: AnalysisState) -> dict:
    """
    Obtiene datos financieros XBRL de EDGAR y devuelve un FinancialsReport.

    Args:
        state: Estado del grafo — usa state["ticker"].

    Returns:
        {"financials": FinancialsReport} — solo la clave que este nodo produce.
        LangGraph hace el merge con el resto del estado.
    """
    ticker = state["ticker"]
    log.info("financials_node_start", ticker=ticker)

    # fetch_raw: llamada de red (o mock en tests)
    # parse_financials: función pura — transforma dict → FinancialsReport
    raw = EdgarAdapter.fetch_raw(ticker)
    report = EdgarAdapter.parse_financials(raw)

    # Calcular CAGR histórico de revenue para anclaje de §6 Valuation (ADR-019).
    # Retorna (cagr, n_years) para que el Expert sepa si es un CAGR de 3 o 5 años.
    cagr_result = _compute_revenue_cagr_5y(report.revenue)
    if cagr_result is not None:
        cagr_5y, cagr_years = cagr_result
        log.info("financials_node_cagr", ticker=ticker, cagr=f"{cagr_5y:.1%}", years=cagr_years)
    else:
        cagr_5y, cagr_years = None, None
        log.info("financials_node_cagr_unavailable", ticker=ticker, revenue_periods=len(report.revenue))

    log.info("financials_node_complete", ticker=ticker, revenue_periods=len(report.revenue))

    out: dict = {"financials": report}
    if cagr_5y is not None:
        out["historical_cagr_5y"] = cagr_5y
        out["historical_cagr_years"] = cagr_years

    # Quarterly snapshot (EC-44/MCE-24): fetch 10-Q XBRL si el 10-K está stale.
    # Solo 3 métricas clave (revenue, EBIT, gross profit) del quarter más reciente.
    try:
        q_snapshot = EdgarAdapter.fetch_quarterly_snapshot(
            ticker, tenk_as_of=report.as_of_date
        )
        if q_snapshot is not None:
            out["quarterly_snapshot"] = q_snapshot
    except Exception as e:
        log.warning("quarterly_snapshot_failed", ticker=ticker, error=str(e))

    return out
