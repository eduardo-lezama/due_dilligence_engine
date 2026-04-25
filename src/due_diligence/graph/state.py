"""
AnalysisState — la "pizarra" compartida de todo el grafo LangGraph.

Qué hace este módulo:
    Define el TypedDict que LangGraph usa como estado. Cada nodo del grafo
    recibe el estado completo, hace su trabajo, y devuelve SOLO las claves
    que modificó (dict parcial). LangGraph hace el merge automáticamente.

Por qué TypedDict (no Pydantic):
    LangGraph requiere que el estado sea serializable a/desde el checkpointer
    de SQLite. TypedDict es un contrato de tipos en tiempo de análisis estático
    (mypy/pyright), no en runtime — lo que encaja perfectamente con LangGraph's
    internal state management.

Por qué NotRequired:
    Al iniciar un análisis, solo tenemos `ticker`. Los demás campos se van
    añadiendo a medida que los nodos corren. NotRequired indica "este campo
    puede no estar en el dict todavía". Sin NotRequired, Python (y mypy)
    esperarían que todos los campos estuvieran presentes desde el inicio.

Cómo encaja en la arquitectura:
    START → supervisor_node (escribe cik, as_of_date, run_id)
          → financials_node  (escribe financials)
          → [Phase 1b: más nodos en paralelo via Send()]
          → [Phase 1c: valuation, synthesis, critique]
          → END
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from due_diligence.schemas.base import Citation, Claim, FinancialsReport
from due_diligence.schemas.reports import (
    CriticReport,
    CompetitiveReport,
    FilingsRAGReport,
    ManagementReport,
    MarketReport,
    ValuationReport,
    ValueExpertReport,
)


class AnalysisState(TypedDict):
    """
    Estado compartido del grafo de análisis.

    Solo `ticker` es obligatorio en la invocación inicial.
    Todos los demás campos son NotRequired — se van poblando
    conforme los nodos del grafo se ejecutan.
    """

    # --- Obligatorio al invocar el grafo ---
    ticker: str                          # Ej: "AAPL", "MSFT"

    # --- Lo escribe el Supervisor (primer nodo) ---
    cik: NotRequired[str]                # Central Index Key de la SEC, ej: "0000320193"
    as_of_date: NotRequired[str]         # ISO date "YYYY-MM-DD" — string para serializar a SQLite
    run_id: NotRequired[str]             # UUID del análisis — thread ID del checkpointer

    # --- Especialistas Phase 1a (financials) y Phase 1b (el resto) ---
    financials: NotRequired[FinancialsReport]     # Datos XBRL de EDGAR (determinista)
    mgmt: NotRequired[ManagementReport]           # Insider ownership, Form 4, etc.
    filings: NotRequired[FilingsRAGReport]        # 10-K MDA, riesgos, business description
    market: NotRequired[MarketReport]             # Precio actual, 52w, market cap (Polygon)
    competitive: NotRequired[CompetitiveReport]   # Moat analysis, peer ROIC comparison

    # --- Peer comparison Phase 2 (ADR-015) ---
    # peers: tickers aportados por el usuario en Checkpoint 1 (puede ser [] si skip)
    # peer_financials: FinancialsReport de cada peer, fetched por peers_node
    peers: NotRequired[list[str]]
    peer_financials: NotRequired[list[FinancialsReport]]
    peers_attempted_no_roic: NotRequired[list[str]]  # peers fetched pero sin ROIC calculable

    # --- Historical context Phase 2 (ADR-019) ---
    # CAGR de revenue calculado por financials_node desde XBRL.
    # historical_cagr_years indica el span real (puede ser 3 si solo hay 4 periodos).
    # Viaja al Value Expert para anclar §6 Valuation vs implied growth del reverse DCF.
    historical_cagr_5y: NotRequired[float]    # el valor del CAGR (nombre legacy, no siempre 5y)
    historical_cagr_years: NotRequired[int]   # span real en años (1-5)

    # --- HITL: inputs del usuario en los checkpoints (Phase 1b) ---
    # dict en lugar de Pydantic para evitar dependency circular en state.py;
    # el nodo que los consume hace CheckpointInput(**state["checkpoint1_user_input"])
    checkpoint1_user_input: NotRequired[dict]
    checkpoint2_user_input: NotRequired[dict]

    # --- Agentes finales (Phase 1c) ---
    valuation: NotRequired[ValuationReport]       # DCF, reverse DCF, múltiplos
    synthesis: NotRequired[ValueExpertReport]     # Value Expert: análisis integrado + VERDICT
    critique: NotRequired[CriticReport]           # Critic: contra-tesis, pre-mortem

    # --- Metadata transversal ---
    citations: NotRequired[list[Citation]]        # Citas validadas de todos los agentes LLM
    unverified_claims: NotRequired[list[Claim]]   # Claims que no pasaron substring-match
    cost_usd: NotRequired[float]                  # Coste acumulado de llamadas LLM
