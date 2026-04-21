"""
Peers Node — fetcha FinancialsReport para cada peer ticker aportado por el usuario.

Qué hace este módulo:
    Implementa peers_node, un nodo tool-lane (sin LLM) que recibe
    state["peers"] (lista de tickers que el usuario proporcionó en Checkpoint 1)
    y devuelve peer_financials: list[FinancialsReport].

    El competitive_agent consume esta lista para poblar
    CompetitiveReport.peer_roic_comparison (tabla ROIC de peers en el reporte).

Por qué es un nodo separado y no inline en competitive_agent:
    Separar el I/O (fetch EDGAR) del razonamiento (LLM moat analysis) es el
    patrón establecido en esta arquitectura. competitive_agent es LLM-lane —
    no debe hacer I/O de red. peers_node es tool-lane puro, igual que financials_node.

Degraded mode:
    - Lista vacía o ausente → devuelve [] sin llamar a EDGAR.
    - Un peer con fallo (ticker inválido, XBRL corrupto) → se loguea y se omite.
    - Nunca bloquea el pipeline completo. Los peers son contexto opcional.

Referencia ADR: ADR-015.
"""

from __future__ import annotations

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.tools.edgar import DataUnavailableError, EdgarAdapter

log = structlog.get_logger()


def peers_node(state: AnalysisState) -> dict:
    """
    Itera sobre state["peers"] y obtiene FinancialsReport para cada uno.

    Args:
        state: Estado del grafo — usa state.get("peers", []).

    Returns:
        {"peer_financials": list[FinancialsReport]} — solo la clave que produce.
        Lista vacía si no hay peers o todos fallaron.
    """
    peers: list[str] = state.get("peers", []) or []  # type: ignore[typeddict-item]

    if not peers:
        log.info("peers_node_skipped", reason="no peers provided")
        return {"peer_financials": []}

    reports = []
    for peer_ticker in peers:
        try:
            raw = EdgarAdapter.fetch_raw(peer_ticker)
            rep = EdgarAdapter.parse_financials(raw)
            reports.append(rep)
            log.info("peers_node_fetched", peer=peer_ticker)
        except DataUnavailableError as e:
            log.warning("peers_node_peer_unavailable", peer=peer_ticker, error=str(e))
        except Exception as e:
            log.warning("peers_node_peer_failed", peer=peer_ticker, error=str(e))

    log.info("peers_node_complete", total_peers=len(peers), fetched=len(reports))
    return {"peer_financials": reports}
