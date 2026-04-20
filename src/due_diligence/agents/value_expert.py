"""
Value Expert Agent — síntesis integrada + VERDICT.

Qué hace este módulo:
    Implementa el nodo LangGraph `value_expert_agent` que cubre las secciones
    §8 (Psychological & Contrarian Lens) y §9 (Decision Frame) del checklist,
    y sintetiza los outputs de todos los agentes anteriores en un análisis
    estructurado de 9 secciones + VERDICT (ADR-009).

Posición en el grafo:
    valuation_agent → value_expert_agent → critic_agent → archiver → END

Por qué este agente usa un modelo más potente:
    Es el único punto del sistema donde se integra información heterogénea
    (cuantitativa + cualitativa + de mercado + de gestión) en un juicio de
    inversión. La calidad del razonamiento importa más aquí que en los agentes
    especialistas. Se usa settings.openai_expert_model (gpt-5.4-mini por defecto).

Por qué no genera números nuevos:
    Todos los números vienen de los reports anteriores. El value_expert interpreta,
    pondera y contextualiza — nunca inventa cifras (ADR-005). Si el LLM intenta
    añadir un número no presente en el contexto, las instrucciones del sistema
    prompt lo prohíben explícitamente.

ADR-009 — VERDICT obligatorio:
    Cuando stub=False, el campo verdict debe contener "BUY", "HOLD" o "SELL"
    con justificación. Un report sin VERDICT no es un análisis completo.

Checklist cubierto (docs/checklist-value-investing.md):
    §1  Business understanding   ← filings_report
    §2  Moat analysis            ← competitive_report
    §3  Management assessment    ← mgmt_report
    §4  Financial quality        ← financials_report
    §5  Growth & reinvestment    ← financials + competitive
    §6  Valuation                ← valuation_report
    §7  Risks & red flags        ← filings_report.risk_factors
    §8  Psychological lens       ← síntesis LLM (mispricing thesis, contrarian)
    §9  Decision frame           ← integración + VERDICT
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
from due_diligence.schemas.reports import ValueExpertReport

log = structlog.get_logger()

_SYSTEM_PROMPT = """You are an elite value investing analyst in the tradition of Buffett, Munger, Fisher, and Greenblatt.

Your task: synthesize the specialist reports into a complete 9-section investment analysis and issue a final VERDICT.

RULES — NON-NEGOTIABLE:
1. Every numerical claim must come VERBATIM from the provided reports. Never generate, estimate, or interpolate numbers.
2. If a section lacks sufficient data, say so explicitly rather than speculating.
3. The VERDICT must be one of: "BUY", "HOLD", or "SELL". No other values.
4. Confidence (0.0–1.0) reflects how much evidence supports the verdict, not how good the company is.
5. suggested_sizing_pct is your recommended position size as % of a concentrated 10-position portfolio (max 20% per position).
6. Return ONLY valid JSON. No markdown fences, no preamble, no commentary outside the JSON.

VERDICT RUBRIC:
- BUY: clear moat + quality management + DCF upside >20% + no material red flags
- HOLD: mixed signals, fair value, or insufficient data for conviction
- SELL: deteriorating moat OR value trap OR DCF downside >20% with red flags

OUTPUT SCHEMA:
{
  "business_understanding": "<§1: 3-4 sentences. What does it do, how does it make money, circle of competence>",
  "moat_analysis": "<§2: 3-4 sentences. Moat type, ROIC evidence, widening or eroding>",
  "management_assessment": "<§3: 3-4 sentences. Ownership, compensation alignment, capital allocation track record>",
  "financial_quality": "<§4: 3-4 sentences. FCF conversion, balance sheet, margin trends, earnings quality>",
  "growth_reinvestment": "<§5: 2-3 sentences. Reinvestment rate, incremental ROIC, runway>",
  "valuation_section": "<§6: 3-4 sentences. DCF upside/downside, reverse DCF implied growth, multiples vs history>",
  "risks": "<§7: 3-4 sentences. Top 3 risks from filings + market news>",
  "psychological_lens": "<§8: 2-3 sentences. Why might this be mispriced? Contrarian thesis or consensus trap?>",
  "decision_frame": "<§9: 2-3 sentences. How does this fit a concentrated value portfolio? Sizing rationale>",
  "verdict": "BUY" | "HOLD" | "SELL",
  "verdict_confidence": <0.0 to 1.0>,
  "suggested_sizing_pct": <0.0 to 20.0>
}"""


def _format_context(state: AnalysisState) -> str:
    """
    Serializa todos los reports disponibles en el estado como contexto para el LLM.

    Formato legible por el LLM: secciones etiquetadas con el nombre del report.
    Los campos None se omiten para no contaminar el contexto con "null".
    """
    ticker = state["ticker"]
    lines = [f"# Investment Analysis Context: {ticker}\n"]

    # --- Financials ---
    financials = state.get("financials")  # type: ignore[typeddict-item]
    if financials is not None:
        lines.append("## FINANCIALS (EDGAR XBRL — deterministic)")
        if financials.revenue:
            rev = financials.revenue[0]
            lines.append(f"Revenue: ${rev.value/1e9:.1f}B ({rev.period})")
        if financials.operating_income:
            oi = financials.operating_income[0]
            lines.append(f"Operating Income (EBIT): ${oi.value/1e9:.1f}B ({oi.period})")
        if financials.net_income:
            ni = financials.net_income[0]
            lines.append(f"Net Income: ${ni.value/1e9:.1f}B ({ni.period})")
        if financials.free_cash_flow:
            fcf = financials.free_cash_flow[0]
            lines.append(f"Free Cash Flow: ${fcf.value/1e9:.1f}B ({fcf.period})")
        if financials.roic_series:
            roic_str = ", ".join(f"{r.period}: {r.value:.1%}" for r in financials.roic_series[:3])
            lines.append(f"ROIC series: {roic_str}")
        if financials.total_debt is not None:
            lines.append(f"Total Debt: ${financials.total_debt.value/1e9:.1f}B")
        if financials.cash_and_equivalents is not None:
            lines.append(f"Cash + STI: ${financials.cash_and_equivalents.value/1e9:.1f}B")
        lines.append("")

    # --- Management ---
    mgmt = state.get("mgmt")  # type: ignore[typeddict-item]
    if mgmt is not None and not mgmt.stub:
        lines.append("## MANAGEMENT REPORT")
        if mgmt.insider_ownership_pct is not None:
            lines.append(f"Insider ownership: {mgmt.insider_ownership_pct:.1f}%")
        if mgmt.ceo_tenure_years is not None:
            lines.append(f"CEO tenure: {mgmt.ceo_tenure_years:.0f} years")
        if mgmt.compensation_structure_notes:
            lines.append(f"Compensation: {mgmt.compensation_structure_notes}")
        if mgmt.capital_allocation_summary:
            lines.append(f"Capital allocation: {mgmt.capital_allocation_summary}")
        lines.append("")

    # --- Filings ---
    filings = state.get("filings")  # type: ignore[typeddict-item]
    if filings is not None and not filings.stub:
        lines.append("## FILINGS REPORT (10-K)")
        if filings.business_description:
            lines.append(f"Business: {filings.business_description}")
        if filings.risk_factors_summary:
            lines.append(f"Key risks: {filings.risk_factors_summary}")
        if filings.mda_highlights:
            lines.append(f"MDA highlights: {filings.mda_highlights}")
        lines.append("")

    # --- Market ---
    market = state.get("market")  # type: ignore[typeddict-item]
    if market is not None and not market.stub:
        lines.append("## MARKET DATA")
        if market.price_current is not None:
            lines.append(f"Current price: ${market.price_current.value:.2f}")
        if market.market_cap is not None:
            lines.append(f"Market cap: ${market.market_cap.value/1e9:.0f}B")
        if market.price_52w_high is not None and market.price_52w_low is not None:
            lines.append(
                f"52w range: ${market.price_52w_low.value:.2f} – ${market.price_52w_high.value:.2f}"
            )
        if market.recent_news_summary:
            lines.append(f"Recent news: {market.recent_news_summary}")
        lines.append("")

    # --- Competitive ---
    competitive = state.get("competitive")  # type: ignore[typeddict-item]
    if competitive is not None and not competitive.stub:
        lines.append("## COMPETITIVE / MOAT REPORT")
        if competitive.moat_classification:
            sources = ", ".join(competitive.moat_sources) if competitive.moat_sources else "N/A"
            lines.append(f"Moat: {competitive.moat_classification} ({sources})")
        lines.append("")

    # --- Valuation ---
    valuation = state.get("valuation")  # type: ignore[typeddict-item]
    if valuation is not None and not valuation.stub:
        lines.append("## VALUATION REPORT")
        if valuation.dcf_intrinsic_value is not None:
            lines.append(f"DCF equity value: ${valuation.dcf_intrinsic_value.value/1e9:.0f}B")
        if valuation.dcf_upside_pct is not None:
            lines.append(f"DCF upside vs market cap: {valuation.dcf_upside_pct:+.1f}%")
        if valuation.reverse_dcf_implied_growth is not None:
            lines.append(f"Reverse DCF implied growth: {valuation.reverse_dcf_implied_growth:.1%}")
        if valuation.ev_ebit_multiple is not None:
            lines.append(f"EV/EBIT: {valuation.ev_ebit_multiple.value:.1f}x")
        if valuation.ev_fcf_multiple is not None:
            lines.append(f"EV/FCF: {valuation.ev_fcf_multiple.value:.1f}x")
        if valuation.pe_multiple is not None:
            lines.append(f"P/E: {valuation.pe_multiple.value:.1f}x")
        if valuation.earnings_yield_pct is not None:
            lines.append(f"Earnings yield (EBIT/EV): {valuation.earnings_yield_pct:.1f}%")
        if valuation.assumptions is not None:
            a = valuation.assumptions
            lines.append(
                f"DCF assumptions: WACC={a.wacc_pct}%, terminal_growth={a.terminal_growth_pct}%, "
                f"years={a.projection_years}"
            )
        lines.append("")

    return "\n".join(lines)


def _parse_response(response_text: str, ticker: str) -> ValueExpertReport:
    """
    Parsea la respuesta JSON del LLM en un ValueExpertReport.

    Si el parsing falla, devuelve un report parcial con el texto crudo en
    decision_frame para que el output no se pierda completamente.
    """
    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object in response")

        data: dict[str, Any] = json.loads(response_text[start:end])

        verdict_raw = data.get("verdict", "HOLD")
        verdict = verdict_raw if verdict_raw in ("BUY", "HOLD", "SELL") else "HOLD"

        confidence_raw = data.get("verdict_confidence", 0.5)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.5

        sizing_raw = data.get("suggested_sizing_pct", 0.0)
        try:
            sizing = max(0.0, min(20.0, float(sizing_raw)))
        except (TypeError, ValueError):
            sizing = 0.0

        return ValueExpertReport(
            ticker=ticker,
            stub=False,
            business_understanding=data.get("business_understanding"),
            moat_analysis=data.get("moat_analysis"),
            management_assessment=data.get("management_assessment"),
            financial_quality=data.get("financial_quality"),
            growth_reinvestment=data.get("growth_reinvestment"),
            valuation_section=data.get("valuation_section"),
            risks=data.get("risks"),
            psychological_lens=data.get("psychological_lens"),
            decision_frame=data.get("decision_frame"),
            verdict=verdict,
            verdict_confidence=confidence,
            suggested_sizing_pct=sizing,
        )

    except Exception as e:
        log.error(
            "value_expert_parse_failed",
            ticker=ticker,
            error=str(e),
            response=response_text[:300],
        )
        return ValueExpertReport(
            ticker=ticker,
            stub=False,
            decision_frame=f"[Parse error — raw response preserved] {response_text[:500]}",
            verdict="HOLD",
            verdict_confidence=0.0,
            suggested_sizing_pct=0.0,
        )


def value_expert_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: síntesis integrada de todos los reports + VERDICT.

    Usa settings.openai_expert_model (gpt-5.4-mini por defecto) para mayor
    calidad de razonamiento en esta fase de síntesis crítica.

    Returns:
        {"synthesis": ValueExpertReport(stub=False, verdict=...)}
    """
    ticker = state["ticker"]
    log.info("value_expert_start", ticker=ticker)

    context = _format_context(state)
    user_prompt = (
        f"Analyze {ticker} based on the specialist reports below and return the JSON.\n\n"
        f"{context}\n\n"
        "Return valid JSON only."
    )

    log.info("value_expert_llm_call", ticker=ticker, model=settings.openai_expert_model)
    response_text = get_client().complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        model=settings.openai_expert_model,
        max_tokens=4096,
    )

    report = _parse_response(response_text, ticker)

    log.info(
        "value_expert_complete",
        ticker=ticker,
        verdict=report.verdict,
        confidence=report.verdict_confidence,
        sizing_pct=report.suggested_sizing_pct,
    )

    return {"synthesis": report}
