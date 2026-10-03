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
    Después de que los 4 especialistas completan su análisis en paralelo.
    Phase 2 añade preguntas Socratic ancladas en datos (ADR-018) y el prompt
    de peers para ROIC comparison (ADR-015). Tras el checkpoint, peers_node
    fetcha XBRL de los peer tickers antes de pasar a checkpoint2.

Cuándo se ejecuta checkpoint2:
    Entre peers_node y valuation_agent. Muestra el CAGR histórico de revenue
    (ADR-019) como anchor para las asunciones de growth. Pide WACC, growth,
    años de proyección y justificación del divergencia vs historia.
    Si el usuario reanuda sin input, el valuation_agent usa defaults.
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt

from due_diligence.utils.formatting import fmt_usd
from due_diligence.graph.state import AnalysisState

log = structlog.get_logger()

# Única fuente de defaults del checkpoint 2 (revisión adversarial: estaban triplicados en
# checkpoint.py, cli.py y valuation.py). La consumen el nodo, la CLI y valuation_agent.
#   - wacc_pct: SOLO fallback cuando no hay market cap; con market cap el default es el
#     WACC real de suggest_wacc (ADR-033).
#   - mid_year_convention: False en H1 por comparabilidad con los reports históricos (ADR-033 §7).
CHECKPOINT2_DEFAULTS: dict = {
    "wacc_pct": 9.0,
    "terminal_growth_pct": 2.5,
    "projection_years": 10,
    "mid_year_convention": False,
}


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

    # Nota: la pregunta de peer tickers NO se incluye aquí — el CLI tiene un prompt
    # dedicado inmediatamente después de las Socratic questions que hace exactamente eso.
    # Incluirla aquí causaba que el usuario tuviera que responder dos veces lo mismo.

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


def _build_checkpoint2_summary(state: AnalysisState) -> dict:
    """
    Payload puro del interrupt de checkpoint 2 (sin efectos: testeable sin grafo).

    Contiene el contexto que ve el usuario (FCF, precio, market cap, ROIC, CAGR histórico) y,
    desde ADR-033, el **WACC real sugerido** con su desglose (Ke + Kd·(1−t) a pesos de mercado)
    como default del prompt. Antes la CLI mostraba el Ke como "Suggested WACC" y usaba 9,0 fijo.
    """
    # Import diferido: valuation importa CHECKPOINT2_DEFAULTS de este módulo → evitar el ciclo.
    from due_diligence.agents.valuation import suggest_wacc
    from due_diligence.agents.valuation_math import describe_wacc

    ticker = state["ticker"]
    # Solo mostramos los campos disponibles — el agente de mercado puede estar degradado.
    financials = state.get("financials")  # type: ignore[typeddict-item]
    market = state.get("market")         # type: ignore[typeddict-item]

    # EC-86: formatter adaptativo común (fmt_usd). Con /1e9 una small cap salía como "$0.0B"
    # justo donde el usuario decide WACC y crecimiento (regresión del patrón EC-14/EC-17).
    fcf_hint = None
    if financials is not None and financials.free_cash_flow:
        fcf = financials.free_cash_flow[0]
        fcf_hint = f"{fmt_usd(fcf.value)} ({fcf.period})"

    price_hint = None
    if market is not None and market.price_current is not None:
        price_hint = f"${market.price_current.value:.2f}"

    market_cap = market.market_cap.value if (market is not None and market.market_cap is not None) else None
    market_cap_hint = fmt_usd(market_cap) if market_cap is not None else None

    roic_hint = None
    if financials is not None and financials.roic_series:
        roic_hint = f"{financials.roic_series[0].value:.1%} ({financials.roic_series[0].period})"

    # CAGR histórico como anchor para las asunciones de growth (ADR-018 + ADR-019)
    cagr_5y = state.get("historical_cagr_5y")  # type: ignore[typeddict-item]

    # WACC real sugerido (ADR-033). Sin market cap no hay pesos de mercado → fallback explícito.
    w = suggest_wacc(financials, market_cap)
    wacc_default = round(w.wacc * 100, 1) if w is not None else CHECKPOINT2_DEFAULTS["wacc_pct"]
    wacc_breakdown = (
        describe_wacc(w) if w is not None
        else f"unavailable (no market cap) — fallback {CHECKPOINT2_DEFAULTS['wacc_pct']}%"
    )

    return {
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
            "wacc_breakdown": wacc_breakdown,
        },
        "defaults": {
            **CHECKPOINT2_DEFAULTS,
            "wacc_pct": wacc_default,
            "revenue_growth_yr1_pct": "auto (historical FCF CAGR or 7%)",
        },
    }


def _validate_resume(user_input) -> str | None:
    """Valida WACC/g del resume con validate_dcf_rates. Devuelve el mensaje de error o None.

    Solo valida lo que el usuario ha dado: si no da WACC, valuation_agent usará el sugerido
    (que se valida allí); si no da g, se usa el default único de CHECKPOINT2_DEFAULTS.
    """
    from due_diligence.agents.valuation_math import ValuationInputError, validate_dcf_rates

    if not isinstance(user_input, dict) or "wacc_pct" not in user_input:
        return None
    try:
        wacc = float(user_input["wacc_pct"]) / 100
        g = float(user_input.get("terminal_growth_pct", CHECKPOINT2_DEFAULTS["terminal_growth_pct"])) / 100
        validate_dcf_rates(wacc, g)
    except ValuationInputError as e:
        return f"ValuationInputError: {e}"
    except (TypeError, ValueError) as e:
        return f"ValuationInputError: non-numeric WACC/terminal growth ({e})"
    return None


def checkpoint2_node(state: AnalysisState) -> dict:
    """
    Checkpoint HITL entre los especialistas y el valuation_agent.

    Muestra el contexto disponible (FCF, precio, market cap, WACC real sugerido) y pide al
    usuario los assumptions del DCF: WACC, near-term growth, años de proyección.

    El usuario reanuda con un dict como:
        {"wacc_pct": 9.0, "terminal_growth_pct": 2.5, "projection_years": 10,
         "revenue_growth_yr1_pct": 12.0}

    Validación en el NODO (no solo en la CLI): si WACC − g < 1pp vuelve a llamar a
    interrupt() con "error" en el payload. LangGraph empareja cada interrupt() del nodo con
    su resume por orden, así que el bucle es seguro: cada vuelta pausa el grafo de nuevo.
    Así un caller programático o la futura web tampoco pueden colar WACC ≤ g (EC-76).

    Si reanuda con {} o None, el valuation_agent usa defaults (CHECKPOINT2_DEFAULTS + WACC sugerido).

    Returns:
        dict con checkpoint2_user_input — leído por valuation_agent para DCF.
    """
    ticker = state["ticker"]
    log.info("checkpoint2_interrupt", ticker=ticker)

    summary = _build_checkpoint2_summary(state)
    payload = summary
    while True:
        user_input = interrupt(payload)
        error = _validate_resume(user_input)
        if error is None:
            break
        log.warning("checkpoint2_invalid_resume", ticker=ticker, error=error)
        payload = {**summary, "error": error}

    return {"checkpoint2_user_input": user_input if user_input is not None else {}}
