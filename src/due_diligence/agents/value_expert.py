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
import statistics
from typing import Any

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
from due_diligence.schemas.reports import ValueExpertReport

from due_diligence.utils.formatting import fmt_usd as _fmt_usd

log = structlog.get_logger()


_SYSTEM_PROMPT = """You are an elite value investing analyst in the tradition of Buffett, Munger, Fisher, and Greenblatt.

Your task is NOT to summarize the specialist reports — it is to INTEGRATE and ADJUDICATE conflicting signals across them, then issue a VERDICT.

RULES — NON-NEGOTIABLE:
1. Every numerical claim must come VERBATIM from the provided reports. Never generate, estimate, or interpolate numbers.
2. You MUST name at least 2 conflicting signals across the specialist reports and explain how you adjudicate them. Example: "moat classified 'narrow' but ROIC=25% — I adjudicate as narrow because margin compression in MD&A overrides the trailing ROIC."
3. §6 Valuation MUST explicitly contrast the reverse-DCF implied growth against the historical 5y revenue CAGR (provided in context as HISTORICAL CONTEXT). State whether the market is pricing in expansion, contraction, or continuation vs the historical rate.
4. §9 Decision Frame MUST name the thesis-breaker as a concrete, observable condition. Example: "thesis breaks if gross margin prints below 35% for two consecutive quarters." Vague phrases like "monitor execution" are not acceptable.
5. §7 Risks MUST incorporate any 8-K material events from context (especially Item 4.02 restatements), not only 10-K Item 1A.
6. §3 Management MUST reconcile insider sale prices against current market price when both are available. If insider transaction pattern reports specific sale prices and the current price is materially different (>15% gap), state the gap explicitly and what it implies (selling at peak before crash = potential value trap signal; or routine diversification at fair value = neutral).
7. §5 Growth & Reinvestment MUST report the reinvestment rate (capex as % of OCF) when 'Reinvestment rate' is in context. State whether the company is in invest mode (>50%), balanced (20-50%), or harvest mode (<20%).
8. §7 Risks MUST cross-check signals: if (a) news flow shows price decline AND (b) insider activity shows selling at higher prices recently AND (c) margin trends are deteriorating, name this combination explicitly as a "value trap signal pattern" — multiple signs pointing the same direction.
9. If a section lacks sufficient data, say so explicitly rather than speculating.
10. NEWS HEADLINE QUALITY: news headlines from Finnhub/Tavily are TITLES, not validated claims. Treat them as suggestive only. Quote a headline phrase as a fact ONLY if it is corroborated by either: (a) the article body summary, (b) a 10-K/10-Q section, or (c) financial data in the context. Phrases like "rising receivables risk margins" appearing only in a title are not financial claims — do not echo them as if validated. If a news headline contains awkward phrasing or what looks like a parsing artifact, ignore it entirely.
11. INSIDER ACTIVITY MAGNITUDE (§3): when classifying insider buying as a signal, use the TOTAL DOLLAR VALUE, not just count or direction. Apply this rubric:
    - Token purchase (<$50K total): cosmetic compliance/alignment, NOT a conviction signal. Often a single director satisfying a minimum ownership requirement.
    - Modest insider buying ($50K–$250K total): mild confidence signal, especially from CEO/CFO. From outside directors, mostly compliance.
    - Material insider buying (>$250K total): genuine conviction signal worth weighing in the verdict.
    - ABSENCE OF BUYING during drawdown >30% from 52w high: this is itself a signal — when insiders had the opportunity to add at depressed prices and chose not to, that is a negative information signal, not "neutral." State this explicitly when the stock is in drawdown AND no material buying by CEO/CFO/COO appears in the last 12 months.
12. SEGMENT-AWARE RISK ANALYSIS (§7): if the business has multiple distinct segments (multi-segment 10-K, named in business_description), analyze the named risks PER SEGMENT, not at company level. The same risk (e.g. tariff, regulatory, commodity price) often has opposite signs across segments. Example: a fertilizer producer's potash segment may absorb a tariff via pricing while the phosphate segment is hit by input-cost inflation — naming "tariff risk" generically misses the real direction. Identify which segment carries which risk and quantify when data allows.
13. QUARTERLY DATA INTEGRATION (§4/§5/§7): if news flow mentions specific quarterly metrics ('Q1 2026 net loss $258M', 'gross margin compressed 140bps QoQ', 'guidance withdrawn for FY2026') that are more recent than the 10-K, you MUST integrate them into §4 Financial Quality and §5 Growth conclusions — not just mention them as factoids in §7. If a management guidance withdrawal or cut appears in news, treat it as a material red flag in §7 with explicit weight in the verdict, regardless of whether 10-K Item 1A mentions it.
14. Return ONLY valid JSON. No markdown fences, no preamble, no commentary outside the JSON.
15. THESIS-BREAK SELF-CHECK (§9): BEFORE finalizing the verdict, verify that the thesis-breaker condition you define in §9 is NOT ALREADY TRUE in the data you just analyzed. If the condition IS already met (e.g., you state "thesis breaks if gross margin falls below 30%" but §4 shows gross margin is already 28%), you MUST either (a) revise the verdict to reflect the already-broken thesis, or (b) choose a DIFFERENT thesis-breaker that is forward-looking. A verdict that contradicts its own thesis-breaker is a critical logical error.
16. BACKLOG & BOOK-TO-BILL (§5): for defense, aerospace, industrial, government-contract, or long-cycle businesses, backlog and book-to-bill ratio are CRITICAL forward indicators. If the QUARTERLY UPDATE, press release, or filings report mentions backlog, orders, or book-to-bill, you MUST: (a) extract the absolute backlog figure and YoY change, (b) compute book-to-bill if orders and revenue are both available (orders/revenue; >1.0 = growing, <1.0 = shrinking), (c) integrate into §5 Growth as the PRIMARY growth signal (more reliable than revenue CAGR for contract businesses). If backlog is declining while revenue appears stable, flag this as a LEADING INDICATOR of future revenue decline in §7.
17. 10b5-1 PLAN SALES (§3): transactions explicitly marked as "10b5-1 PRE-ARRANGED PLAN" in the insider activity data are NOT informative about current management sentiment. These sales were programmed months before execution — the decision date is the plan adoption date, not the execution date. Do NOT use them as evidence of insider distribution, bearish sentiment, or value trap signals. Instead, note them factually as "pre-arranged plan sale" and EXCLUDE them from your insider sentiment assessment. The relevant signals from insiders are: (a) discretionary open-market purchases (bullish), (b) ABSENCE of buying during drawdowns (mildly bearish per rule 11), (c) discretionary sales NOT under a plan (bearish). A CEO with a 10b5-1 for estate planning who also owns 15%+ is demonstrating alignment through ownership magnitude, not trading activity.
18. PEER RELATIVE VALUATION (§6): when context contains a "## PEER RELATIVE VALUATION" block, §6 Valuation MUST use it. Specifically:
    (a) State whether the subject trades at a discount or premium to the peer median EV/EBIT — name both numbers and the % gap. A premium requires a growth or quality justification; a discount is a potential margin of safety signal only if moat and quality pass.
    (b) State whether the subject FCF yield is above or below the peer median — name both numbers. FCF yield above peer median suggests relative cheapness on cash generation; below suggests the market prices in higher growth or the business generates less cash than peers.
    (c) If Magic Formula rank is available, name it and interpret it: rank 1 = best combined earnings yield + ROIC in the peer group; rank N = worst. A rank in the bottom half with a BUY thesis requires explicit justification.
    (d) If all peers failed to fetch (block is absent), explicitly note in §6 that relative valuation context is unavailable and the absolute multiples (EV/EBIT, EV/FCF) must be interpreted without sector benchmarks — increase uncertainty accordingly.
19. POST-10-K ACQUISITION (§4+§5+§6): when context contains a "## POST-10-K ACQUISITION DETECTED" block, treat this as a CRITICAL staleness warning for balance sheet and growth data:
    (a) §4 Financial Quality MUST explicitly flag that reported debt and cash figures come from the 10-K balance sheet, which PREDATES the acquisition. If the deal involved material cash outlay or new credit facilities (visible in 8-K events), state that the actual post-acquisition balance sheet is materially different from the reported figures. Do NOT present pre-acquisition debt/cash as current fact.
    (b) §5 Growth & Reinvestment MUST distinguish organic from inorganic revenue. If the acquisition closed DURING the most recent quarter reported (visible from the acquisition date vs Q1 press release date), the reported quarterly revenue growth includes an inorganic component. Flag this explicitly — do NOT present the blended growth rate as a pure organic signal. If the acquisition date and Q1 close date allow estimation of inorganic contribution, make the estimate.
    (c) §6 Valuation MUST caveat EV-based multiples (EV/EBIT, EV/FCF) as potentially understated. If cash was consumed and/or debt was raised to finance the deal, the true EV is higher than what the 10-K balance sheet implies. Note this uncertainty explicitly — EV multiples based on pre-acquisition data are directional only, not precise.
    (d) §9 Decision Frame MUST name the pending 10-Q (first quarterly filing post-acquisition) as the event that will resolve the balance sheet uncertainty and clarify organic growth rate.
20. DCF GROWTH ASSUMPTION REFLEXION (§6): the context provides a "## DCF GROWTH ASSUMPTION" block with the near-term growth rate used and its source. In §6 you MUST:
    (a) State the growth rate used and its source in one sentence.
    (b) Assess whether it is credible given §5 growth analysis and the quarterly data — does historical FCF CAGR reflect a sustainable run-rate or a cyclical peak/trough?
    (c) If the source is "GENERIC FALLBACK" (default_7pct), explicitly warn the reader that the DCF upside has no empirical basis and should be treated as purely illustrative. Do NOT present a DCF upside anchored on 7% generic as a solid investment thesis.
    (d) If source is "revenue CAGR proxy", note the limitation: revenue growing ≠ FCF growing if margins are compressing. State whether this is expansionary or contractionary for FCF specifically.

VERDICT RUBRIC:
- BUY: moat confirmed + quality management + DCF upside >20% + no material 8-K red flags + conflicting signals resolved in favor of the bull case.
- HOLD: conflicting signals unresolved, fair value, or data insufficient for conviction.
- SELL: deteriorating moat OR value trap OR DCF downside >20% with red flags OR recent Item 4.02 restatement.

SIZING DISCIPLINE — NON-NEGOTIABLE:
- `suggested_sizing_pct` MUST scale with `verdict_confidence`. Rule: max sizing ≤ verdict_confidence × 10.
  Examples: confidence 0.50 → max 5% sizing; confidence 0.70 → max 7%; confidence 0.85 → max 8.5%.
- HOLD with confidence < 0.65 → sizing should be 0-2%, not 3%+.
- Never recommend sizing > 10% unless confidence > 0.85 AND verdict is BUY.
- If signals conflict and you adjudicate as HOLD, sizing reflects the unresolved uncertainty — keep small.

OUTPUT SCHEMA:
{
  "business_understanding": "<§1: 3-4 sentences. What does it do, how does it make money, circle of competence>",
  "moat_analysis": "<§2: integrate ROIC evidence with qualitative moat classification. If peer ROIC data is available in context, explicitly compare the subject's ROIC against peers — name the numbers. If they conflict with the moat label, adjudicate with reasoning.>",
  "management_assessment": "<§3: skin in the game (insider %) + capital allocation track record + earnings call Q&A tone if available. Quote one verbatim phrase from transcripts if available. MUST reconcile insider sale prices vs current market price when both are in context — name the gap explicitly. MUST classify insider buying by TOTAL DOLLAR MAGNITUDE per rule 11: token (<$50K) is cosmetic, NOT conviction. ABSENCE of CEO/CFO open-market buying during drawdown >30% is itself a negative signal.>",
  "financial_quality": "<§4: FCF conversion, balance sheet, margin trends, earnings quality. If 'Capital returned to shareholders' is in context, you MUST report it explicitly: how much was returned, what % of FCF, split between buybacks/dividends. Capital allocation is core to value investing thesis. If quarterly news data is more recent than 10-K, update conclusions accordingly (rule 13).>",
  "growth_reinvestment": "<§5: reinvestment rate (capex/OCF — MUST report the precomputed value if available) + ROIC trend interpretation (incremental ROIC proxy — RISING means new capital earns above base, FALLING means value destruction). State whether the company is in invest/balanced/harvest mode AND whether marginal returns are positive or negative based on the ROIC trend. Integrate quarterly news data when more recent than 10-K (rule 13).>",
  "valuation_section": "<§6: MUST contrast reverse-DCF implied growth vs historical 5y revenue CAGR. State the spread and what it implies about market expectations.>",
  "risks": "<§7: include any 8-K material events (especially Item 4.02) PLUS top risks from 10-K Item 1A. MUST cross-check news flow + insider activity + margin trends — if multiple signals point negative, name the value trap pattern explicitly. MUST analyze named risks PER SEGMENT when company is multi-segment (rule 12) — same tariff/regulatory/commodity risk often has opposite signs across segments. MUST integrate any quarterly news data more recent than the 10-K (Q1/Q2/Q3 metrics, guidance changes) and weigh them in the verdict, not just mention as factoids (rule 13).>",
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
        # Staleness check (EC-46): si el 10-K tiene >180 días, advertir al LLM
        # para que no trate los datos anuales como verdad absoluta cuando hay
        # datos trimestrales más recientes (quarterly update, press releases).
        from datetime import date as _date
        staleness_days = (_date.today() - financials.as_of_date).days
        if staleness_days > 180:
            lines.append(
                f"⚠️ DATA STALENESS WARNING: the 10-K data below is {staleness_days} days old "
                f"(as-of {financials.as_of_date}). If a QUARTERLY UPDATE section appears below, "
                f"those figures are MORE RECENT and should take precedence for §4, §5, and §7."
            )
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

        # Reinvestment rate (MCE-22) — precomputado para §5 Growth & Reinvestment.
        # Capex / OCF dice qué proporción del cash generado se reinvierte vs se devuelve.
        # >50% = invest mode, 20-50% = balanced, <20% = harvest mode (empresa madura).
        if financials.operating_cash_flow and financials.capex:
            ocf_v = financials.operating_cash_flow[0].value
            capex_v = financials.capex[0].value
            if ocf_v > 0:
                reinvest_pct = capex_v / ocf_v * 100
                if reinvest_pct >= 50:
                    mode = "invest mode"
                elif reinvest_pct >= 20:
                    mode = "balanced"
                else:
                    mode = "harvest mode"
                lines.append(
                    f"Reinvestment rate (capex/OCF, {financials.operating_cash_flow[0].period}): "
                    f"{reinvest_pct:.0f}% — {mode}"
                )

        if financials.roic_series:
            # Surfacear serie completa (hasta 5 años) — antes truncábamos a 3 lo que
            # ocultaba picos/valles cíclicos (EC-38). El cálculo de avg ΔROIC abajo ya
            # usa la serie completa, así que el display debe ser consistente.
            roic_str = ", ".join(f"{r.period}: {r.value:.1%}" for r in financials.roic_series[:5])
            lines.append(f"ROIC series: {roic_str}")
            # Incremental ROIC proxy (Fix #3 + EC-32): tendencia + delta concreto en pp.
            # Antes solo emitíamos interpretación cualitativa (RISING/FALLING). Auditoría
            # externa pidió el número concreto: "marginal ROIC appears positive" sin valor
            # no es accionable. Ahora computamos avg annual delta en percentage points.
            if len(financials.roic_series) >= 2:
                roic_latest = financials.roic_series[0].value
                roic_oldest = financials.roic_series[-1].value
                n_periods = len(financials.roic_series) - 1  # 4 años → 3 deltas
                avg_annual_delta_pp = (roic_latest - roic_oldest) / n_periods * 100  # en pp
                if roic_latest > roic_oldest * 1.05:
                    interp = f"RISING — marginal ROIC > average (avg ΔROIC: +{avg_annual_delta_pp:.1f}pp/year over {n_periods} years)"
                elif roic_latest < roic_oldest * 0.95:
                    interp = f"FALLING — marginal ROIC < average (avg ΔROIC: {avg_annual_delta_pp:.1f}pp/year over {n_periods} years)"
                else:
                    interp = f"STABLE — marginal ROIC ≈ average (avg ΔROIC: {avg_annual_delta_pp:+.1f}pp/year)"
                lines.append(f"ROIC trend interpretation (use in §5): {interp}")
        if financials.total_debt is not None:
            lines.append(f"Total Debt: {_fmt_usd(financials.total_debt.value)}")
        if financials.cash_and_equivalents is not None:
            lines.append(f"Cash + STI: {_fmt_usd(financials.cash_and_equivalents.value)}")

        # Capital returns (ADR-023, EC-19) — central para tesis value investing.
        # MUST aparecer en §4 Financial Quality del Expert.
        if financials.buybacks or financials.dividends:
            buyback_latest = financials.buybacks[0] if financials.buybacks else None
            dividend_latest = financials.dividends[0] if financials.dividends else None
            buyback_v = buyback_latest.value if buyback_latest else 0.0
            dividend_v = dividend_latest.value if dividend_latest else 0.0
            total_returned = buyback_v + dividend_v
            fcf_latest = financials.free_cash_flow[0].value if financials.free_cash_flow else 0.0
            pct_of_fcf = (total_returned / fcf_latest * 100) if fcf_latest > 0 else None
            period = (buyback_latest or dividend_latest).period
            parts = []
            if buyback_v > 0:
                parts.append(f"buybacks {_fmt_usd(buyback_v)}")
            if dividend_v > 0:
                parts.append(f"dividends {_fmt_usd(dividend_v)}")
            pct_str = f" = {pct_of_fcf:.0f}% of FCF" if pct_of_fcf is not None else ""
            lines.append(
                f"Capital returned to shareholders ({period}): "
                f"{_fmt_usd(total_returned)} ({', '.join(parts)}){pct_str}"
            )

        # Gross margin trend — SIEMPRE surfaceado (Fix B / EC-22).
        # Antes solo se emitía con compresión 3 años; ahora se emite siempre que haya
        # ≥2 puntos. Esto da al §4 Financial Quality del Expert el dato necesario para
        # razonar sobre la calidad del negocio, independiente de si el trend es positivo
        # o negativo. El warning de compresión se añade adicionalmente cuando aplica.
        if (financials.gross_profit and financials.revenue
                and len(financials.gross_profit) >= 2 and len(financials.revenue) >= 2):
            margins = []
            for gp, rev in zip(financials.gross_profit, financials.revenue):
                if rev.value and rev.value > 0:
                    margins.append((gp.period, gp.value / rev.value))
            if len(margins) >= 2:
                trend_str = " → ".join(f"{m:.1%}" for _, m in reversed(margins[:3]))
                lines.append(f"Gross margin trend (oldest→latest): {trend_str}")
                # Auto-flag de compresión si 3+ años consecutivos descendentes
                if len(margins) >= 3 and all(margins[i][1] < margins[i + 1][1] for i in range(2)):
                    lines.append(
                        f"⚠️ STRUCTURAL MARGIN RISK: gross margin has compressed 3 consecutive years. "
                        f"Elevate to §7 Risks as 'structural margin pressure'."
                    )
        lines.append("")

    # --- Quarterly XBRL snapshot (EC-44/MCE-24) ---
    q_snap = state.get("quarterly_snapshot")  # type: ignore[typeddict-item]
    if q_snap is not None:
        lines.append("## QUARTERLY XBRL (10-Q — deterministic, MORE RECENT than 10-K)")
        lines.append(f"Period: {q_snap.period}")
        lines.append(f"Quarterly Revenue: {_fmt_usd(q_snap.revenue)}")
        if q_snap.operating_income is not None:
            lines.append(f"Quarterly Operating Income: {_fmt_usd(q_snap.operating_income)}")
        if q_snap.gross_profit is not None:
            lines.append(f"Quarterly Gross Profit: {_fmt_usd(q_snap.gross_profit)}")
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

        # Insider transaction citations — surface evidence verbatim for §3 reconciliation
        # (Fix C / EC-23). El Value Expert §3 MUST tiene regla de comparar precio insider
        # vs precio actual, pero no podía ejecutarse sin los precios. Aquí los exponemos
        # desde las citations validadas (Form 4 sin alteración).
        if mgmt.citations:
            lines.append("Insider transaction evidence (use for §3 price reconciliation):")
            for c in mgmt.citations[:3]:  # top 3 más relevantes
                excerpt = c.excerpt[:400].replace("\n", " ")
                lines.append(f"  - {excerpt}")
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
        # Quarterly earnings press release (EC-44) — datos trimestrales más recientes
        # que el 10-K. Surfacear prominentemente para que Rule #13 se cumpla.
        if market.quarterly_earnings_text:
            lines.append("")
            lines.append(f"## QUARTERLY UPDATE (8-K Item 2.02, filed {market.quarterly_earnings_date or '?'})")
            lines.append("⚠️ This data is MORE RECENT than the 10-K annual report. "
                         "You MUST integrate these figures into §4 Financial Quality "
                         "and §5 Growth conclusions per Rule 13.")
            lines.append(market.quarterly_earnings_text[:2500])
            lines.append("")
        # 8-K material events para §7 Risks (ADR-017 + ADR-019)
        # Item 1.01 con lenguaje M&A recibe bloque propio con texto completo (EC-53):
        # las adquisiciones post-10-K son eventos transformativos que invalidan el
        # balance sheet y mezclan crecimiento orgánico con inorgánico.
        if market.material_events:
            # Separar eventos M&A (Item 1.01 con keywords de adquisición) del resto
            ma_events = []
            other_events = []
            _MA_KEYWORDS = (
                "purchase agreement", "acquisition", "merger agreement",
                "membership interest", "acquired", "business combination",
                "asset purchase",
            )
            for e in market.material_events:
                item_nums = [i.lower() for i in e.get("item_numbers", [])]
                summary_lower = (e.get("summary_text") or "").lower()
                is_ma = (
                    any("1.01" in i for i in item_nums)
                    and any(kw in summary_lower for kw in _MA_KEYWORDS)
                )
                if is_ma:
                    ma_events.append(e)
                else:
                    other_events.append(e)

            # Bloque dedicado para M&A — texto completo, balance sheet staleness warning
            if ma_events:
                lines.append("")
                lines.append("## POST-10-K ACQUISITION DETECTED")
                lines.append(
                    "⚠️ One or more Item 1.01 8-K filings contain M&A activity AFTER the 10-K "
                    "balance sheet date. Per Rule 18: (a) §4 balance sheet data is STALE, "
                    "(b) §5 quarterly revenue growth may include inorganic contribution, "
                    "(c) §6 EV multiples are directional only — true EV may be materially higher."
                )
                for e in ma_events:
                    items_str = ", ".join(e.get("item_numbers", []))
                    # Texto completo del evento (hasta 800 chars — ya filtrado en fetch)
                    lines.append(
                        f"  [{e.get('filing_date', '?')}] Items {items_str}: "
                        f"{e.get('summary_text', '')[:800]}"
                    )
                lines.append("")

            # Resto de eventos (no M&A) — resumen normal de 200 chars
            if other_events:
                lines.append("Material 8-K events:")
                for e in other_events:
                    items_str = ", ".join(e.get("item_numbers", []))
                    lines.append(
                        f"  [{e.get('filing_date', '?')}] Items {items_str}: "
                        f"{e.get('summary_text', '')[:200]}"
                    )
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
                # Serie completa (hasta 5 años) — antes [:3] ocultaba ciclicidad (EC-38)
                own_series = financials.roic_series[:5]
                own_trail = " → ".join(f"{r.value:.1%}" for r in reversed(own_series))
                lines.append(f"  {ticker} (subject): {own_series[0].value:.1%} ({own_series[0].period}) | trend: {own_trail}")
            for pr in competitive.peer_roic_comparison:
                if pr.roic_trail and len(pr.roic_trail) > 1:
                    trail_str = " → ".join(f"{r['roic']:.1%}" for r in reversed(pr.roic_trail))
                    lines.append(f"  {pr.ticker} (peer): {pr.roic:.1%} ({pr.period}) | trend: {trail_str}")
                else:
                    lines.append(f"  {pr.ticker} (peer): {pr.roic:.1%} ({pr.period})")
            # Peer SIC mismatches (EC-42): si algún peer es de industria distinta al subject,
            # la comparación es apples-to-oranges — surface al Expert para que lo refleje en §2.
            sic_mismatches = state.get("peer_sic_mismatches", [])  # type: ignore[typeddict-item]
            if sic_mismatches:
                lines.append("⚠️ PEER QUALITY CAVEAT — SIC industry mismatches detected:")
                for m in sic_mismatches:
                    lines.append(f"  - {m}")
                lines.append("  → In §2 Moat Analysis, explicitly state which peers are NOT apples-to-apples and discount their comparison weight.")
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

        # Bloque DCF GROWTH ASSUMPTION (EC-54) — el parámetro más sensible del DCF debe
        # ser evaluado explícitamente por el Value Expert en §6, no tomado como dato.
        # El nivel de alerta varía según la fuente: user_override = informativo,
        # default_7pct = ⚠️ CAUTION máximo (sin base empírica para esta empresa).
        if valuation.near_term_growth_used_pct is not None:
            g_pct = valuation.near_term_growth_used_pct * 100
            src = valuation.near_term_growth_source or "unknown"

            if src == "user_override":
                growth_block = (
                    f"Near-term FCF growth used: {g_pct:.1f}%\n"
                    f"Source: user-specified override (not auto-calculated)\n"
                    f"Assess in §6 whether this rate is consistent with §5 growth analysis."
                )
            elif src == "default_7pct":
                growth_block = (
                    f"Near-term FCF growth used: {g_pct:.1f}%\n"
                    f"Source: GENERIC FALLBACK — no company-specific historical data available.\n"
                    f"⚠️ CAUTION: The DCF is not anchored to any empirical signal for this company. "
                    f"In §6 you MUST note this limitation explicitly and treat DCF upside as "
                    f"illustrative only. Do NOT present a DCF upside anchored on 7% generic "
                    f"as a solid investment thesis."
                )
            elif "rev_capped" in src:
                n_y = src.split("_")[2].replace("y", "")
                growth_block = (
                    f"Near-term FCF growth used: {g_pct:.1f}%\n"
                    f"Source: FCF CAGR ({n_y}y) capped by revenue CAGR — FCF was growing faster "
                    f"than revenue (unsustainable long-term).\n"
                    f"Assess in §6 whether the revenue growth ceiling is appropriate given "
                    f"the business model and margin trajectory."
                )
            elif src == "revenue_cagr_fallback":
                growth_block = (
                    f"Near-term FCF growth used: {g_pct:.1f}%\n"
                    f"Source: revenue CAGR proxy — FCF CAGR unavailable (reported FCF was "
                    f"negative or insufficient periods).\n"
                    f"⚠️ Less direct signal than FCF CAGR. In §6, assess whether the revenue "
                    f"proxy overstates or understates the realistic FCF growth trajectory "
                    f"(margin expansion vs compression matters here)."
                )
            else:  # fcf_cagr_Ny
                n_y = src.replace("fcf_cagr_", "").replace("y", "")
                growth_block = (
                    f"Near-term FCF growth used: {g_pct:.1f}%\n"
                    f"Source: historical FCF CAGR ({n_y} years of data).\n"
                    f"Assess in §6 whether this historical rate is credible going forward "
                    f"given competitive position, capacity for reinvestment, and quarterly trend."
                )

            lines.append(f"\n## DCF GROWTH ASSUMPTION (use in §6 — most sensitive DCF parameter)")
            lines.append(growth_block)

        lines.append("")

    # --- Peer Relative Valuation context for §6 (MCE-18) ---
    # Provides LLM with peer medians and MF rank so §6 Valuation can answer
    # "is this company cheap or expensive relative to its sector?"
    peer_valuations_ctx = state.get("peer_valuations") or []  # type: ignore[typeddict-item]
    if peer_valuations_ctx:
        lines.append("## PEER RELATIVE VALUATION (use in §6 — relative cheapness vs sector)")

        valid_ev_ebits = [pv.ev_ebit for pv in peer_valuations_ctx if pv.ev_ebit is not None]
        valid_fcf_yields = [pv.fcf_yield_pct for pv in peer_valuations_ctx if pv.fcf_yield_pct is not None]

        # Subject EV/EBIT vs peer median
        valuation_ctx = state.get("valuation")  # type: ignore[typeddict-item]
        if valuation_ctx and valuation_ctx.ev_ebit_multiple and valid_ev_ebits:
            peer_median_ev = statistics.median(valid_ev_ebits)
            direction = "discount" if valuation_ctx.ev_ebit_multiple.value < peer_median_ev else "premium"
            lines.append(
                f"Subject EV/EBIT: {valuation_ctx.ev_ebit_multiple.value:.1f}x "
                f"| Peer median: {peer_median_ev:.1f}x "
                f"-> subject trades at {direction} to peers"
            )

        # Subject FCF yield vs peer median (computed from raw state — not in ValuationReport)
        financials_ctx = state.get("financials")   # type: ignore[typeddict-item]
        market_ctx = state.get("market")           # type: ignore[typeddict-item]
        if (
            financials_ctx and financials_ctx.free_cash_flow
            and market_ctx and market_ctx.market_cap and market_ctx.market_cap.value
            and valid_fcf_yields
        ):
            fcf_val = financials_ctx.free_cash_flow[0].value
            mkt_val = market_ctx.market_cap.value
            if fcf_val > 0 and mkt_val > 0:
                subj_fcf_yield = fcf_val / mkt_val * 100
                peer_median_fcf = statistics.median(valid_fcf_yields)
                direction_fcf = "above" if subj_fcf_yield > peer_median_fcf else "below"
                lines.append(
                    f"Subject FCF yield: {subj_fcf_yield:.1f}% "
                    f"| Peer median: {peer_median_fcf:.1f}% "
                    f"-> subject FCF yield is {direction_fcf} peer median"
                )

        # MF rank
        if valuation_ctx and valuation_ctx.magic_formula_rank is not None:
            valid_pvs = [pv for pv in peer_valuations_ctx if pv.mf_rank is not None]
            total = len(valid_pvs) + 1
            lines.append(
                f"Subject MF rank: #{valuation_ctx.magic_formula_rank} of {total} "
                f"(Greenblatt earnings yield + ROIC — #1 = best)"
            )

        # Warnings for excluded peers
        excluded = [pv.ticker for pv in peer_valuations_ctx if pv.no_market_data]
        ebit_excluded = [pv.ticker for pv in peer_valuations_ctx if not pv.no_market_data and pv.ev_ebit is None]
        for t in excluded:
            lines.append(f"[WARNING] {t} excluded from MF rank — no market data available")
        for t in ebit_excluded:
            lines.append(f"[WARNING] {t} excluded from MF rank — no valid EBIT for EV/EBIT computation")

        lines.append("")

    # --- Historical CAGR anchor + user's growth justification (ADR-019) ---
    cagr = state.get("historical_cagr_5y")        # type: ignore[typeddict-item]
    cagr_years = state.get("historical_cagr_years", 5)  # type: ignore[typeddict-item]
    if cagr is not None:
        lines.append("## HISTORICAL CONTEXT (for §6 Valuation)")
        lines.append(f"Revenue {cagr_years}y CAGR (from XBRL, last {cagr_years} fiscal years): {cagr:.1%}")
        # Qualifier para periodos atípicos (EC-47): un CAGR de 2-3 años puede incluir
        # distorsiones (COVID recovery, one-time contract, acquisition spike). El LLM
        # debe saber cuándo el anchor es menos confiable.
        qualifiers = []
        if cagr_years < 4:
            qualifiers.append(
                f"⚠️ SHORT SPAN: only {cagr_years} years of data — CAGR may not represent "
                f"normalized growth. Weight it less heavily than a full 5-year trend."
            )
        if cagr > 0.40:
            qualifiers.append(
                "⚠️ EXTREME GROWTH: CAGR >40% is likely unsustainable or distorted by "
                "acquisition/recovery. Use a conservative reversion-to-mean assumption."
            )
        elif cagr < 0:
            qualifiers.append(
                "⚠️ NEGATIVE CAGR: revenue has been shrinking. Verify if this is secular "
                "decline or cyclical trough before anchoring the DCF."
            )
        for q in qualifiers:
            lines.append(q)
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

        # Sizing discipline post-hoc clamp (Fix D / EC-24): defensa en profundidad.
        # Aunque el system prompt instruye "max sizing ≤ confidence × 10", el LLM puede
        # ignorar la regla. Aquí clampamos programáticamente para que el output siempre
        # respete la consistencia entre sizing y confidence.
        sizing_ceiling = confidence * 10.0
        if sizing > sizing_ceiling:
            log.info(
                "value_expert_sizing_clamped",
                ticker=ticker,
                original_sizing=sizing,
                clamped_to=sizing_ceiling,
                confidence=confidence,
            )
            sizing = sizing_ceiling

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


def _thesis_break_self_check(
    report: ValueExpertReport, context: str
) -> ValueExpertReport:
    """
    Verifica que el thesis-breaker de §9 no sea ya verdadero en los datos (EC-45).

    Heurística: busca en decision_frame patrones como "thesis breaks if X declines"
    y luego verifica si financial_quality, risks, o growth_reinvestment ya describen X
    como ocurriendo. Si detecta contradicción, loguea warning y añade caveat.

    No modifica el verdict — eso sería overreach. Solo flagea la inconsistencia
    para que el Critic y el usuario puedan evaluarla.
    """
    df = (report.decision_frame or "").lower()
    if not df or len(df) < 20:
        return report

    # Frases que indican condiciones negativas en el thesis-breaker
    negative_condition_phrases = [
        "margin decline", "margin compress", "margin below", "margin fall",
        "revenue decline", "revenue drop", "revenue fall", "revenue below",
        "guidance cut", "guidance withdraw", "guidance lower",
        "backlog decline", "backlog fall", "backlog shrink",
        "debt increase", "leverage increase", "debt exceed",
        "loss widen", "net loss", "negative earnings",
        "cash burn", "negative fcf", "fcf negative",
    ]

    # Secciones del propio LLM que describen la realidad actual
    own_text = " ".join(filter(None, [
        report.financial_quality,
        report.risks,
        report.growth_reinvestment,
    ])).lower()

    if not own_text:
        return report

    # Buscar overlap: ¿el thesis-breaker menciona una condición que las secciones
    # ya describen como ocurriendo?
    triggered = []
    for phrase in negative_condition_phrases:
        words = phrase.split()
        # Cada palabra del phrase debe aparecer en decision_frame
        if all(w in df for w in words):
            # Y al menos 2 de las palabras deben aparecer en las secciones propias
            matches_in_own = sum(1 for w in words if w in own_text)
            if matches_in_own >= 2:
                triggered.append(phrase)

    if triggered:
        log.warning(
            "thesis_break_already_true",
            ticker=report.ticker,
            triggered_phrases=triggered[:3],
            verdict=report.verdict,
        )
        caveat = (
            f"\n\n⚠️ THESIS-BREAK SELF-CHECK WARNING: the conditions "
            f"[{', '.join(triggered[:3])}] appear to be ALREADY TRUE based on "
            f"the analysis above. This may indicate the thesis is already broken "
            f"and the verdict should be reassessed."
        )
        report.decision_frame = (report.decision_frame or "") + caveat

    return report


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
    # response_format=json_object forces the model to produce valid JSON and
    # prevents mid-JSON truncation (gpt-5.4-mini sometimes stops early otherwise).
    response_text = get_client().complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        model=settings.openai_expert_model,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )

    report = _parse_response(response_text, ticker)

    # Retry once if parse failed (defense-in-depth against model quirks).
    if report.verdict_confidence == 0.0 and report.decision_frame and "[Parse error" in report.decision_frame:
        log.warning("value_expert_retry", ticker=ticker, reason="parse_failed_first_attempt")
        response_text = get_client().complete(
            messages=[{"role": "user", "content": user_prompt}],
            system=_SYSTEM_PROMPT,
            model=settings.openai_expert_model,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        report = _parse_response(response_text, ticker)

    # --- Thesis-break self-check (EC-45) — defense-in-depth ---
    # Detect if the thesis-breaker mentions a condition that the LLM's own
    # sections already describe as true. This is a heuristic — not perfect,
    # but catches obvious contradictions like MRCY ("thesis breaks if margin
    # declines" when §4 already says margins are declining).
    report = _thesis_break_self_check(report, context)

    log.info(
        "value_expert_complete",
        ticker=ticker,
        verdict=report.verdict,
        confidence=report.verdict_confidence,
        sizing_pct=report.suggested_sizing_pct,
    )

    return {"synthesis": report}
