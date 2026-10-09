"""
Competitive Agent — análisis de moat y ventaja competitiva.

Qué hace este módulo:
    Implementa el nodo LangGraph `competitive_agent` que cubre la sección §2
    (Moat Analysis) del checklist canónico. Combina datos deterministas del
    FinancialsReport (ROIC series, márgenes) con búsqueda web via Tavily
    para clasificar el tipo de moat y evaluar señales de erosión.

Flujo del nodo:
    1. Leer FinancialsReport desde el estado — no fetcha EDGAR de nuevo
    2. Formatear ROIC series y márgenes como contexto cuantitativo
    3. Búsqueda Tavily con 4 queries sobre competitive landscape
    4. Prompt con datos financieros + contexto web + preguntas §2
    5. LLMClient.structured(CompetitiveLLMOutput) → CompetitiveReport(stub=False);
       si el LLM falla → CompetitiveReport(stub=True, error=...) (fail loud, ADR-032)
    6. Return {"competitive": report} → LangGraph merge en AnalysisState

Por qué consume financials del estado (no fetchea EDGAR):
    El FinancialsReport ya fue calculado por financials_node en Phase 1a
    y está en el estado. Re-fetchear EDGAR sería redundante y costoso.
    El competitive_agent es un consumidor, no un productor de datos XBRL.

Peer ROIC comparison (Phase 2, ADR-015):
    peers_node (graph.py) fetcha XBRL de los peer tickers aportados por el usuario
    en Checkpoint 1 y los guarda en state["peer_financials"]. Este agente los
    consume en competitive_agent() para poblar peer_roic_comparison.
    El LLM no genera peers — los números vienen de EDGAR (ADR-005).

Por qué imports al nivel de módulo:
    Requerido para mocker.patch en tests.
"""

from __future__ import annotations

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.agents.llm_support import error_label, model_used
from due_diligence.llm.client import get_client
from due_diligence.llm.errors import LLMError
from due_diligence.schemas.base import FinancialsReport
from due_diligence.schemas.llm_outputs import CompetitiveLLMOutput
from due_diligence.schemas.reports import CompetitiveReport
from due_diligence.tools.tavily import TavilyAdapter, format_tavily_for_prompt
from due_diligence.utils.formatting import fmt_usd
from due_diligence.validation.citations import count_verified, validate_and_relabel
from due_diligence.utils.periods import align_by_period, cagr_by_fiscal_years, value_at

log = structlog.get_logger()

# Queries Tavily para §2 del checklist. Diseñadas para obtener evidencia
# de pricing power, market share, disruption threats y competitive dynamics.
#
# Formato: (template, time_range). Se incluye {ticker} para desambiguar empresas
# con nombre genérico. time_range diferenciado por tipo de pregunta:
#   - Moat/ventaja competitiva: None (el moat se evalúa sobre historial multi-año,
#     no solo el último año; artículos de 2-5 años atrás son relevantes).
#   - Landscape/disruption/márgenes: "year" (dinámica competitiva reciente).
_TAVILY_QUERY_TEMPLATES: list[tuple[str, str | None]] = [
    ("{ticker} {company_name} competitive advantage moat pricing power market share", None),
    ("{ticker} {company_name} competitors competitive landscape industry analysis", "year"),
    ("{ticker} {company_name} disruption threat technology business model risk", "year"),
    ("{ticker} {company_name} gross margin trend vs competitors operating leverage", "year"),
]

_SYSTEM_PROMPT = """You are a value investing analyst specializing in competitive moat analysis.
Analyze the provided financial data and web research to assess the company's competitive advantage.

RULES:
- Base every claim ONLY on the provided context. Never invent facts or numbers.
- For numerical claims, cite the specific financial data provided.
- For qualitative claims, cite the web research source and excerpt.
- Citation excerpts are verified automatically against the sources: copy them VERBATIM
  (for financial data, copy the exact line from SOURCE 1). Paraphrased excerpts are discarded.
- The output format is enforced by the response schema (moat_classification, moat_sources, citations).

MOAT CLASSIFICATION RUBRIC — APPLY STRICTLY (ADR-024):

The default is NARROW. WIDE requires ALL criteria below; missing any one downgrades to NARROW.

WIDE moat — requires ALL of:
1. ROIC > 15% sustained ≥ 4 consecutive years (use the full roic_series, not just latest).
2. Capital base trending UP or stable — proxy: invested_capital growing OR buybacks <70% of FCF.
   A company returning ~all FCF to shareholders has stagnant capital base = NOT wide moat.
3. Revenue 3y CAGR ≥ 4% (real organic growth, not cyclical bounce).
4. At least ONE specific structural advantage with EVIDENCE in the citations:
   - Dominant scale (market share leader in defined market — name the share %)
   - Network effects (with documented user/transaction growth flywheel)
   - Switching costs (contractual / integration / data lock-in evidence)
   - Brand pricing power AT SCALE (premium pricing data + gross margin >50% sustained)
   - Regulatory barrier (license, patent, government contract moat)
   - Efficient scale (small market, 1-2 dominant players, structural barrier to entry)

If you classify as WIDE, you MUST provide one citation per criterion (4 citations minimum) with the specific evidence.
If any criterion fails or evidence is absent, downgrade to NARROW.

NARROW moat — ROIC > WACC sustained + at least one structural advantage above (real but vulnerable).
   IMPORTANT: high-ROIC niche businesses with limited reinvestment runway (high payout, flat FCF,
   stagnant capital base) are NARROW with high returns, NOT wide. They are cash cows on small niches,
   not compounding machines. Examples: family-controlled specialty retailers (BKE), mature niche brands
   with 95%+ FCF payout. The high ROIC reflects a small, well-defended moat — narrow by definition.

NONE — declining ROIC, no specific advantage identifiable, commodity or pure cyclical.

MOAT SOURCES (use only applicable):
"brand", "switching_costs", "network_effects", "cost_advantage",
"efficient_scale", "intangibles", "regulatory"

Use the citations to document the evidence for: the moat type, the ROIC evidence, whether the
moat is widening or eroding, and credible disruption threats (checklist §2.1-§2.6)."""


def _format_financials_for_prompt(financials: FinancialsReport) -> str:
    """
    Formatea los datos cuantitativos del FinancialsReport como texto estructurado.

    Incluye ROIC series, márgenes y FCF — la evidencia cuantitativa del moat.
    Solo incluye los campos disponibles (algunos pueden ser None o listas vacías).
    """
    lines = ["## Quantitative Financial Data (from SEC XBRL — deterministic)"]

    # ROIC series — la evidencia más directa de moat estructural
    if financials.roic_series:
        # Ventana de 5 años (T7): con periods=10 la serie completa serían 10 años.
        roic_str = ", ".join(
            f"{mv.period}: {mv.value:.1%}" for mv in financials.roic_series[:5]
        )
        lines.append(f"ROIC series: {roic_str}")

    # Gross margin trend
    if financials.gross_profit and financials.revenue:
        gp_mv = financials.gross_profit
        rev_mv = financials.revenue
        margins = []
        for gp, rev in align_by_period(gp_mv, rev_mv):  # EC-82: mismo año, no misma posición
            if rev.value > 0:
                margins.append(f"{gp.period}: {gp.value/rev.value:.1%}")
        if margins:
            lines.append(f"Gross margin: {', '.join(margins[:3])}")

    # Operating margin
    if financials.operating_income and financials.revenue:
        oi_mv = financials.operating_income
        rev_mv = financials.revenue
        op_margins = []
        for oi, rev in align_by_period(oi_mv, rev_mv):  # EC-82: mismo año, no misma posición
            if rev.value > 0:
                op_margins.append(f"{oi.period}: {oi.value/rev.value:.1%}")
        if op_margins:
            lines.append(f"Operating margin: {', '.join(op_margins[:3])}")

    # FCF generation
    if financials.free_cash_flow:
        fcf_str = ", ".join(
            f"{mv.period}: {fmt_usd(mv.value)}" for mv in financials.free_cash_flow[:3]
        )
        lines.append(f"Free cash flow: {fcf_str}")

    # Invested capital context
    if financials.invested_capital:
        lines.append(
            f"Invested capital: {fmt_usd(financials.invested_capital.value)} ({financials.invested_capital.period})"
        )

    return "\n".join(lines)


def _build_user_prompt(
    ticker: str,
    company_name: str,
    financials_text: str,
    tavily_text: str,
) -> str:
    return f"""# Competitive Moat Analysis: {ticker} ({company_name})

## SOURCE 1: Financial Data (XBRL — deterministic, from SEC EDGAR)
{financials_text}

---

## SOURCE 2: Web Research (Tavily)
{tavily_text}

---

# CHECKLIST §2: Moat Analysis

Answer each question using ONLY the sources above:

1. What type of competitive advantage does this company have? (brand, switching costs,
   network effects, cost advantage, efficient scale, intangibles, regulatory, or none?)
2. What is the ROIC evidence? Does sustained high ROIC vs industry confirm the moat?
3. Is the moat widening or eroding? What are the specific signals?
4. What credible disruption threats exist? Is the company the disruptor or the disrupted?
5. How does the gross/operating margin compare to typical industry levels?"""


def _build_report(out: CompetitiveLLMOutput, ticker: str, corpus: dict[str, str]) -> CompetitiveReport:
    """Convierte la salida validada del LLM en el report de dominio.

    El Literal["wide","narrow","none"] del schema sustituye la validación manual del
    moat. peer_roic_comparison queda vacío: lo rellena peers_node después (ADR-015).
    Las citas se validan contra `corpus` ({"Financial data", "Web (Tavily)"}, EC-69).
    """
    cits = validate_and_relabel([c.to_citation() for c in out.citations], corpus)
    return CompetitiveReport(
        ticker=ticker, stub=False,
        moat_classification=out.moat_classification,
        moat_sources=list(out.moat_sources),
        peer_roic_comparison=[],
        citations=cits,
    )


def competitive_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: clasifica moat usando FinancialsReport del estado + Tavily + LLM.

    Consume el FinancialsReport ya calculado — no hace fetch adicional a EDGAR.
    """
    ticker = state["ticker"]
    company_name = state.get("company_name", ticker)  # type: ignore[typeddict-item]
    financials: FinancialsReport | None = state.get("financials")  # type: ignore[typeddict-item]

    log.info("competitive_agent_start", ticker=ticker)

    # --- 1. Formatear datos financieros del estado como contexto cuantitativo ---
    if financials is not None:
        financials_text = _format_financials_for_prompt(financials)
    else:
        financials_text = "Financial data not available in state."
        log.warning("competitive_agent_no_financials", ticker=ticker)

    # --- 2. Búsqueda web Tavily para competitive landscape ---
    tavily_queries = [
        (t.format(ticker=ticker, company_name=company_name), tr)
        for t, tr in _TAVILY_QUERY_TEMPLATES
    ]
    tavily_results = TavilyAdapter.search(tavily_queries)
    tavily_text = format_tavily_for_prompt(tavily_results)

    # --- 3. Construir prompt y llamar al LLM ---
    user_prompt = _build_user_prompt(
        ticker=ticker,
        company_name=company_name,
        financials_text=financials_text,
        tavily_text=tavily_text,
    )

    log.info("competitive_agent_llm_call", ticker=ticker)
    client = get_client()
    try:
        out = client.structured(
            CompetitiveLLMOutput,
            messages=[{"role": "user", "content": user_prompt}],
            system=_SYSTEM_PROMPT,
            max_tokens=4096,   # antes 2048: margen para las citas de evidencia por criterio
        )
    except LLMError as e:
        # Fail loud (ADR-032, EC-68): antes un parse fallido devolvía stub=False con moat=None.
        log.error("competitive_agent_llm_failed", ticker=ticker, error_type=type(e).__name__, error=str(e))
        return {"competitive": CompetitiveReport(ticker=ticker, stub=True, error=error_label(e))}

    # --- 4. Salida tipada → report de dominio ---
    corpus = {"Financial data": financials_text or "", "Web (Tavily)": tavily_text or ""}
    report = _build_report(out, ticker, corpus)
    n_ok, n_bad = count_verified(report.citations)
    log.info("competitive_citations_validated", ticker=ticker, verified=n_ok, unverified=n_bad)
    report = report.model_copy(update={"model_used": model_used(client)})

    # --- 5. Moat calibration clamp (Fix #1 / EC-28 / ADR-024) ---
    # Defensa en profundidad: si el LLM clasifica "wide" pero los criterios cuantitativos
    # no lo soportan, downgrade a "narrow". Mismo patrón que sizing clamp en EC-24.
    # El LLM puede ignorar la rúbrica del prompt; el clamp impone disciplina.
    if report.moat_classification == "wide" and financials is not None:
        downgrade_reasons = _check_wide_moat_criteria(financials)
        if downgrade_reasons:
            log.warning(
                "competitive_moat_downgraded_wide_to_narrow",
                ticker=ticker,
                reasons=downgrade_reasons,
            )
            report = report.model_copy(update={"moat_classification": "narrow"})

    # Nota: peer_roic_comparison se construye en peers_node (no aquí).
    # competitive_agent corre en el fan-out ANTES de peers_node, así que
    # state["peer_financials"] no existe aún cuando este nodo ejecuta.
    # peers_node actualiza state["competitive"] con los datos de peers via model_copy.

    log.info(
        "competitive_agent_complete",
        ticker=ticker,
        moat=report.moat_classification,
        citations=len(report.citations),
    )

    return {"competitive": report}


def _check_wide_moat_criteria(financials: FinancialsReport) -> list[str]:
    """
    Valida los 4 criterios cuantitativos de wide moat (ADR-024).

    Returns:
        Lista de razones por las que NO se cumple wide. Si vacía, criterios cumplidos.
    """
    fails: list[str] = []

    # Criterio 1: ROIC > 15% sostenido ≥ 4 años
    if not financials.roic_series or len(financials.roic_series) < 4:
        fails.append("ROIC series < 4 years (need 4+ for wide)")
    else:
        roic_4y = financials.roic_series[:4]
        if any(r.value < 0.15 for r in roic_4y):
            below = [f"{r.period}: {r.value:.1%}" for r in roic_4y if r.value < 0.15]
            fails.append(f"ROIC fell below 15% in: {', '.join(below)}")

    # Criterio 2: Capital base estable o creciente — proxy: buybacks < 70% FCF
    if financials.buybacks and financials.free_cash_flow:
        fcf_v = financials.free_cash_flow[0].value
        # Recompras del MISMO ejercicio que el FCF (revisión final H1): buybacks[0] podía ser de
        # otro año si la serie tenía huecos. Sin dato de ese año, el criterio no se evalúa.
        bb_v = value_at(financials.buybacks, financials.free_cash_flow[0].period)
        if bb_v is not None and fcf_v > 0:
            payout_pct = bb_v / fcf_v * 100
            if payout_pct >= 70:
                fails.append(
                    f"Buybacks {payout_pct:.0f}% of FCF (≥70% = stagnant capital base, "
                    f"cash cow profile not wide moat)"
                )

    # Criterio 3: Revenue 3y CAGR ≥ 4%
    # EC-83: ventana de 3 AÑOS FISCALES (no índice 3, que con huecos son 4+ años).
    if financials.revenue and len(financials.revenue) >= 2:
        r = cagr_by_fiscal_years(financials.revenue, max_years=3)
        if r is not None and r[1] == 3:  # solo si hay un punto exactamente 3 años atrás
            cagr = r[0]
            if cagr < 0.04:
                fails.append(f"Revenue 3y CAGR {cagr:.1%} (<4% = no organic growth)")

    return fails
