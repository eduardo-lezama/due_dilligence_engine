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

ADR-032 — Structured outputs y fail loud:
    La salida se pide con LLMClient.structured(ValueExpertLLMOutput). Si el LLM falla
    (truncación, proveedor, schema dos veces) el report es stub=True + error y SIN
    verdict: nunca un HOLD de relleno (EC-68). Solo LLMSchemaError se reintenta una vez
    (es estocástico); truncación y proveedor no mejoran repitiendo la misma llamada.

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

import re
import statistics

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.agents.archiver import collect_failures
from due_diligence.agents.llm_support import error_label, model_used
from due_diligence.llm.client import get_client
from due_diligence.llm.errors import LLMError, LLMSchemaError
from due_diligence.schemas.llm_outputs import ThesisBreakCheck, ValueExpertLLMOutput
from due_diligence.schemas.reports import ValueExpertReport

from due_diligence.utils.formatting import fmt_usd as _fmt_usd
from due_diligence.utils.periods import align_by_period

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
14. OUTPUT FORMAT: the response format is enforced by the API schema. Put each section's content in its matching field (see SECTION GUIDANCE below); never leave verdict_rationale empty.
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
    (e) State the 3-stage split used (e.g. "5y constant growth at X%, then 5y fade to terminal Y%") so the reader understands the model structure.

21. VALUATION CONVERGENCE (§6): the context provides a "## VALUATION METHODS CONVERGENCE" block with 4 methods classified as cheap/fair/expensive. You MUST:
    (a) Count the signals and state the convergence explicitly: "3 of 4 methods signal cheap", not just describe each individually.
    (b) If 3 or 4 methods classify as "cheap" → state that conviction is high and explain why the convergence is meaningful.
    (c) If methods diverge (e.g. DCF cheap but EPV expensive) → adopt HOLD bias by default. Explain in §6 which method you weight most and WHY — e.g. "EPV is the more conservative anchor here because the DCF relies on a growth rate the market is not pricing."
    (d) EPV is the zero-growth floor: if EPV per share > price (EPV upside > 0%), state explicitly that a BUY does NOT require growth assumptions to hold — the margin of safety exists in the static business alone. This is the highest-conviction BUY signal.
    (e) Never ignore the convergence count. A sentence like "DCF shows 35% upside" with no mention of EPV or reverse DCF is an incomplete §6.

22. ROIC TREND & INCREMENTAL (§4+§5): the ROIC trend label and the incremental ROIC figure are computed deterministically; narrative MUST be consistent with both; if incremental ROIC is n/m, say so and do not infer reinvestment quality. The 'ROIC trend interpretation' label (RISING, FALLING, or STABLE) is AUTHORITATIVE — you MUST NOT use language that contradicts it:
    - RISING label → do NOT write "declining ROIC", "eroding ROIC", "deteriorating ROIC", "falling ROIC", or "moat is eroding due to ROIC decline". Even if the absolute ROIC level is still negative, a trend from -28% to +5% is an unambiguous improvement and MUST be described as such. Use language like "ROIC recovering toward positive territory" or "marginal returns improving."
    - FALLING label → do NOT describe ROIC as "improving", "recovering", or "strengthening".
    - STABLE label → use neutral language about ROIC.
    Critical distinction: absolute ROIC level (negative = poor quality capital allocation) vs ROIC direction (RISING = marginal capital is earning improving returns). A company going from -28% to +5% ROIC is at a potential inflection point — not in erosion. Describing RISING trend as "eroding moat" is a category error that produces incorrect verdicts with high spurious confidence.
23. GOVERNANCE RED FLAGS (§3+§7+§9): when context contains a '## GOVERNANCE RED FLAGS' block, §7 MUST list each flag explicitly, §3 MUST assess its implication for management trust, and §9 MUST state whether any flag is a thesis-breaker.
24. INVESTOR HYPOTHESES: the '## INVESTOR'S OWN HYPOTHESES' block is the user's opinion. In §8 Psychological Lens, evaluate each hypothesis against the evidence, label it SUPPORTED / CONTRADICTED / UNTESTABLE, and never cite it as evidence elsewhere.

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

SECTION GUIDANCE (content of each field — the format is enforced by the schema):
- business_understanding: §1: 3-4 sentences. What does it do, how does it make money, circle of competence
- moat_analysis: §2: integrate ROIC evidence with qualitative moat classification. If peer ROIC data is available in context, explicitly compare the subject's ROIC against peers — name the numbers. If they conflict with the moat label, adjudicate with reasoning.
- management_assessment: §3: skin in the game (insider %) + capital allocation track record + earnings call Q&A tone if available. Quote one verbatim phrase from transcripts if available. MUST reconcile insider sale prices vs current market price when both are in context — name the gap explicitly. MUST classify insider buying by TOTAL DOLLAR MAGNITUDE per rule 11: token (<$50K) is cosmetic, NOT conviction. ABSENCE of CEO/CFO open-market buying during drawdown >30% is itself a negative signal.
- financial_quality: §4: FCF conversion, balance sheet, margin trends, earnings quality. If 'Capital returned to shareholders' is in context, you MUST report it explicitly: how much was returned, what % of FCF, split between buybacks/dividends. Capital allocation is core to value investing thesis. If quarterly news data is more recent than 10-K, update conclusions accordingly (rule 13).
- growth_reinvestment: §5: reinvestment rate (capex/OCF — MUST report the precomputed value if available) + ROIC trend label + the precomputed incremental ROIC (ΔNOPAT/ΔIC; if n/m say so — rule 22). State whether the company is in invest/balanced/harvest mode AND whether marginal returns are positive or negative based on the incremental ROIC figure. Integrate quarterly news data when more recent than 10-K (rule 13).
- valuation_section: §6: MUST contrast reverse-DCF implied growth vs historical 5y revenue CAGR. State the spread and what it implies about market expectations.
- risks: §7: include any 8-K material events (especially Item 4.02) PLUS top risks from 10-K Item 1A. MUST cross-check news flow + insider activity + margin trends — if multiple signals point negative, name the value trap pattern explicitly. MUST analyze named risks PER SEGMENT when company is multi-segment (rule 12) — same tariff/regulatory/commodity risk often has opposite signs across segments. MUST integrate any quarterly news data more recent than the 10-K (Q1/Q2/Q3 metrics, guidance changes) and weigh them in the verdict, not just mention as factoids (rule 13).
- psychological_lens: §8: why might this be mispriced? Contrarian thesis or consensus trap?
- decision_frame: §9: MUST state the thesis-breaker as a concrete observable condition. No vague placeholders.
- verdict: one of BUY, HOLD, SELL
- verdict_confidence: 0.0 to 1.0
- suggested_sizing_pct: 0.0 to 20.0
- verdict_rationale: 2-3 sentences: WHY this verdict. Reference specific numbers and name the key adjudicated conflict that drove the decision.
"""


# Tope determinista de confianza cuando falta evidencia esencial (ADR-032, revisión
# funcional H1). Igual que el clamp de sizing: el prompt no basta, se impone en código.
CONFIDENCE_CAP_DATA_GAPS = 0.6
# Tope por governance verificado (T4a, regla de dominio confirmada por Eduardo — R1):
# una investigación SEC activa o una material weakness en ICFR con cita verificada
# impiden una convicción alta, diga lo que diga el LLM.
CONFIDENCE_CAP_GOVERNANCE = 0.6
# Palabras que §7 (risks) debe contener para cada flag verificado (check determinista
# de la regla 23; no se re-genera, solo se añade un caveat visible — H2 decide más).
_GOVERNANCE_FLAG_TERMS = {
    "active SEC investigation": ("sec investigation", "sec inquiry", "sec subpoena", "sec enforcement",
                                 "securities and exchange commission"),
    "material weakness": ("material weakness",),
}
# Especialistas sin los que el VERDICT no puede tener convicción alta: §1/§7 (filings)
# y §3 (management) no se pueden reconstruir desde los números.
_ESSENTIAL_SPECIALISTS = (("filings", "Filings"), ("mgmt", "Management"))


def _missing_essentials(state: AnalysisState) -> list[str]:
    """Etiquetas de los especialistas esenciales ausentes, stub o fallidos."""
    missing = []
    for key, label in _ESSENTIAL_SPECIALISTS:
        rep = state.get(key)  # type: ignore[literal-required]
        if rep is None or getattr(rep, "stub", True):
            missing.append(label)
    return missing


def _data_gaps(state: AnalysisState) -> list[tuple[str, str]]:
    """(agente, motivo) de cada hueco: fallos explícitos + esenciales ausentes sin error."""
    gaps = list(collect_failures(state))
    failed = {label for label, _ in gaps}
    for label in _missing_essentials(state):
        if label not in failed:
            gaps.append((label, "report not available"))
    return gaps


def _format_data_gaps(state: AnalysisState) -> list[str]:
    """Bloque DATA GAPS al principio del contexto: el Expert sabe qué le falta antes de leer."""
    gaps = _data_gaps(state)
    if not gaps:
        return []
    lines = ["## DATA GAPS (specialists that failed or are missing — their evidence is ABSENT)"]
    lines += [f"- {label}: {err}" for label, err in gaps]
    lines.append("Do not infer the content of missing sections. State explicitly in the affected "
                 f"sections that evidence is missing. Confidence is capped at {CONFIDENCE_CAP_DATA_GAPS:.0%} "
                 "when Filings or Management are missing.")
    return lines + [""]


# Etiquetas que §8 debe usar para cada hipótesis del inversor (regla 24).
_HYPOTHESIS_LABEL_RE = re.compile(r"\b(SUPPORTED|CONTRADICTED|UNTESTABLE)\b")


def _investor_hypotheses(state: AnalysisState) -> tuple[list[tuple[str, str]], str]:
    """(pares (pregunta, respuesta) no vacíos del checkpoint 1, growth_justification del cp2)."""
    cp1 = state.get("checkpoint1_user_input") or {}  # type: ignore[typeddict-item]
    cp2 = state.get("checkpoint2_user_input") or {}  # type: ignore[typeddict-item]
    answers = cp1.get("answers") or {}
    items = [(a.get("question", ""), a["answer"].strip()) for a in answers.values()
             if isinstance(a, dict) and (a.get("answer") or "").strip()]
    gj = (cp2.get("growth_justification") or "").strip()
    return items, gj


def _format_investor_hypotheses(state: AnalysisState) -> list[str]:
    """Respuestas socráticas (cp1, ADR-018) y growth_justification (cp2) en un bloque aislado.

    Investigación ("Price of Agreement", ICLR 2026): mezclar la opinión del usuario con la
    evidencia induce sicofancia. Va al final, separada y etiquetada como hipótesis. Riesgo
    conocido: el sesgo de recencia puede aumentar su peso; solo el eval de H2-1 lo mide
    (por eso T0b congela la línea base antes de este cambio).
    """
    items, gj = _investor_hypotheses(state)
    if not items and not gj:
        return []
    lines = ["## INVESTOR'S OWN HYPOTHESES (evaluate critically, do not adopt)",
             "These are the investor's opinions, NOT evidence. Test each against the data above. "
             "Never raise confidence because the investor agrees; state explicitly where the data contradicts them."]
    lines += [f"- Q: {q}\n  Investor: {a}" for q, a in items]
    if gj:
        lines.append(f"- Rationale for DCF growth assumptions: {gj}")
    return lines + [""]


def _check_rule24_lens(lens: str | None, state: AnalysisState, ticker: str) -> str | None:
    """Check determinista de la regla 24: §8 debe etiquetar cada hipótesis del inversor.

    Cuenta las etiquetas SUPPORTED/CONTRADICTED/UNTESTABLE en psychological_lens; si hay
    menos que hipótesis, añade un caveat visible (no se re-genera la síntesis).
    """
    items, gj = _investor_hypotheses(state)
    n_hyp = len(items) + (1 if gj else 0)
    if n_hyp == 0:
        return lens
    n_labels = len(_HYPOTHESIS_LABEL_RE.findall(lens or ""))
    if n_labels >= n_hyp:
        return lens
    log.warning("value_expert_rule24_unlabeled_hypotheses", ticker=ticker,
                hypotheses=n_hyp, labels=n_labels)
    caveat = f"⚠️ §8 did not evaluate {n_hyp} investor hypotheses (SUPPORTED/CONTRADICTED/UNTESTABLE)"
    return f"{lens}\n\n{caveat}" if lens else caveat


def _format_context(state: AnalysisState) -> str:
    """
    Serializa todos los reports disponibles en el estado como contexto para el LLM.

    Formato legible por el LLM: secciones etiquetadas con el nombre del report.
    Los campos None se omiten para no contaminar el contexto con "null".
    """
    ticker = state["ticker"]
    lines = [f"# Investment Analysis Context: {ticker}\n"]

    # --- Data gaps (ADR-032): lo primero que ve el Expert ---
    lines += _format_data_gaps(state)

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
            # Tendencia del ROIC (Fix #3 + EC-32): label + delta concreto en pp. Desde T9 la
            # tendencia ya NO se presenta como "ROIC marginal": el ROIC incremental real
            # (ΔNOPAT/ΔIC) va en su propia línea, calculado en parse_financials (EC-85).
            if len(financials.roic_series) >= 2:
                roic_latest = financials.roic_series[0].value
                # Ventana de 5 años (T7): con periods=10 roic_series[-1] sería una tendencia a 10.
                oldest_idx = min(4, len(financials.roic_series) - 1)
                roic_oldest = financials.roic_series[oldest_idx].value
                n_periods = oldest_idx  # 5 puntos → 4 deltas
                avg_annual_delta_pp = (roic_latest - roic_oldest) / n_periods * 100  # en pp
                if roic_latest > roic_oldest * 1.05:
                    interp = f"RISING (avg ΔROIC: +{avg_annual_delta_pp:.1f}pp/year over {n_periods} years)"
                elif roic_latest < roic_oldest * 0.95:
                    interp = f"FALLING (avg ΔROIC: {avg_annual_delta_pp:.1f}pp/year over {n_periods} years)"
                else:
                    interp = f"STABLE (avg ΔROIC: {avg_annual_delta_pp:+.1f}pp/year)"
                lines.append(f"ROIC trend interpretation (use in §5): {interp}")
            # ROIC incremental real (regla 22): ΔNOPAT/ΔIC frente al ROIC medio de la misma ventana.
            window = financials.roic_series[:5]
            avg_roic = sum(r.value for r in window) / len(window)
            if financials.incremental_roic is not None:
                inc = financials.incremental_roic
                lines.append(f"Incremental ROIC (ΔNOPAT/ΔIC, {inc.period}): {inc.value:.1%} "
                             f"vs average ROIC {avg_roic:.1%}")
            else:
                lines.append("Incremental ROIC: n/m (ΔIC too small or balance history unavailable) "
                             "— do not infer reinvestment quality")
        if financials.total_debt is not None:
            lines.append(f"Total Debt: {_fmt_usd(financials.total_debt.value)}")
        if financials.cash_and_equivalents is not None:
            # Desde H1 (EC-81) cash_and_equivalents es SOLO caja; STI en línea propia.
            lines.append(f"Cash (ex-STI): {_fmt_usd(financials.cash_and_equivalents.value)}")
        if financials.short_term_investments is not None:
            lines.append(f"Short-term investments: {_fmt_usd(financials.short_term_investments.value)}")

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
            # Alineado por periodo (EC-82): zip posicional mezclaba años si una serie tenía hueco.
            for gp, rev in align_by_period(financials.gross_profit, financials.revenue):
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
        # Data quality flags (deterministas, parse_financials): identidades contables rotas,
        # fallbacks de conceptos. El LLM debe tratar las cifras afectadas con cautela, no ignorarlas.
        if financials.data_quality_flags:
            lines.append("## DATA QUALITY FLAGS (deterministic)")
            lines += [f"- {flag}" for flag in financials.data_quality_flags]
            lines.append("Treat figures touched by these flags as lower-confidence in §4 and §6.")
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
        # Etiquetas honestas (T3c, EC-69): cada línea dice de dónde sale el dato.
        # ownership y tenure solo existen si su cita se verificó contra fuente primaria (T3b).
        if mgmt.insider_ownership_pct is not None:
            lines.append(f"Insider ownership (DEF 14A, citation-verified): {mgmt.insider_ownership_pct:.1f}%")
        if mgmt.founder_ceo is not None:
            lines.append(f"Founder-CEO: {'Yes' if mgmt.founder_ceo else 'No'}")
        if mgmt.ceo_tenure_years is not None:
            lines.append(f"CEO tenure (citation-verified): {mgmt.ceo_tenure_years:.0f} years")
        if mgmt.form4_transactions_12m is not None:
            lines.append(f"Form 4 open-market transactions (12m, deterministic): {mgmt.form4_transactions_12m}")
        if mgmt.form4_summary:
            lines.append(f"Form 4 transactions (Form 4, deterministic):\n{mgmt.form4_summary}")
        # Narrativa libre del LLM: antes se etiquetaba "(Form 4)" y presentaba como dato
        # primario una lectura que podía confundir CEO y CFO (caso KBR).
        if mgmt.insider_transaction_pattern:
            lines.append(f"Insider transaction pattern (LLM summary, unverified): {mgmt.insider_transaction_pattern}")
        if mgmt.compensation_structure_notes:
            lines.append(f"Compensation (LLM summary): {mgmt.compensation_structure_notes}")
        if mgmt.capital_allocation_summary:
            lines.append(f"Capital allocation (LLM summary): {mgmt.capital_allocation_summary}")

        # Evidencia verbatim para la reconciliación de precios de §3 (Fix C / EC-23).
        # SOLO citas verificadas (invariante 2): una no verificada no entra en la síntesis.
        verified = [c for c in mgmt.citations if c.verified]
        if verified:
            lines.append("Insider transaction evidence (verified citations; use for §3 price reconciliation):")
            for c in verified[:3]:  # top 3 más relevantes
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
            lines.append(f"MDA highlights (LLM summary): {filings.mda_highlights}")
        if filings.backlog_usd is not None:
            lines.append(f"Contracted backlog: {_fmt_usd(filings.backlog_usd)}")
        if filings.revenue_guidance:
            lines.append(f"Management guidance: {filings.revenue_guidance}")
        lines.append("")

    # EC-66 extraía governance_red_flags pero nadie lo consumía (EC-70). Bloque propio,
    # separado de "Key risks", para que el LLM no lo diluya entre riesgos genéricos (regla 23).
    # Etiqueta "(from 10-K)": filings_agent solo lee Items 1/1A/7; exagerar la fuente
    # contradiría ADR-014.
    if filings is not None and not filings.stub and filings.governance_red_flags:
        lines += ["## GOVERNANCE RED FLAGS (from 10-K)", filings.governance_red_flags]
        verified_flags = [name for name, on in (("active SEC investigation", filings.active_sec_investigation),
                                                ("material weakness in ICFR", filings.material_weakness)) if on]
        if verified_flags:
            lines.append(f"Citation-verified flags: {', '.join(verified_flags)} "
                         f"(confidence is capped at {CONFIDENCE_CAP_GOVERNANCE:.0%} deterministically).")
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
                    "balance sheet date. Per Rule 19: (a) §4 balance sheet data is STALE, "
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
    if valuation is not None and valuation.stub and valuation.error:
        # Error tipado del agente (p.ej. WACC ≤ g): hueco de datos VISIBLE, no ausencia silenciosa.
        lines.append("## VALUATION UNAVAILABLE")
        lines.append(f"{valuation.error}")
        lines.append("§6 MUST state that no deterministic valuation is available; do NOT invent "
                     "intrinsic values, upsides or multiples.")
        lines.append("")
    if valuation is not None and not valuation.stub:
        lines.append("## VALUATION REPORT")
        if valuation.valuation_basis:
            lines.append(f"Valuation basis: {valuation.valuation_basis}")
        # --- Plano enterprise (ADR-033): FCFF → WACC → EV → bridge → por acción ---
        if valuation.fcff_base is not None:
            lines.append(f"FCFF base: {_fmt_usd(valuation.fcff_base.value)} ({valuation.fcff_base.source})")
        if valuation.fcff_breakdown is not None:
            bd = valuation.fcff_breakdown
            lines.append(
                f"FCFF components (latest FY): OCF {_fmt_usd(bd.ocf)} + after-tax interest "
                f"{_fmt_usd(bd.after_tax_interest_addback)} − after-tax interest income "
                f"{_fmt_usd(bd.after_tax_interest_income)} − capex {_fmt_usd(bd.capex)} − SBC "
                f"{_fmt_usd(bd.sbc_charge)} − finance-lease principal {_fmt_usd(bd.finance_lease_principal)} "
                f"= {_fmt_usd(bd.fcff)} (tax {bd.tax_rate:.1%})"
                + (f"; missing inputs: {', '.join(bd.missing_inputs)}" if bd.missing_inputs else "")
            )
        if valuation.suggested_wacc_breakdown:
            lines.append(f"WACC breakdown (suggested, real): {valuation.suggested_wacc_breakdown}")
        if valuation.enterprise_value_dcf is not None:
            lines.append(f"Enterprise value (DCF): {_fmt_usd(valuation.enterprise_value_dcf.value)}")
        if valuation.equity_bridge is not None:
            b = valuation.equity_bridge
            lines.append(
                f"Equity bridge: EV {_fmt_usd(b.enterprise_value)} − debt {_fmt_usd(b.total_debt)} "
                f"− minority {_fmt_usd(b.minority_interest)} − preferred {_fmt_usd(b.preferred_stock)} "
                f"+ cash {_fmt_usd(b.cash)} + STI {_fmt_usd(b.short_term_investments)} "
                f"+ LTI {_fmt_usd(b.long_term_investments)} = equity {_fmt_usd(b.equity_value)} "
                f"(net claims {_fmt_usd(b.net_claims)})"
            )
            if b.operating_lease_liabilities_memo is not None:
                lines.append(
                    f"Operating lease liabilities (memo, not deducted — ASC 842, already in OCF/EBIT): "
                    f"{_fmt_usd(b.operating_lease_liabilities_memo)}"
                )
            if valuation.share_count_used:
                # /1e6 aceptado: nº de acciones (siempre ≥ 1M), no USD (MCE-26)
                lines.append(f"Diluted shares used: {valuation.share_count_used / 1e6:,.2f}M "
                             f"({valuation.share_count_source})")
        if valuation.terminal_value_share is not None:
            lines.append(f"Terminal value share of DCF EV: {valuation.terminal_value_share:.0%}")
            if valuation.terminal_value_share > 0.75:
                lines.append("⚠️ >75% of the DCF value comes from the terminal value — "
                             "§6 must flag terminal-value dependence.")
        if valuation.dcf_intrinsic_value is not None:
            lines.append(f"DCF equity value (after bridge): {_fmt_usd(valuation.dcf_intrinsic_value.value)}")
        if valuation.dcf_intrinsic_per_share is not None:
            lines.append(f"DCF value per share: ${valuation.dcf_intrinsic_per_share.value:.2f}")
        if valuation.dcf_upside_pct is not None:
            lines.append(f"DCF upside vs price: {valuation.dcf_upside_pct:+.1f}%")
        if valuation.reverse_dcf_implied_growth is not None:
            ten_y = (f" (10y-equivalent CAGR {valuation.reverse_dcf_10y_equivalent:.1%})"
                     if valuation.reverse_dcf_10y_equivalent is not None else "")
            lines.append(f"Reverse DCF implied growth (target = market EV): "
                         f"{valuation.reverse_dcf_implied_growth:.1%}{ten_y}")
        if valuation.market_enterprise_value is not None:
            lines.append(f"Market EV (price × shares + net claims): "
                         f"{_fmt_usd(valuation.market_enterprise_value.value)}")
        if valuation.ev_ebit_multiple is not None:
            lines.append(f"EV/EBIT: {valuation.ev_ebit_multiple.value:.1f}x")
        if valuation.ev_fcf_multiple is not None:
            lines.append(f"EV/FCFF: {valuation.ev_fcf_multiple.value:.1f}x")
        if valuation.pe_multiple is not None:
            lines.append(f"P/E: {valuation.pe_multiple.value:.1f}x")
        if valuation.earnings_yield_pct is not None:
            lines.append(f"Earnings yield (EBIT/EV): {valuation.earnings_yield_pct:.1f}%")
        if valuation.epv_per_share is not None:
            epv_upside_str = (
                f" ({valuation.epv_upside_pct:+.1f}% vs price)"
                if valuation.epv_upside_pct is not None else ""
            )
            lines.append(
                f"EPV (zero-growth floor, Greenwald): ${valuation.epv_per_share.value:.2f}/sh"
                f"{epv_upside_str} — source: {valuation.epv_source or 'EBIT median'}"
            )
        if valuation.epv_growth_premium is not None:
            # Lectura Greenwald: el precio por encima del EPV paga crecimiento, que solo vale si hay moat.
            gp = valuation.epv_growth_premium
            reading = (
                f"{gp:.0%} of the price pays for growth — justified only if the moat lets growth "
                f"earn above WACC (cross-check with §2 and incremental ROIC)"
                if gp >= 0 else
                "price is below the no-growth value: margin of safety does not depend on growth"
            )
            lines.append(f"EPV growth premium (1 − EPV/price): {gp:+.0%} — {reading}")
        if valuation.assumptions is not None:
            a = valuation.assumptions
            stage_str = ""
            if valuation.stage1_years_used and valuation.fade_years_used is not None:
                stage_str = (
                    f", 3-stage: {valuation.stage1_years_used}y constant + "
                    f"{valuation.fade_years_used}y fade"
                )
            lines.append(
                f"DCF assumptions: WACC={a.wacc_pct}%, terminal_growth={a.terminal_growth_pct}%, "
                f"years={a.projection_years}{stage_str}"
            )
        if valuation.suggested_wacc_pct is not None:
            lines.append(
                f"Suggested WACC (real: Ke + after-tax Kd, market weights): "
                f"{valuation.suggested_wacc_pct:.1f}%"
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

        # Bloque VALUATION METHODS CONVERGENCE (ADR-030) — tabla estructurada de 4 métodos.
        # Le da al LLM los datos categorizados para que la regla #21 del prompt pueda
        # sintetizar convergencia sin tener que inferirla de números crudos.
        if valuation and valuation.valuation_methods_summary:
            cheap = [r for r in valuation.valuation_methods_summary if r["classification"] == "cheap"]
            expensive = [r for r in valuation.valuation_methods_summary if r["classification"] == "expensive"]
            fair = [r for r in valuation.valuation_methods_summary if r["classification"] == "fair"]
            total = len(valuation.valuation_methods_summary)
            lines.append("\n## VALUATION METHODS CONVERGENCE (use in §6 — read Rule #21)")
            for row in valuation.valuation_methods_summary:
                if row["classification"] != "n/a":
                    lines.append(
                        f"- {row['method']}: **{row['classification'].upper()}** | {row['display_upside']}"
                    )
                else:
                    lines.append(f"- {row['method']}: n/a (insufficient data)")
            lines.append(
                f"\nConvergence: {len(cheap)} cheap, {len(fair)} fair, {len(expensive)} expensive "
                f"out of {total} methods."
            )
            if valuation.epv_upside_pct is not None and valuation.epv_upside_pct > 0:
                lines.append(
                    "EPV note: EPV per share > price — the stock is potentially below zero-growth value. "
                    "A BUY verdict here does NOT require growth assumptions to hold."
                )

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
        # growth_justification ya NO va aquí (T4b, EC-70): mezclada con la evidencia
        # inducía sicofancia y además desaparecía si no había CAGR. Ahora vive en el
        # bloque de hipótesis del inversor, al final.
        lines.append("")

    # --- Hipótesis del inversor: SIEMPRE el último bloque (T4b, regla 24) ---
    lines += _format_investor_hypotheses(state)

    return "\n".join(lines).rstrip() + "\n"


def _to_report(out: ValueExpertLLMOutput, ticker: str,
               state: AnalysisState | None = None) -> ValueExpertReport:
    """Post-validación de negocio sobre la salida ya tipada (ADR-032).

    El Literal del schema ya garantiza BUY/HOLD/SELL; la normalización se mantiene
    como defensa en profundidad. Clamps deterministas: confidence [0,1], sizing [0,20]
    y sizing ≤ confidence×10 (Fix D / EC-24). Con `state`, además, topes de confianza
    deterministas: ≤ 0.6 si falta Filings o Management (DATA GAPS). El sizing se clampa
    DESPUÉS de los topes para que sizing ≤ confidence×10 siga siendo cierto.
    """
    verdict = out.verdict if out.verdict in ("BUY", "HOLD", "SELL") else "HOLD"
    confidence = max(0.0, min(1.0, out.verdict_confidence))
    risks = out.risks
    lens = out.psychological_lens
    if state is not None:
        missing = _missing_essentials(state)
        if missing and confidence > CONFIDENCE_CAP_DATA_GAPS:
            log.info("value_expert_confidence_capped_data_gaps", ticker=ticker,
                     original_confidence=confidence, capped_to=CONFIDENCE_CAP_DATA_GAPS,
                     missing=missing)
            confidence = CONFIDENCE_CAP_DATA_GAPS
        gov_flags = _verified_governance_flags(state)
        if gov_flags and confidence > CONFIDENCE_CAP_GOVERNANCE:
            log.info("value_expert_confidence_capped_governance", ticker=ticker,
                     original_confidence=confidence, capped_to=CONFIDENCE_CAP_GOVERNANCE,
                     flags=gov_flags)
            confidence = CONFIDENCE_CAP_GOVERNANCE
        risks = _check_rule23_risks(risks, gov_flags, ticker)
        lens = _check_rule24_lens(lens, state, ticker)
    sizing = max(0.0, min(20.0, out.suggested_sizing_pct))
    # Sizing discipline post-hoc clamp (Fix D / EC-24): aunque el prompt instruye
    # "max sizing ≤ confidence × 10", el LLM puede ignorarlo; aquí se impone en código.
    ceiling = confidence * 10.0
    if sizing > ceiling:
        log.info("value_expert_sizing_clamped", ticker=ticker, original_sizing=sizing,
                 clamped_to=ceiling, confidence=confidence)
        sizing = ceiling
    return ValueExpertReport(
        ticker=ticker, stub=False,
        business_understanding=out.business_understanding, moat_analysis=out.moat_analysis,
        management_assessment=out.management_assessment, financial_quality=out.financial_quality,
        growth_reinvestment=out.growth_reinvestment, valuation_section=out.valuation_section,
        risks=risks, psychological_lens=lens, decision_frame=out.decision_frame,
        verdict=verdict, verdict_confidence=confidence, suggested_sizing_pct=sizing,
        verdict_rationale=out.verdict_rationale,
    )


def _verified_governance_flags(state: AnalysisState) -> list[str]:
    """Flags de governance con cita verificada en el report de filings (T4a)."""
    filings = state.get("filings")  # type: ignore[typeddict-item]
    if filings is None or filings.stub:
        return []
    flags = []
    if filings.active_sec_investigation:
        flags.append("active SEC investigation")
    if filings.material_weakness:
        flags.append("material weakness")
    return flags


def _check_rule23_risks(risks: str | None, flags: list[str], ticker: str) -> str | None:
    """Check determinista de la regla 23: §7 debe nombrar cada flag verificado.

    Si falta alguno se añade un caveat visible a §7 (no se re-genera la síntesis).
    """
    if not flags:
        return risks
    text = (risks or "").lower()
    missing = [f for f in flags if not any(t in text for t in _GOVERNANCE_FLAG_TERMS[f])]
    if not missing:
        return risks
    log.warning("value_expert_rule23_flag_omitted", ticker=ticker, missing=missing)
    caveats = "\n".join(f"⚠️ §7 omits governance flag: {f}" for f in missing)
    return f"{risks}\n\n{caveats}" if risks else caveats


def _failed_synthesis(ticker: str, e: LLMError) -> ValueExpertReport:
    """Síntesis fallida: stub=True, error explícito y SIN VERDICT.

    Nunca un HOLD de relleno (ADR-009 + ADR-032, EC-68): el archiver nombra el report
    -INCOMPLETE y la CLI muestra el panel rojo.
    """
    log.error("value_expert_failed", ticker=ticker, error_type=type(e).__name__, error=str(e))
    return ValueExpertReport(ticker=ticker, stub=True, error=error_label(e))


def _word_in_text(word: str, text: str) -> bool:
    """
    Comprueba si `word` aparece como palabra completa en `text` (word boundary match).

    Evita falsos positivos por substring: 'fall' no coincide con 'falls', 'falling',
    'fallen'. Sin este check, "revenue fall" (condición futura en thesis-breaker)
    disparaba sobre texto que contenía "revenue falls below 10%" — causa raíz del
    false positive EC-59 (PATH) y EC-45 (CGNX con "margin compress" vs "compressing").
    """
    return bool(re.search(rf'\b{re.escape(word)}\b', text))


def _validate_thesis_break_with_llm(
    decision_frame: str,
    own_text: str,
    triggered_phrases: list[str],
    ticker: str,
) -> bool | None:
    """
    Valida semánticamente si un thesis-breaker Python-triggered es verdadero ahora (ADR-031).

    Solo llamado cuando _word_in_text detecta overlap entre el thesis-breaker (§9) y las
    secciones §4/§5/§7. La distinción clave que regex no puede hacer:
    - "thesis breaks if revenue falls below 10%" (condición FUTURA) vs
    - "revenue fell 8% YoY in Q3 2026" (hecho ACTUAL ya ocurrido)

    Usa modelo barato (settings.openai_validation_model) con salida estructurada
    ThesisBreakCheck (ADR-032). Coste marginal: ~$0.0001 por invocación.

    Returns:
        True  → el LLM confirma que la condición ES ACTUALMENTE VERDADERA.
        False → el LLM dice que es solo un riesgo futuro.
        None  → el validador falló (LLMError). Antes devolvía False, lo que ocultaba el
                fallo como "tesis no rota"; ahora el llamador añade un caveat visible.
    """
    prompt = (
        f"THESIS-BREAKER CONDITION (from §9 of investment analysis):\n"
        f"{decision_frame[:600]}\n\n"
        f"CURRENT ANALYSIS EXCERPTS (§4 Financial Quality, §5 Growth, §7 Risks):\n"
        f"{own_text[:800]}\n\n"
        f"Python pre-filter detected word overlap in: {triggered_phrases}\n\n"
        f"Is the THESIS-BREAKER condition CURRENTLY TRUE based on the excerpts?\n"
        f"Answer true only if the excerpts contain CLEAR EVIDENCE that the condition "
        f"IS ALREADY HAPPENING NOW (past/present tense facts, not future risk).\n"
        f"Answer false if the excerpts only describe it as a FORWARD-LOOKING RISK or "
        f"FUTURE POSSIBILITY. Give a one-sentence reason."
    )
    try:
        check = get_client().structured(
            ThesisBreakCheck,
            messages=[{"role": "user", "content": prompt}],
            system="You are a precise financial analyst.",
            model=settings.openai_validation_model,
            max_tokens=200,
        )
    except LLMError as e:
        log.warning("thesis_break_llm_validation_failed", ticker=ticker,
                    error_type=type(e).__name__, error=str(e))
        return None
    log.info(
        "thesis_break_llm_validation",
        ticker=ticker,
        is_currently_true=check.is_currently_true,
        reason=check.reason,
        triggered_phrases=triggered_phrases,
    )
    return check.is_currently_true


def _thesis_break_self_check(
    report: ValueExpertReport, context: str
) -> ValueExpertReport:
    """
    Verifica que el thesis-breaker de §9 no sea ya verdadero en los datos (EC-45).

    Pipeline de dos etapas (ADR-031):
    1. Python pre-filter con word boundary regex (_word_in_text): rápido, cero coste.
       Elimina falsos positivos por inflexiones ("falls" ≠ "fall", "declining" ≠ "decline").
    2. LLM semantic validation (_validate_thesis_break_with_llm): solo si Python trigger
       dispara. Modelo cheap (gpt-4o-mini) confirma si la condición es ACTUALMENTE verdadera
       vs si es solo un riesgo futuro — distinción semántica que regex no puede hacer.

    No modifica el verdict — eso sería overreach. Solo flagea la inconsistencia para
    que el Critic y el usuario puedan evaluarla.
    """
    df = (report.decision_frame or "").lower()
    if not df or len(df) < 20:
        return report

    # Frases que indican condiciones negativas típicas en un thesis-breaker.
    # Se añaden variantes inflectadas comunes para maximizar recall sin perder precision
    # (la precision la aporta el LLM validator en la segunda etapa).
    negative_condition_phrases = [
        "margin decline", "margin compress", "margin compression", "margin below", "margin fall",
        "revenue decline", "revenue drop", "revenue fall", "revenue below",
        "guidance cut", "guidance withdraw", "guidance withdrawn", "guidance lower",
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

    # Stage 1: Python pre-filter con word boundary matching (EC-59).
    # _word_in_text usa regex \b para que "fall" no matchee "falls" o "falling" —
    # esto elimina el false positive de PATH donde "revenue falls below 10%" (condición
    # futura en §9) disparaba sobre "revenue growth falling toward X%" en §7 (riesgo).
    triggered = []
    for phrase in negative_condition_phrases:
        words = phrase.split()
        if all(_word_in_text(w, df) for w in words):
            matches_in_own = sum(1 for w in words if _word_in_text(w, own_text))
            if matches_in_own >= 2:
                triggered.append(phrase)

    if not triggered:
        return report

    # Stage 2: LLM semantic validation — confirmar antes de añadir el warning (ADR-031).
    # El Python trigger es necesario pero no suficiente. El LLM distingue
    # "condición futura posible" de "hecho actual ya ocurrido".
    confirmed = _validate_thesis_break_with_llm(
        decision_frame=report.decision_frame or "",
        own_text=own_text,
        triggered_phrases=triggered[:3],
        ticker=report.ticker,
    )

    if confirmed is None:
        # Validador caído: el check no se pudo completar. Se dice explícitamente en vez
        # de dar la tesis por no rota (revisión funcional H1, ADR-032).
        report.decision_frame = (report.decision_frame or "") + (
            f"\n\n⚠️ Thesis-break validator unavailable; manual check required "
            f"(pre-filter matched: {', '.join(triggered[:3])})."
        )
        return report

    if not confirmed:
        log.info(
            "thesis_break_python_trigger_llm_rejected",
            ticker=report.ticker,
            triggered_phrases=triggered[:3],
        )
        return report

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
        f"Analyze {ticker} based on the specialist reports below.\n\n"
        f"{context}"
    )

    log.info("value_expert_llm_call", ticker=ticker, model=settings.openai_expert_model)
    # 8192 tokens: 9 secciones con adjudicación de conflictos + thesis-breaker concreto.
    # La truncación ya no es silenciosa: structured() lanza LLMTruncatedError (ADR-032).
    client = get_client()
    out: ValueExpertLLMOutput | None = None
    for attempt in (1, 2):
        try:
            out = client.structured(ValueExpertLLMOutput,
                                    messages=[{"role": "user", "content": user_prompt}],
                                    system=_SYSTEM_PROMPT, model=settings.openai_expert_model,
                                    max_tokens=8192)
            break
        except LLMSchemaError as e:
            # Solo el error de schema justifica reintento: es estocástico. Truncación y
            # proveedor no mejoran repitiendo la misma llamada.
            if attempt == 1:
                log.warning("value_expert_retry", ticker=ticker, reason=str(e))
                continue
            return {"synthesis": _failed_synthesis(ticker, e)}
        except LLMError as e:
            return {"synthesis": _failed_synthesis(ticker, e)}

    report = _to_report(out, ticker, state)
    report = report.model_copy(update={"model_used": model_used(client)})

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
