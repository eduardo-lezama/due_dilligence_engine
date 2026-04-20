"""
Management Agent — analista de gestión e insider ownership.

Qué hace este módulo:
    Implementa el nodo LangGraph `management_agent` que cubre la sección §3
    del checklist canónico (Management & Skin in the Game). Orquesta tres
    fuentes EDGAR + búsqueda web Tavily, construye un prompt estructurado,
    llama al LLM, y devuelve un ManagementReport con citations validadas.

Flujo del nodo:
    1. Fetch DEF 14A (compensación, ownership) via EdgarManagementAdapter
    2. Fetch Form 4 (insider transactions 12m) via EdgarManagementAdapter
    3. Fetch 10-K Item 10 (bios directivos) via EdgarManagementAdapter
    4. Búsqueda web con 4 queries dirigidas via TavilyAdapter
    5. Prompt con todo el contexto + preguntas exactas del §3
    6. LLM interpreta y devuelve JSON → ManagementReport(stub=False)
    7. Return {"mgmt": report} → LangGraph hace merge en AnalysisState

Por qué el LLM no genera números:
    Los valores numéricos (ownership %, compensation $, # transacciones) se
    extraen de EDGAR antes de llamar al LLM. El LLM solo interpreta, explica,
    y genera texto cualitativo. Zero hallucination numérica (ADR-005).

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
from due_diligence.schemas.reports import ManagementReport
from due_diligence.tools.edgar import EdgarManagementAdapter, format_form4_for_prompt
from due_diligence.tools.tavily import TavilyAdapter, format_tavily_for_prompt

log = structlog.get_logger()

# Queries de Tavily optimizadas para §3 del checklist canónico (ADR-012).
# Intencionalmente genéricas — se formatean con el nombre de la empresa en runtime.
_TAVILY_QUERY_TEMPLATES = [
    "{company_name} CEO tenure track record capital allocation",
    "{company_name} insider ownership shareholder alignment",
    "{company_name} executive compensation structure",
    "{company_name} management quality governance board",
]

# Prompt del sistema: instruye al LLM a actuar como analista value
# y a devolver JSON estructurado con citations explícitas.
_SYSTEM_PROMPT = """You are a value investing analyst specializing in management quality assessment.
Analyze the provided context and answer the checklist questions below.

RULES:
- Base every claim ONLY on the provided context. Never invent facts.
- For each qualitative claim, include a citation with the source document/URL and an exact excerpt.
- If information is not available in the context, say "Not available in provided sources."
- Return ONLY valid JSON matching the schema below. No markdown, no explanation outside JSON.

OUTPUT SCHEMA:
{
  "insider_ownership_pct": <float or null>,
  "ceo_tenure_years": <float or null>,
  "form4_transactions_12m": <int — total count of transactions>,
  "compensation_structure_notes": "<string — 2-3 sentences on compensation alignment>",
  "capital_allocation_summary": "<string — 2-3 sentences on capital allocation quality>",
  "citations": [
    {
      "claim": "<exact claim being cited>",
      "source_url": "<URL or document name>",
      "excerpt": "<verbatim excerpt from source supporting the claim>",
      "confidence": <0.0 to 1.0>,
      "gaap_vs_nongaap_flag": null
    }
  ]
}"""


def _build_user_prompt(
    ticker: str,
    company_name: str,
    def14a_text: str,
    form4_text: str,
    item10_text: str,
    tavily_text: str,
) -> str:
    """
    Construye el prompt de usuario con todo el contexto de fuentes.

    Cada sección está claramente etiquetada para que el LLM sepa de dónde
    viene cada fragmento — esto es crítico para generar citations correctas.
    """
    return f"""# Management Analysis: {ticker} ({company_name})

## SOURCE 1: DEF 14A — Proxy Statement (compensation, ownership, board)
{def14a_text or "DEF 14A not available."}

---

## SOURCE 2: Form 4 — Insider Transactions (last 12 months)
{form4_text}

---

## SOURCE 3: 10-K Item 10 — Directors and Executive Officers
{item10_text or "10-K Item 10 not available."}

---

## SOURCE 4: Web Research
{tavily_text}

---

# CHECKLIST §3: Management & Skin in the Game

Answer each question using ONLY the sources above:

1. Who runs the company? How long has the CEO been in position? What is their prior track record?
2. Does management have skin in the game? What is the insider ownership %? What does Form 4 activity show?
3. How is management compensated? Is it aligned with shareholders (long-term equity, ROIC targets) or with short-term adjusted metrics?
4. Capital allocation track record: M&A discipline, buybacks at reasonable prices, dividends, reinvestment quality.
5. Is the board independent? Any red flags in governance, related-party transactions, or entrenchment?

Return valid JSON only."""


def _parse_llm_response(response_text: str, ticker: str) -> ManagementReport:
    """
    Parsea la respuesta JSON del LLM en un ManagementReport validado.

    Si el JSON es inválido o incompleto, devuelve un ManagementReport parcial
    con stub=False pero campos opcionales en None. Nunca falla silenciosamente
    — siempre loguea el error con el texto que causó el problema.
    """
    try:
        # Extraer JSON del texto — el LLM a veces añade texto antes/después
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found in LLM response")

        data: dict[str, Any] = json.loads(response_text[start:end])

        # Construir citations validadas desde los dicts del LLM
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
                log.debug("citation_parse_failed", ticker=ticker, error=str(e))

        return ManagementReport(
            ticker=ticker,
            stub=False,  # el agente ha corrido — ya no es placeholder
            insider_ownership_pct=data.get("insider_ownership_pct"),
            ceo_tenure_years=data.get("ceo_tenure_years"),
            form4_transactions_12m=data.get("form4_transactions_12m"),
            compensation_structure_notes=data.get("compensation_structure_notes"),
            capital_allocation_summary=data.get("capital_allocation_summary"),
            citations=citations,
        )

    except Exception as e:
        log.error("management_llm_parse_failed", ticker=ticker, error=str(e), response=response_text[:200])
        # Fallback: devolver reporte parcial con el texto crudo como nota
        return ManagementReport(
            ticker=ticker,
            stub=False,
            compensation_structure_notes=f"[Parse error] Raw LLM response: {response_text[:500]}",
        )


def management_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: analiza management quality usando EDGAR + Tavily + LLM.

    Recibe el AnalysisState completo (populated por supervisor y financials_node),
    ejecuta el pipeline de fuentes, llama al LLM, y devuelve solo la clave
    que este nodo produce: {"mgmt": ManagementReport}.

    LangGraph hace el merge automático de este dict parcial en el estado global.
    """
    ticker = state["ticker"]
    # company_name para las queries Tavily — usamos el ticker si no tenemos el nombre
    company_name = state.get("company_name", ticker)  # type: ignore[typeddict-item]

    log.info("management_agent_start", ticker=ticker)

    # --- 1. Fuentes EDGAR (deterministas, primary source) ---
    def14a_text = EdgarManagementAdapter.fetch_def14a_text(ticker)
    form4_transactions = EdgarManagementAdapter.fetch_form4_transactions(ticker)
    item10_text = EdgarManagementAdapter.fetch_10k_item10(ticker)

    # Formateamos Form 4 como tabla texto para el prompt
    form4_text = format_form4_for_prompt(form4_transactions)

    # --- 2. Búsqueda web Tavily (contexto cualitativo, ADR-012) ---
    tavily_queries = [
        t.format(company_name=company_name) for t in _TAVILY_QUERY_TEMPLATES
    ]
    tavily_results = TavilyAdapter.search(tavily_queries)
    tavily_text = format_tavily_for_prompt(tavily_results)

    # --- 3. Construir prompt y llamar al LLM ---
    user_prompt = _build_user_prompt(
        ticker=ticker,
        company_name=company_name,
        def14a_text=def14a_text,
        form4_text=form4_text,
        item10_text=item10_text,
        tavily_text=tavily_text,
    )

    log.info("management_agent_llm_call", ticker=ticker)
    response_text = get_client().complete(
        messages=[{"role": "user", "content": user_prompt}],
        system=_SYSTEM_PROMPT,
        max_tokens=2048,
    )

    # --- 4. Parsear respuesta del LLM en ManagementReport ---
    report = _parse_llm_response(response_text, ticker)

    log.info(
        "management_agent_complete",
        ticker=ticker,
        stub=report.stub,
        citations=len(report.citations),
    )

    # Retornar solo la clave que este nodo produce.
    # LangGraph hace merge automático con el resto del AnalysisState.
    return {"mgmt": report}
