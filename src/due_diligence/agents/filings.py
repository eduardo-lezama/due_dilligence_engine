"""
Filings Agent — analista de secciones cualitativas del 10-K anual.

Qué hace este módulo:
    Implementa el nodo LangGraph `filings_agent` que cubre las secciones §1
    (Business Understanding) y §7 (Risks & Red Flags) del checklist canónico.
    Lee tres secciones del 10-K más reciente desde EDGAR, construye un prompt
    estructurado, llama al LLM, y devuelve un FilingsRAGReport con citations.

Flujo del nodo:
    1. Fetch 10-K Item 1 (Business) via EdgarFilingsAdapter
    2. Fetch 10-K Item 1A (Risk Factors) via EdgarFilingsAdapter
    3. Fetch 10-K Item 7 (MD&A) via EdgarFilingsAdapter
    4. Prompt con las tres secciones + preguntas exactas de §1 y §7
    5. LLM interpreta y devuelve JSON → FilingsRAGReport(stub=False)
    6. Return {"filings": report} → LangGraph hace merge en AnalysisState

Por qué no usa Tavily (a diferencia de management_agent):
    Las secciones §1 y §7 están completamente cubiertas por el 10-K. La
    empresa está legalmente obligada a declarar todos sus riesgos materiales.
    No hay valor adicional de la web que no esté ya en el filing. Si en fases
    futuras se añade comparación con peers, se reconsiderará (ADR-012).

Por qué imports al nivel de módulo:
    LangGraph usa mocker.patch en tests — los imports deben estar al nivel de
    módulo (no dentro de funciones) para que el patch sea efectivo.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
from due_diligence.schemas.base import Citation
from due_diligence.schemas.reports import FilingsRAGReport
from due_diligence.tools.edgar import EdgarFilingsAdapter

log = structlog.get_logger()

# Instrucciones al LLM: actúa como analista value, responde §1+§7 del checklist,
# devuelve solo JSON con citations explícitas. Sin markdown fuera del JSON.
_SYSTEM_PROMPT = """You are a value investing analyst specializing in SEC filing analysis.
Analyze the provided 10-K sections and answer the checklist questions below.

RULES:
- Base every claim ONLY on the provided 10-K text. Never invent facts.
- For each qualitative claim, include a citation with the section name and an exact excerpt.
- REGULATORY EVENTS: If Item 1A or Item 7 mentions ANY specific government action — entity list addition, export restriction, BIS/OFAC/SEC enforcement, sanctions, going-concern language — report it verbatim in key_regulatory_events with the entity name and date. Do NOT generalize these into "regulatory risk" — name the specific event.
- If information is not available in the provided text, say "Not available in provided sources."
- Return ONLY valid JSON matching the schema below. No markdown, no explanation outside JSON.

OUTPUT SCHEMA:
{
  "business_description": "<string — 3-5 sentences: what the company sells, to whom, how it makes money, key segments>",
  "risk_factors_summary": "<string — 3-5 sentences: most material risks: regulatory, competitive, technology, macro>",
  "mda_highlights": "<string — 3-5 sentences: key revenue/margin drivers, management concerns, notable trends>",
  "backlog_usd": <float or null — contracted backlog in USD if mentioned in Item 7, e.g. 1270000000.0>,
  "revenue_guidance": "<string or null — management's stated guidance for next period, e.g. '$1.1B–$1.2B revenue in 2026, 21-30% growth'>",
  "geographic_revenue_breakdown": "<string or null — revenue split by geography if disclosed, e.g. 'China: 85%, US: 8%, Other: 7%'>",
  "key_regulatory_events": "<string or null — SPECIFIC government actions: entity list additions, export restrictions, enforcement with dates/entities. Null if none found. Example: 'December 2, 2024: BIS added ACM Research (Shanghai) to Entity List, restricting access to US-origin technology.'>",
  "citations": [
    {
      "claim": "<exact claim being cited>",
      "source_url": "<section name: '10-K Item 1', '10-K Item 1A', '10-K Item 7'>",
      "excerpt": "<verbatim excerpt from source>",
      "confidence": <0.0 to 1.0>,
      "gaap_vs_nongaap_flag": null
    }
  ]
}"""


def _build_user_prompt(
    ticker: str,
    business_text: str,
    risk_factors_text: str,
    mda_text: str,
) -> str:
    """
    Construye el prompt de usuario con las tres secciones del 10-K etiquetadas.

    Cada sección está claramente marcada para que el LLM cite correctamente
    la fuente al generar las citations (Item 1 vs Item 1A vs Item 7).
    """
    return f"""# Filings Analysis: {ticker}

## SOURCE 1: 10-K Item 1 — Business Description
{business_text or "Item 1 not available."}

---

## SOURCE 2: 10-K Item 1A — Risk Factors
{risk_factors_text or "Item 1A not available."}

---

## SOURCE 3: 10-K Item 7 — Management Discussion & Analysis (MD&A)
{mda_text or "Item 7 not available."}

---

# CHECKLIST §1 + §7: Business Understanding & Risks

Answer each question using ONLY the sources above:

§1 — Business Understanding:
1. What does the company sell and to whom? Articulate the business model clearly.
2. What are the key revenue segments by PRODUCT and by GEOGRAPHY? What % of revenue comes from China/US/Other if disclosed?
3. What is the company's competitive strategy? What gives it an edge?

§7 — Risks & Red Flags:
4. What are the most material risks? (regulatory, competitive, technology)
5. Has the company been subject to any SPECIFIC government action — entity list, export restriction, BIS/OFAC/SEC action, sanctions — in the last 12-24 months? If so, name the entity, date, and scope VERBATIM from the filing.
6. What does management highlight as key challenges in MD&A?

§ Operational Metrics (from Item 7):
7. Is there a contracted backlog figure? If so, what is it in USD?
8. Does management provide revenue or growth guidance for the next period? Quote it exactly.
9. Are there any accounting changes, going-concern language, or capital raise announcements?

Return valid JSON only."""


def _parse_llm_response(response_text: str, ticker: str) -> FilingsRAGReport:
    """
    Parsea la respuesta JSON del LLM en un FilingsRAGReport validado.

    Si el JSON es inválido, devuelve un reporte parcial con stub=False
    y el texto de error en mda_highlights. Nunca lanza excepción hacia el grafo.
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
                log.debug("filings_citation_parse_failed", ticker=ticker, error=str(e))

        # backlog_usd puede venir como int o float — normalizar
        backlog_raw = data.get("backlog_usd")
        backlog = float(backlog_raw) if backlog_raw is not None else None

        return FilingsRAGReport(
            ticker=ticker,
            stub=False,
            business_description=data.get("business_description"),
            risk_factors_summary=data.get("risk_factors_summary"),
            mda_highlights=data.get("mda_highlights"),
            backlog_usd=backlog,
            revenue_guidance=data.get("revenue_guidance"),
            geographic_revenue_breakdown=data.get("geographic_revenue_breakdown"),
            key_regulatory_events=data.get("key_regulatory_events"),
            citations=citations,
        )

    except Exception as e:
        log.error("filings_llm_parse_failed", ticker=ticker, error=str(e), response=response_text[:200])
        return FilingsRAGReport(
            ticker=ticker,
            stub=False,
            mda_highlights=f"[Parse error] Raw LLM response: {response_text[:500]}",
        )


def filings_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: analiza secciones §1+§7 del 10-K usando EDGAR + LLM.

    Recibe el AnalysisState completo, extrae las tres secciones cualitativas
    del 10-K más reciente, llama al LLM, y devuelve solo {"filings": report}.
    LangGraph mergea este dict parcial en el estado global.
    """
    ticker = state["ticker"]
    log.info("filings_agent_start", ticker=ticker)

    # --- 1. Fuentes EDGAR — tres secciones del 10-K más reciente ---
    business_text = EdgarFilingsAdapter.fetch_10k_business(ticker)
    risk_factors_text = EdgarFilingsAdapter.fetch_10k_risk_factors(ticker)
    mda_text = EdgarFilingsAdapter.fetch_10k_mda(ticker)

    # --- 2. Construir prompt y llamar al LLM ---
    user_prompt = _build_user_prompt(
        ticker=ticker,
        business_text=business_text,
        risk_factors_text=risk_factors_text,
        mda_text=mda_text,
    )

    log.info("filings_agent_llm_call", ticker=ticker)
    response_text = get_client().complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        max_tokens=2048,
    )

    # --- 3. Parsear respuesta del LLM en FilingsRAGReport ---
    report = _parse_llm_response(response_text, ticker)

    log.info(
        "filings_agent_complete",
        ticker=ticker,
        stub=report.stub,
        citations=len(report.citations),
    )

    return {"filings": report}
