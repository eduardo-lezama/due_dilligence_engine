"""
Critic Agent — contra-tesis y pre-mortem del análisis.

Qué hace este módulo:
    Implementa el nodo LangGraph `critic_agent` que genera deliberadamente
    el argumento contrario al VERDICT del value_expert. Es un agente de
    "red team" que asume que el análisis puede estar equivocado y busca
    las razones por las que fallaría.

Por qué un agente separado y no el mismo Expert:
    El value_expert tiene sesgo de confirmación estructural — está diseñado
    para sintetizar y concluir. Si se le pide que critique su propio output,
    tiende a minimizar los riesgos. El critic recibe los mismos datos crudos
    pero con instrucciones opuestas: su única misión es encontrar fallos.
    Este es el patrón pre-mortem de Kahneman: "asume que fallamos, ¿por qué?"

Qué NO hace:
    - No emite VERDICT propio (eso es del Expert).
    - No re-calcula números.
    - No invalida el análisis del Expert — lo complementa con la contra-vista.

Checklist cubierto:
    §8.4 Pre-mortem (docs/checklist-value-investing.md)
    §8.2 Consensus vs variant view
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
from due_diligence.schemas.reports import CriticReport
from due_diligence.utils.formatting import fmt_usd

log = structlog.get_logger()

_SYSTEM_PROMPT = """You are a skeptical value investing critic. Your job is NOT to confirm the analyst's view — it is to steelman the opposite case.

You have just read an investment analysis. Your task:
1. Identify the 3-5 most important weaknesses or blind spots in the bull case.
2. Write the strongest possible contra-thesis — the argument a smart short-seller would make.
3. Write a pre-mortem scenario: assume it's 3 years later and the investment thesis failed. What happened?

RULES:
- Be specific. Generic risks ("competition", "macro") are useless without specifics.
- Every weakness must be grounded in the data provided — no invented facts.
- Do not soften weaknesses with "however" or "that said". State them bluntly.
- Do not repeat the bull case. The analyst already made it.
- Return ONLY valid JSON. No markdown, no preamble.

OUTPUT SCHEMA:
{
  "weaknesses": [
    "<specific weakness 1>",
    "<specific weakness 2>",
    "<specific weakness 3>"
  ],
  "contra_thesis": "<2-3 sentences: the strongest argument against the bull case>",
  "pre_mortem_scenario": "<2-3 sentences: 3 years later, the thesis failed because...>"
}"""


def _build_critic_prompt(state: AnalysisState) -> str:
    """
    Construye el prompt para el critic con el contexto relevante.

    El critic recibe: el VERDICT del Expert + los datos crudos clave + flags
    estructurados detectados por el sistema (MCE-23). Los flags son señales
    cuantitativas que el critic puede usar como munición concreta para el
    contra-argumento sin tener que deducirlas del texto del Expert.

    No recibe el razonamiento completo del Expert para evitar anclaje.
    """
    ticker = state["ticker"]
    lines = [f"# Investment Analysis to Critique: {ticker}\n"]

    synthesis = state.get("synthesis")  # type: ignore[typeddict-item]
    if synthesis is not None and not synthesis.stub:
        lines.append(f"## EXPERT VERDICT: {synthesis.verdict} "
                     f"(confidence: {synthesis.verdict_confidence:.0%}, "
                     f"suggested sizing: {synthesis.suggested_sizing_pct:.0f}%)")
        if synthesis.business_understanding:
            lines.append(f"\nBull case summary: {synthesis.business_understanding}")
        if synthesis.moat_analysis:
            lines.append(f"Moat: {synthesis.moat_analysis}")
        if synthesis.valuation_section:
            lines.append(f"Valuation: {synthesis.valuation_section}")
        lines.append("")

    financials = state.get("financials")  # type: ignore[typeddict-item]
    if financials is not None:
        lines.append("## KEY FINANCIALS (for grounding weaknesses)")
        if financials.roic_series:
            # Serie completa (hasta 5 años) — para que el Critic vea ciclicidad y picos (EC-38)
            roic_str = ", ".join(f"{r.period}: {r.value:.1%}" for r in financials.roic_series[:5])
            lines.append(f"ROIC: {roic_str}")
        if financials.free_cash_flow:
            fcf_trend = [f"{f.period}: {fmt_usd(f.value)}" for f in financials.free_cash_flow[:3]]
            lines.append(f"FCF trend: {', '.join(fcf_trend)}")
        if financials.total_debt is not None and financials.cash_and_equivalents is not None:
            net_debt = financials.total_debt.value - financials.cash_and_equivalents.value
            lines.append(f"Net debt: {fmt_usd(net_debt)}")
        lines.append("")

    filings = state.get("filings")  # type: ignore[typeddict-item]
    if filings is not None and not filings.stub and filings.risk_factors_summary:
        lines.append("## DECLARED RISK FACTORS (from 10-K)")
        lines.append(filings.risk_factors_summary)
        lines.append("")

    valuation = state.get("valuation")  # type: ignore[typeddict-item]
    if valuation is not None and not valuation.stub:
        lines.append("## VALUATION DATA")
        if valuation.reverse_dcf_implied_growth is not None:
            lines.append(f"Implied growth in current price: {valuation.reverse_dcf_implied_growth:.1%}")
        if valuation.dcf_upside_pct is not None:
            lines.append(f"DCF upside: {valuation.dcf_upside_pct:+.1f}%")
        if valuation.ev_fcf_multiple is not None:
            lines.append(f"EV/FCF: {valuation.ev_fcf_multiple.value:.1f}x")
        lines.append("")

    # MCE-23: Detected flags — señales cuantitativas que el sistema ha precalculado
    # para que el Critic no tenga que deducirlas del texto narrativo del Expert.
    # Cada flag es accionable: el Critic puede usar el flag como pivote del contra-argumento.
    flags = _detect_red_flags(state)
    if flags:
        lines.append("## DETECTED FLAGS (use these as pivots for the contra-thesis)")
        for f in flags:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("Now identify the weaknesses. Be blunt. Return valid JSON only.")
    return "\n".join(lines)


def _detect_red_flags(state: AnalysisState) -> list[str]:
    """
    Detecta señales rojas estructuradas que el Critic debe considerar (MCE-23).

    Cada flag es una observación cuantitativa derivada del state — no opinión.
    El Critic los recibe como pivotes concretos para construir su contra-argumento.

    Flags actuales:
        1. DCF contradiction (forward vs reverse divergen >5pp)
        2. Capital returns >80% del FCF (empresa madura sin reinversión)
        3. Margin compression 3+ años consecutivos
        4. Net debt creciente vs FCF estable (palanca financiera sin growth)
    """
    flags: list[str] = []

    # Flag 1: DCF contradiction (ya existe el cálculo en valuation.py)
    valuation = state.get("valuation")  # type: ignore[typeddict-item]
    if valuation is not None and not valuation.stub and valuation.dcf_reliability_note:
        if "contradiction" in valuation.dcf_reliability_note.lower():
            flags.append(
                "DCF FORWARD/REVERSE CONTRADICTION: the bull case DCF upside is "
                "conditional on growth assumptions the market does not currently price. "
                "Use this to challenge the upside as artifact, not opportunity."
            )

    financials = state.get("financials")  # type: ignore[typeddict-item]

    # Flag 2: capital returns >80% del FCF → empresa madura, sin reinversión
    if financials is not None:
        if financials.free_cash_flow and (financials.buybacks or financials.dividends):
            fcf_v = financials.free_cash_flow[0].value
            buyback_v = financials.buybacks[0].value if financials.buybacks else 0.0
            dividend_v = financials.dividends[0].value if financials.dividends else 0.0
            total_returned = buyback_v + dividend_v
            if fcf_v > 0:
                pct = total_returned / fcf_v * 100
                if pct >= 80:
                    flags.append(
                        f"HIGH CAPITAL RETURN ({pct:.0f}% of FCF): the company returns nearly all "
                        f"its cash to shareholders. Bull narrative may frame this as 'shareholder-friendly' "
                        f"but a contrarian view: it signals limited reinvestment opportunities — "
                        f"a mature/declining business with no growth runway. Challenge the growth thesis."
                    )

    # Flag 3: margin compression 3+ años consecutivos
    if financials is not None and financials.gross_profit and financials.revenue:
        if len(financials.gross_profit) >= 3 and len(financials.revenue) >= 3:
            margins = []
            for gp, rev in zip(financials.gross_profit, financials.revenue):
                if rev.value > 0:
                    margins.append(gp.value / rev.value)
            # margins[0] = most recent; descendente = compresión
            if len(margins) >= 3 and all(margins[i] < margins[i + 1] for i in range(2)):
                trend = " → ".join(f"{m:.1%}" for m in reversed(margins[:3]))
                flags.append(
                    f"STRUCTURAL MARGIN COMPRESSION: gross margin has fallen 3 consecutive years "
                    f"({trend}). This is not cyclical noise — it suggests pricing power erosion or "
                    f"input cost pressure that management cannot pass through. Use to challenge "
                    f"any moat claim the bull case makes."
                )

    # Flag 4: insider sale price gap — si hay management report con citations
    # que reporten precios de venta y el precio actual ha caído sustancialmente
    mgmt = state.get("mgmt")  # type: ignore[typeddict-item]
    market = state.get("market")  # type: ignore[typeddict-item]
    if (mgmt is not None and not mgmt.stub
            and market is not None and not market.stub
            and market.price_current is not None
            and market.price_52w_high is not None):
        # Heurístico: si el precio actual está a <60% del 52w high
        # Y hay insider transaction pattern reportado — value trap signal candidate
        ratio = market.price_current.value / market.price_52w_high.value if market.price_52w_high.value > 0 else 1.0
        if ratio < 0.60 and mgmt.insider_transaction_pattern:
            drop_pct = (1 - ratio) * 100
            flags.append(
                f"PRICE COLLAPSE + INSIDER ACTIVITY ({drop_pct:.0f}% from 52w high): "
                f"the stock has fallen materially while insider activity is reported. "
                f"Cross-check: did insiders sell at higher prices before the drop? "
                f"If yes, this is a value trap signal, not a contrarian opportunity. "
                f"Challenge the bull case on this directly."
            )

    return flags


def _parse_response(response_text: str, ticker: str) -> CriticReport:
    """
    Parsea la respuesta JSON del LLM en un CriticReport.

    En caso de fallo, preserva el texto crudo en contra_thesis para
    que el output no se pierda. El critic nunca bloquea el pipeline.
    """
    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object in response")

        data: dict[str, Any] = json.loads(response_text[start:end])

        weaknesses = data.get("weaknesses", [])
        if not isinstance(weaknesses, list):
            weaknesses = []
        # Asegurar que son strings
        weaknesses = [str(w) for w in weaknesses if w]

        return CriticReport(
            ticker=ticker,
            stub=False,
            weaknesses=weaknesses,
            contra_thesis=data.get("contra_thesis"),
            pre_mortem_scenario=data.get("pre_mortem_scenario"),
        )

    except Exception as e:
        log.error(
            "critic_parse_failed",
            ticker=ticker,
            error=str(e),
            response=response_text[:200],
        )
        return CriticReport(
            ticker=ticker,
            stub=False,
            weaknesses=[],
            contra_thesis=f"[Parse error] {response_text[:300]}",
        )


def critic_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: genera la contra-tesis y pre-mortem del análisis.

    Usa settings.openai_expert_model (mismo que el value_expert) para
    que la calidad del argumento contrario sea comparable al bull case.

    Returns:
        {"critique": CriticReport(stub=False, weaknesses=[...], contra_thesis=...)}
    """
    ticker = state["ticker"]
    log.info("critic_agent_start", ticker=ticker)

    user_prompt = _build_critic_prompt(state)

    log.info("critic_agent_llm_call", ticker=ticker, model=settings.openai_expert_model)
    response_text = get_client().complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        model=settings.openai_expert_model,
        max_tokens=1024,
    )

    report = _parse_response(response_text, ticker)

    log.info(
        "critic_agent_complete",
        ticker=ticker,
        weaknesses=len(report.weaknesses),
    )

    return {"critique": report}
