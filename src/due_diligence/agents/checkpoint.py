"""
Nodos de checkpoint HITL (Human-In-The-Loop) para el grafo LangGraph.

Qué hace este módulo:
    Define los dos nodos que pausan la ejecución del grafo para input humano.
    Cuando LangGraph ejecuta interrupt(), serializa el estado completo al
    checkpointer (SQLite en producción) y devuelve el control al llamador.
    El grafo se reanuda cuando se invoca graph.invoke(Command(resume=...)).

Por qué interrupt() y no un edge condicional:
    interrupt() es el mecanismo oficial de LangGraph para HITL. A diferencia
    de un edge condicional (que toma decisiones basadas en estado existente),
    interrupt() pausa y espera nueva información del usuario — como aprobación,
    correcciones, o flags de seguimiento.

Cuándo se ejecuta checkpoint1:
    Después de que los 4 especialistas (management, filings, market, competitive)
    completan su análisis en paralelo. El usuario puede revisar los resultados
    y decidir si continúa.

Cuándo se ejecuta checkpoint2:
    Entre checkpoint1 y valuation_agent. Pide al usuario los assumptions del
    DCF: WACC, growth rate, años de proyección. El valuation_agent los lee de
    state["checkpoint2_user_input"]. Si el usuario reanuda sin input ({} o None),
    el valuation_agent usa defaults razonables (WACC=9%, growth=2.5%, 10 años).
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt

from due_diligence.graph.state import AnalysisState

log = structlog.get_logger()


def _build_socratic_questions(state: AnalysisState) -> list[str]:
    """
    Genera 1-3 preguntas Socratic ancladas en los datos del state (ADR-018).

    Cada pregunta surge de un patrón detectado en los outputs de los especialistas.
    Si un especialista no está disponible, su patrón se salta — nunca se generan
    preguntas sin datos reales que las anclen.

    La pregunta de peers (ADR-015) siempre se incluye como última pregunta.

    Por qué heurístico y no LLM:
        Las preguntas son patrones fijos sobre señales conocidas (moat vs ROIC,
        ownership, tenure). Un LLM añadiría coste y no-determinismo a un paso
        que es de estructuración, no de razonamiento. Si los patrones resultan
        limitantes, se pueden añadir triggers en futuras fases.
    """
    qs: list[str] = []
    competitive = state.get("competitive")  # type: ignore[typeddict-item]
    financials = state.get("financials")    # type: ignore[typeddict-item]
    mgmt = state.get("mgmt")               # type: ignore[typeddict-item]

    # --- Patrón 1: Moat vs ROIC mismatch ---
    if (
        competitive is not None
        and not competitive.stub
        and competitive.moat_classification
        and financials is not None
        and financials.roic_series
    ):
        roic = financials.roic_series[0].value
        moat = competitive.moat_classification
        if moat == "narrow" and roic > 0.20:
            qs.append(
                f"Moat classified '{moat}' but ROIC is {roic:.0%}. "
                "What specific signal would reconcile this, or indicate moat erosion in the future?"
            )
        elif moat == "none" and roic > 0.15:
            qs.append(
                f"No moat identified yet ROIC is {roic:.0%}. "
                "Is this cyclical/temporary, or is there a structural advantage the qualitative review missed?"
            )

    # --- Patrón 2: Insider ownership bajo ---
    if mgmt is not None and not mgmt.stub and mgmt.insider_ownership_pct is not None:
        if mgmt.insider_ownership_pct < 1.0:
            qs.append(
                f"Insider ownership is {mgmt.insider_ownership_pct:.2f}%. "
                "Would you weigh this as misaligned incentives or normal for a company of this size?"
            )

    # --- Patrón 3: CEO tenure largo ---
    if mgmt is not None and not mgmt.stub and mgmt.ceo_tenure_years is not None:
        if mgmt.ceo_tenure_years > 12:
            qs.append(
                f"CEO has been in role for {mgmt.ceo_tenure_years:.0f} years. "
                "Do you see this as a continuity asset or a succession risk?"
            )

    # --- Peer prompt — siempre presente (ADR-015) ---
    qs.append(
        "Which 3-5 peer tickers would you benchmark this company against? "
        "(press Enter to skip — moat analysis will use 10-K data only)"
    )

    return qs


def checkpoint1_node(state: AnalysisState) -> dict:
    """
    Checkpoint HITL entre Phase 1b (especialistas) y Phase 1c (valoración).

    Pausa el grafo y presenta al usuario un resumen de los resultados de los
    4 especialistas. El usuario puede: continuar, cancelar, o añadir notas
    que se pasan al valuation_agent en Phase 1c.

    El valor devuelto por interrupt() es lo que el usuario proporciona al
    reanudar con Command(resume=<value>). Si el usuario no proporciona nada
    (reanuda con None), se usa {} como default.

    Returns:
        dict con checkpoint1_user_input — se guarda en el estado para que
        los nodos de Phase 1c puedan leer instrucciones adicionales del usuario.
    """
    ticker = state["ticker"]
    log.info("checkpoint1_interrupt", ticker=ticker)

    # Construir el resumen que se presenta al usuario en el checkpoint.
    # Solo incluimos los campos que ya existen en el estado (NotRequired).
    # Generar preguntas Socratic ancladas en los datos actuales (ADR-018).
    # El CLI las renderiza para que el usuario responda antes de continuar.
    socratic_questions = _build_socratic_questions(state)

    summary: dict = {
        "phase": "checkpoint1",
        "ticker": ticker,
        "message": (
            f"Phase 1b complete for {ticker}. "
            "Review specialist results and confirm to proceed to valuation (Phase 1c)."
        ),
        "available_reports": [
            field for field in ("mgmt", "filings", "market", "competitive")
            if state.get(field) is not None  # type: ignore[literal-required]
        ],
        # ADR-018: preguntas Socratic para el CLI
        "socratic_questions": socratic_questions,
    }

    # interrupt() serializa el estado y suspende el grafo.
    # Retorna cuando el usuario llama graph.invoke(Command(resume=<value>)).
    user_input = interrupt(summary)

    # Extraer peers del input del usuario. El CLI pasa {"proceed": True, "peers": "MSFT,GOOGL"}.
    # Convertir string CSV a lista de tickers normalizados. Input vacío → [].
    peers_raw = (user_input or {}).get("peers", "") if isinstance(user_input, dict) else ""
    peers = [p.strip().upper() for p in peers_raw.split(",") if p.strip()] if isinstance(peers_raw, str) else []

    return {
        "checkpoint1_user_input": user_input if user_input else {"proceed": True},
        "peers": peers,
    }


def checkpoint2_node(state: AnalysisState) -> dict:
    """
    Checkpoint HITL entre los especialistas y el valuation_agent.

    Muestra el contexto disponible (FCF, precio, market cap) y pide al usuario
    los assumptions del DCF: WACC, near-term growth, años de proyección.

    El usuario reanuda con un dict como:
        {"wacc_pct": 9.0, "terminal_growth_pct": 2.5, "projection_years": 10,
         "revenue_growth_yr1_pct": 12.0}

    Si reanuda con {} o None, el valuation_agent usa defaults internos.

    Returns:
        dict con checkpoint2_user_input — leído por valuation_agent para DCF.
    """
    ticker = state["ticker"]
    log.info("checkpoint2_interrupt", ticker=ticker)

    # Extraer hints de contexto para mostrar al usuario antes del DCF.
    # Solo mostramos los campos disponibles — el agente de mercado puede estar degradado.
    financials = state.get("financials")  # type: ignore[typeddict-item]
    market = state.get("market")         # type: ignore[typeddict-item]

    fcf_hint = None
    if financials is not None and financials.free_cash_flow:
        fcf = financials.free_cash_flow[0]
        fcf_hint = f"${fcf.value / 1e9:.1f}B ({fcf.period})"

    price_hint = None
    if market is not None and market.price_current is not None:
        price_hint = f"${market.price_current.value:.2f}"

    market_cap_hint = None
    if market is not None and market.market_cap is not None:
        market_cap_hint = f"${market.market_cap.value / 1e9:.0f}B"

    roic_hint = None
    if financials is not None and financials.roic_series:
        roic_hint = f"{financials.roic_series[0].value:.1%} ({financials.roic_series[0].period})"

    # CAGR histórico como anchor para las asunciones de growth (ADR-018 + ADR-019)
    cagr_5y = state.get("historical_cagr_5y")  # type: ignore[typeddict-item]

    summary: dict = {
        "phase": "checkpoint2",
        "ticker": ticker,
        "message": (
            f"Ready to run valuation for {ticker}. "
            "Provide DCF assumptions or resume with {{}} to use defaults."
        ),
        "context": {
            "latest_fcf": fcf_hint,
            "current_price": price_hint,
            "market_cap": market_cap_hint,
            "latest_roic": roic_hint,
            # Anchor histórico: el usuario puede comparar su growth propuesto vs historia
            "historical_revenue_cagr_5y": f"{cagr_5y:.1%}" if cagr_5y is not None else "unavailable",
        },
        "defaults": {
            "wacc_pct": 9.0,
            "terminal_growth_pct": 2.5,
            "projection_years": 10,
            "revenue_growth_yr1_pct": "auto (historical FCF CAGR or 7%)",
        },
    }

    user_input = interrupt(summary)

    return {"checkpoint2_user_input": user_input if user_input is not None else {}}
