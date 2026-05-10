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


def _fmt_usd(value: float) -> str:
    """
    Formatea un valor USD con unidad adaptativa según magnitud.

    Evita el problema de small caps donde /1e9 produce '$0.0B' para
    valores como $39.5M de FCF. El LLM recibe la cifra legible y la reproduce
    correctamente en su análisis.
    """
    abs_v = abs(value)
    if abs_v >= 1e9:
        return f"${value / 1e9:.1f}B"
    elif abs_v >= 1e6:
        return f"${value / 1e6:.0f}M"
    elif abs_v >= 1e3:
        return f"${value / 1e3:.0f}k"
    else:
        return f"${value:.0f}"


_SYSTEM_PROMPT = """You are an elite value investing analyst in the tradition of Buffett, Munger, Fisher, and Greenblatt.

Your task is NOT to summarize the specialist reports — it is to INTEGRATE and ADJUDICATE conflicting signals across them, then issue a VERDICT.

RULES — NON-NEGOTIABLE:
1. Every numerical claim must come VERBATIM from the provided reports. Never generate, estimate, or interpolate numbers.
2. You MUST name at least 2 conflicting signals across the specialist reports and explain how you adjudicate them. Example: "moat classified 'narrow' but ROIC=25% — I adjudicate as narrow because margin compression in MD&A overrides the trailing ROIC."
3. §6 Valuation MUST explicitly contrast the reverse-DCF implied growth against the historical 5y revenue CAGR (provided in context as HISTORICAL CONTEXT). State whether the market is pricing in expansion, contraction, or continuation vs the historical rate.
4. §9 Decision Frame MUST name the thesis-breaker as a concrete, observable condition. Example: "thesis breaks if gross margin prints below 35% for two consecutive quarters." Vague phrases like "monitor execution" are not acceptable.
5. §7 Risks MUST incorporate any 8-K material events from context (especially Item 4.02 restatements), not only 10-K Item 1A.
6. If a section lacks sufficient data, say so explicitly rather than speculating.
7. Return ONLY valid JSON. No markdown fences, no preamble, no commentary outside the JSON.

VERDICT RUBRIC:
- BUY: moat confirmed + quality management + DCF upside >20% + no material 8-K red flags + conflicting signals resolved in favor of the bull case.
- HOLD: conflicting signals unresolved, fair value, or data insufficient for conviction.
- SELL: deteriorating moat OR value trap OR DCF downside >20% with red flags OR recent Item 4.02 restatement.

OUTPUT SCHEMA:
{
  "business_understanding": "<§1: 3-4 sentences. What does it do, how does it make money, circle of competence>",
  "moat_analysis": "<§2: integrate ROIC evidence with qualitative moat classification. If peer ROIC data is available in context, explicitly compare the subject's ROIC against peers — name the numbers. If they conflict with the moat label, adjudicate with reasoning.>",
  "management_assessment": "<§3: skin in the game (insider %) + capital allocation track record + earnings call Q&A tone if available. Quote one verbatim phrase from transcripts if available.>",
  "financial_quality": "<§4: FCF conversion, balance sheet, margin trends, earnings quality>",
  "growth_reinvestment": "<§5: reinvestment rate, incremental ROIC, runway>",
  "valuation_section": "<§6: MUST contrast reverse-DCF implied growth vs historical 5y revenue CAGR. State the spread and what it implies about market expectations.>",
  "risks": "<§7: include any 8-K material events (especially Item 4.02) PLUS top risks from 10-K Item 1A>",
  "psychological_lens": "<§8: why might this be mispriced? Contrarian thesis or consensus trap?>",
  "decision_frame": "<§9: MUST state the thesis-breaker as a concrete observable condition. No vague placeholders.>",
  "verdict": "BUY" | "HOLD" | "SELL",
  "verdict_confidence": <0.0 to 1.0>,
  "suggested_sizing_pct": <0.0 to 20.0>,
  "verdict_rationale": "<2-3 sentences: WHY this verdict. Reference specific numbers and name the key adjudicated conflict that drove the decision.>"
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
            lines.append(f"Revenue: {_fmt_usd(rev.value)} ({rev.period})")
        if financials.operating_income:
            oi = financials.operating_income[0]
            lines.append(f"Operating Income (EBIT): {_fmt_usd(oi.value)} ({oi.period})")
        if financials.net_income:
            ni = financials.net_income[0]
            lines.append(f"Net Income: {_fmt_usd(ni.value)} ({ni.period})")
        if financials.free_cash_flow:
            fcf = financials.free_cash_flow[0]
            lines.append(f"Free Cash Flow: {_fmt_usd(fcf.value)} ({fcf.period})")
        if financials.roic_series:
            roic_str = ", ".join(f"{r.period}: {r.value:.1%}" for r in financials.roic_series[:3])
            lines.append(f"ROIC series: {roic_str}")
        if financials.total_debt is not None:
            lines.append(f"Total Debt: {_fmt_usd(financials.total_debt.value)}")
        if financials.cash_and_equivalents is not None:
            lines.append(f"Cash + STI: {_fmt_usd(financials.cash_and_equivalents.value)}")

        # Margin compression auto-flag (Fix E): si hay 3+ años con gross margin decreciente,
        # elevarlo como riesgo estructural para §7. Los datos vienen de XBRL (determinista).
        if (financials.gross_profit and financials.revenue
                and len(financials.gross_profit) >= 3 and len(financials.revenue) >= 3):
            margins = []
            for gp, rev in zip(financials.gross_profit, financials.revenue):
                if rev.value and rev.value > 0:
                    margins.append((gp.period, gp.value / rev.value))
            # margins[0] = most recent; check if descending for 3+ years
            if len(margins) >= 3 and all(margins[i][1] < margins[i + 1][1] for i in range(2)):
                trend_str = " → ".join(f"{m:.1%}" for _, m in reversed(margins[:3]))
                lines.append(
                    f"⚠️ STRUCTURAL MARGIN RISK: gross margin has compressed 3 consecutive years "
                    f"({trend_str}, oldest→latest). Elevate to §7 Risks as 'structural margin pressure'."
                )
        lines.append("")

    # --- Management ---
    mgmt = state.get("mgmt")  # type: ignore[typeddict-item]
    if mgmt is not None and not mgmt.stub:
        lines.append("## MANAGEMENT REPORT")
        if mgmt.insider_ownership_pct is not None:
            lines.append(f"Insider ownership (total insiders): {mgmt.insider_ownership_pct:.1f}%")
        if mgmt.founder_ceo is not None:
            lines.append(f"Founder-CEO: {'Yes' if mgmt.founder_ceo else 'No'}")
        if mgmt.ceo_tenure_years is not None:
            lines.append(f"CEO tenure: {mgmt.ceo_tenure_years:.0f} years")
        if mgmt.insider_transaction_pattern:
            lines.append(f"Insider transaction pattern (Form 4): {mgmt.insider_transaction_pattern}")
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
        if filings.geographic_revenue_breakdown:
            lines.append(f"Revenue by geography: {filings.geographic_revenue_breakdown}")
        if filings.risk_factors_summary:
            lines.append(f"Key risks: {filings.risk_factors_summary}")
        if filings.key_regulatory_events:
            # Eventos regulatorios concretos — críticos para §7 y VERDICT
            lines.append(f"SPECIFIC REGULATORY EVENTS: {filings.key_regulatory_events}")
        if filings.mda_highlights:
            lines.append(f"MDA highlights: {filings.mda_highlights}")
        if filings.backlog_usd is not None:
            lines.append(f"Contracted backlog: {_fmt_usd(filings.backlog_usd)}")
        if filings.revenue_guidance:
            lines.append(f"Management guidance: {filings.revenue_guidance}")
        lines.append("")

    # --- Market ---
    market = state.get("market")  # type: ignore[typeddict-item]
    if market is not None and not market.stub:
        lines.append("## MARKET DATA")
        if market.price_current is not None:
            lines.append(f"Current price: ${market.price_current.value:.2f}")
        if market.market_cap is not None:
            lines.append(f"Market cap: {_fmt_usd(market.market_cap.value)}")
        if market.price_52w_high is not None and market.price_52w_low is not None:
            lines.append(
                f"52w range: ${market.price_52w_low.value:.2f} – ${market.price_52w_high.value:.2f}"
            )
        if market.recent_news_summary:
            lines.append(f"Recent news: {market.recent_news_summary}")
        # 8-K material events para §7 Risks (ADR-017 + ADR-019)
        if market.material_events:
            lines.append("Material 8-K events:")
            for e in market.material_events:
                items_str = ", ".join(e.get("item_numbers", []))
                lines.append(f"  [{e.get('filing_date', '?')}] Items {items_str}: {e.get('summary_text', '')[:200]}")
        lines.append("")

    # --- Competitive ---
    competitive = state.get("competitive")  # type: ignore[typeddict-item]
    if competitive is not None and not competitive.stub:
        lines.append("## COMPETITIVE / MOAT REPORT")
        if competitive.moat_classification:
            sources = ", ".join(competitive.moat_sources) if competitive.moat_sources else "N/A"
            lines.append(f"Moat: {competitive.moat_classification} ({sources})")
        # Peer ROIC comparison — datos cuantitativos para anclar §2 Moat Analysis
        if competitive.peer_roic_comparison:
            ticker = state.get("ticker", "subject")  # type: ignore[typeddict-item]
            lines.append("Peer ROIC comparison (use in §2 — trail shows trend; flag cyclicals vs compounders):")
            financials = state.get("financials")  # type: ignore[typeddict-item]
            if financials and financials.roic_series:
                own_series = financials.roic_series[:3]
                own_trail = " → ".join(f"{r.value:.1%}" for r in reversed(own_series))
                lines.append(f"  {ticker} (subject): {own_series[0].value:.1%} ({own_series[0].period}) | trend: {own_trail}")
            for pr in competitive.peer_roic_comparison:
                if pr.roic_trail and len(pr.roic_trail) > 1:
                    trail_str = " → ".join(f"{r['roic']:.1%}" for r in reversed(pr.roic_trail))
                    lines.append(f"  {pr.ticker} (peer): {pr.roic:.1%} ({pr.period}) | trend: {trail_str}")
                else:
                    lines.append(f"  {pr.ticker} (peer): {pr.roic:.1%} ({pr.period})")
        lines.append("")

    # --- Valuation ---
    valuation = state.get("valuation")  # type: ignore[typeddict-item]
    if valuation is not None and not valuation.stub:
        lines.append("## VALUATION REPORT")
        if valuation.dcf_intrinsic_value is not None:
            lines.append(f"DCF equity value: {_fmt_usd(valuation.dcf_intrinsic_value.value)}")
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

    # --- Historical CAGR anchor + user's growth justification (ADR-019) ---
    cagr = state.get("historical_cagr_5y")        # type: ignore[typeddict-item]
    cagr_years = state.get("historical_cagr_years", 5)  # type: ignore[typeddict-item]
    if cagr is not None:
        lines.append("## HISTORICAL CONTEXT (for §6 Valuation)")
        lines.append(f"Revenue {cagr_years}y CAGR (from XBRL, last {cagr_years} fiscal years): {cagr:.1%}")
        lines.append(
            "Compare this to the reverse-DCF implied growth above. "
            "If implied growth > CAGR, the market is pricing acceleration. "
            "If implied growth < CAGR, the market is pricing deceleration."
        )
        # Justificación del usuario sobre por qué sus asunciones de growth divergen o
        # se alinean con el CAGR histórico — aportada en Checkpoint 2 (ADR-018).
        # Si está disponible, es contexto clave para §6 Valuation y §9 Decision Frame.
        cp2 = state.get("checkpoint2_user_input") or {}  # type: ignore[typeddict-item]
        growth_justification = cp2.get("growth_justification", "")
        if growth_justification:
            lines.append(f"Investor's rationale for DCF growth assumptions: {growth_justification}")
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
            verdict_rationale=data.get("verdict_rationale"),
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
    # 8192 tokens: el prompt v2 genera respuestas más largas que el original
    # (9 secciones con adjudicación de conflictos + thesis-breaker concreto).
    # 4096 era insuficiente para empresas con contexto rico (ej. DRS con 20+ páginas de 10-K).
    response_text = get_client().complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        model=settings.openai_expert_model,
        max_tokens=8192,
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
