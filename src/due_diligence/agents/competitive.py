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

Peer ROIC comparison (Phase 1b):
    Deferred a Phase 1c por ADR-005 (zero hallucination numérica).
    El LLM puede mencionar peers cualitativamente en las citations, pero
    el campo peer_roic_comparison queda vacío hasta que se implemente el
    peer fetch de XBRL en Phase 1c. Ver nota en _parse_llm_response.

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

log = structlog.get_logger()

# Queries Tavily para §2 del checklist. Diseñadas para obtener evidencia
# de pricing power, market share, disruption threats y competitive dynamics.
_TAVILY_QUERY_TEMPLATES = [
    "{company_name} competitive advantage moat pricing power market share",
    "{company_name} competitors competitive landscape industry analysis",
    "{company_name} disruption threat technology business model risk",
    "{company_name} gross margin trend vs competitors operating leverage",
]

_SYSTEM_PROMPT = """You are a value investing analyst specializing in competitive moat analysis.
Analyze the provided financial data and web research to assess the company's competitive advantage.

RULES:
- Base every claim ONLY on the provided context. Never invent facts or numbers.
- For numerical claims, cite the specific financial data provided.
- For qualitative claims, cite the web research source and excerpt.
- Return ONLY valid JSON matching the schema below. No markdown outside JSON.

MOAT CLASSIFICATION:
- "wide": durable competitive advantage likely to persist 10+ years
- "narrow": some competitive advantage but vulnerable to erosion in 5-10 years
- "none": no identifiable structural advantage, commodity or cyclical business

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
            f"{mv.period}: ${mv.value/1e9:.1f}B" for mv in financials.free_cash_flow[:3]
        )
        lines.append(f"Free cash flow: {fcf_str}")

    # Invested capital context
    if financials.invested_capital:
        lines.append(
            f"Invested capital: ${financials.invested_capital.value/1e9:.1f}B ({financials.invested_capital.period})"
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

        return CompetitiveReport(
            ticker=ticker,
            stub=False,
            moat_classification=moat,
            moat_sources=data.get("moat_sources", []),
            peer_roic_comparison=[],  # deferred a Phase 1c
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
        t.format(company_name=company_name) for t in _TAVILY_QUERY_TEMPLATES
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

    log.info(
        "competitive_agent_complete",
        ticker=ticker,
        moat=report.moat_classification,
        citations=len(report.citations),
    )

    return {"competitive": report}
