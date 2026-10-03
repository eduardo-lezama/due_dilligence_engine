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
    5. LLM clasifica moat y devuelve JSON → CompetitiveReport(stub=False)
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

import json
from typing import Any

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
from due_diligence.schemas.base import Citation, FinancialsReport
from due_diligence.schemas.reports import CompetitiveReport
from due_diligence.tools.tavily import TavilyAdapter, format_tavily_for_prompt
from due_diligence.utils.formatting import fmt_usd

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
- Return ONLY valid JSON matching the schema below. No markdown outside JSON.

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

If you classify as WIDE, you MUST list specific evidence for each of the 4 criteria in moat_analysis.
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

OUTPUT SCHEMA:
{
  "moat_classification": "wide" | "narrow" | "none",
  "moat_sources": ["<source1>", "<source2>"],
  "moat_analysis": "<string — 3-4 sentences on the nature and durability of the moat>",
  "widening_or_eroding": "<string — 2-3 sentences: is the moat widening or eroding? Evidence?>",
  "disruption_assessment": "<string — 2-3 sentences on credible disruption threats>",
  "citations": [
    {
      "claim": "<exact claim being cited>",
      "source_url": "<URL or 'Financial data: ROIC series' or 'Financial data: margins'>",
      "excerpt": "<verbatim excerpt or financial data point supporting the claim>",
      "confidence": <0.0 to 1.0>,
      "gaap_vs_nongaap_flag": null
    }
  ]
}"""


def _format_financials_for_prompt(financials: FinancialsReport) -> str:
    """
    Formatea los datos cuantitativos del FinancialsReport como texto estructurado.

    Incluye ROIC series, márgenes y FCF — la evidencia cuantitativa del moat.
    Solo incluye los campos disponibles (algunos pueden ser None o listas vacías).
    """
    lines = ["## Quantitative Financial Data (from SEC XBRL — deterministic)"]

    # ROIC series — la evidencia más directa de moat estructural
    if financials.roic_series:
        roic_str = ", ".join(
            f"{mv.period}: {mv.value:.1%}" for mv in financials.roic_series
        )
        lines.append(f"ROIC series: {roic_str}")

    # Gross margin trend
    if financials.gross_profit and financials.revenue:
        gp_mv = financials.gross_profit
        rev_mv = financials.revenue
        margins = []
        for gp, rev in zip(gp_mv, rev_mv):
            if rev.value > 0:
                margins.append(f"{gp.period}: {gp.value/rev.value:.1%}")
        if margins:
            lines.append(f"Gross margin: {', '.join(margins[:3])}")

    # Operating margin
    if financials.operating_income and financials.revenue:
        oi_mv = financials.operating_income
        rev_mv = financials.revenue
        op_margins = []
        for oi, rev in zip(oi_mv, rev_mv):
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
5. How does the gross/operating margin compare to typical industry levels?

Return valid JSON only."""


def _parse_llm_response(response_text: str, ticker: str) -> CompetitiveReport:
    """
    Parsea la respuesta JSON del LLM en un CompetitiveReport validado.

    peer_roic_comparison queda vacío (Phase 1b) — se llena en Phase 1c
    cuando se implementa el peer XBRL fetch.
    """
    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found in LLM response")

        data: dict[str, Any] = json.loads(response_text[start:end])

        citations = []
        for c in data.get("citations", []):
            try:
                citations.append(Citation(
                    claim=c.get("claim", ""),
                    source_url=c.get("source_url", ""),
                    excerpt=c.get("excerpt", ""),
                    confidence=float(c.get("confidence", 0.5)),
                    gaap_vs_nongaap_flag=c.get("gaap_vs_nongaap_flag"),
                ))
            except Exception as e:
                log.debug("competitive_citation_parse_failed", ticker=ticker, error=str(e))

        # Validar moat_classification contra valores permitidos
        moat_raw = data.get("moat_classification", "none")
        moat = moat_raw if moat_raw in ("wide", "narrow", "none") else "none"

        # peer_roic_comparison se inyecta después del parse desde state["peer_financials"] (ADR-015)
        return CompetitiveReport(
            ticker=ticker,
            stub=False,
            moat_classification=moat,
            moat_sources=data.get("moat_sources", []),
            peer_roic_comparison=[],
            citations=citations,
        )

    except Exception as e:
        log.error(
            "competitive_llm_parse_failed",
            ticker=ticker,
            error=str(e),
            response=response_text[:200],
        )
        return CompetitiveReport(ticker=ticker, stub=False)


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
    response_text = get_client().complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        max_tokens=2048,
    )

    # --- 4. Parsear respuesta ---
    report = _parse_llm_response(response_text, ticker)

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
        bb_v = financials.buybacks[0].value
        fcf_v = financials.free_cash_flow[0].value
        if fcf_v > 0:
            payout_pct = bb_v / fcf_v * 100
            if payout_pct >= 70:
                fails.append(
                    f"Buybacks {payout_pct:.0f}% of FCF (≥70% = stagnant capital base, "
                    f"cash cow profile not wide moat)"
                )

    # Criterio 3: Revenue 3y CAGR ≥ 4%
    if financials.revenue and len(financials.revenue) >= 4:
        newest = financials.revenue[0].value
        oldest_3y = financials.revenue[3].value  # 3 años atrás
        if oldest_3y > 0:
            cagr = (newest / oldest_3y) ** (1 / 3) - 1
            if cagr < 0.04:
                fails.append(f"Revenue 3y CAGR {cagr:.1%} (<4% = no organic growth)")

    return fails
