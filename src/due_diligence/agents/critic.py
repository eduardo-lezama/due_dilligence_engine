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

    El critic recibe: el VERDICT del Expert + los datos crudos clave.
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
            roic_str = ", ".join(f"{r.period}: {r.value:.1%}" for r in financials.roic_series[:3])
            lines.append(f"ROIC: {roic_str}")
        if financials.free_cash_flow:
            fcf_trend = [f"{f.period}: ${f.value/1e9:.1f}B" for f in financials.free_cash_flow[:3]]
            lines.append(f"FCF trend: {', '.join(fcf_trend)}")
        if financials.total_debt is not None and financials.cash_and_equivalents is not None:
            net_debt = financials.total_debt.value - financials.cash_and_equivalents.value
            lines.append(f"Net debt: ${net_debt/1e9:.1f}B")
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

    lines.append("Now identify the weaknesses. Be blunt. Return valid JSON only.")
    return "\n".join(lines)


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
