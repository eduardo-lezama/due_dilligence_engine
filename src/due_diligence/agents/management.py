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

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
from due_diligence.schemas.base import Citation
from due_diligence.schemas.reports import ManagementReport
from due_diligence.tools.edgar import EdgarManagementAdapter, format_form4_for_prompt
from due_diligence.tools.finnhub import FinnhubAdapter
from due_diligence.tools.tavily import TavilyAdapter, format_tavily_for_prompt

log = structlog.get_logger()

# Queries de Tavily para §3 del checklist canónico (ADR-012).
# Formato: (template, time_range) — None = sin filtro temporal (para contexto histórico).
# Las queries de ownership/compensation usan "year" para evitar datos obsoletos.
# Las queries de CEO background y estructura corporativa usan None — esa info no envejece.
_TAVILY_QUERY_TEMPLATES: list[tuple[str, str | None]] = [
    ("{company_name} CEO founder background track record tenure history", None),  # historia no envejece
    ("{company_name} insider ownership shareholder alignment", "year"),
    ("{company_name} executive compensation structure incentives", "year"),
    ("{company_name} management quality governance board directors", "year"),
    ("{company_name} corporate structure subsidiary holding VIE listing", None),  # estructura no envejece
]

# Prompt del sistema: instruye al LLM a actuar como analista value
# y a devolver JSON estructurado con citations explícitas.
_SYSTEM_PROMPT = """You are a value investing analyst specializing in management quality assessment.
Analyze the provided context and answer the checklist questions below.

RULES:
- Base every claim ONLY on the provided context. Never invent facts.
- CITATION SOURCES — follow this strictly:
  * Numerical facts (ownership %, compensation $, Form 4 counts): cite ONLY SOURCE 1 (DEF 14A) or SOURCE 2 (Form 4). Never use web articles for numbers — they go stale.
  * CEO founder status, company history, patents: SOURCE 4 (web) is appropriate.
  * Qualitative governance, board reputation, track record: SOURCE 4 (web) and SOURCE 5 (transcripts) are appropriate.
  * SOURCE 3 (10-K Item 10) for director bios and committee memberships.
- When citing earnings call transcripts (SOURCE 5), set source_url to "Finnhub transcript <id>" and copy the speaker's verbatim sentence into `excerpt`.
- If information is not available in the context, say "Not available in provided sources."
- Return ONLY valid JSON matching the schema below. No markdown, no explanation outside JSON.

OUTPUT SCHEMA:
{
  "insider_ownership_pct": <float or null — total % of shares owned by all insiders combined>,
  "founder_ceo": <true/false/null — is the current CEO the company founder?>,
  "ceo_tenure_years": <float or null — years current CEO has been in role>,
  "form4_transactions_12m": <int — total transaction count>,
  "insider_transaction_pattern": "<string — WHO is buying vs selling (name the CEO and largest holders), NET direction (net buyer or net seller overall), and what signal this gives. E.g.: 'CEO David Wang net seller: sold 55K shares in March 2026, no open-market purchases in 12m. Board member X net buyer. Overall insider sentiment: neutral to slightly negative.' If no clear pattern, say so.>",
  "compensation_structure_notes": "<string — 2-3 sentences on compensation alignment with shareholders>",
  "capital_allocation_summary": "<string — 2-3 sentences on M&A discipline, buybacks, dividends, reinvestment quality>",
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


def _format_transcripts(transcripts: list[dict]) -> str:
    """
    Formatea transcripts de earnings calls como bloque de texto para el prompt.

    Incluye los últimos quarters ordenados de más reciente a más antiguo.
    Prepared remarks truncados a 2000 chars, Q&A a 3000 chars — balance entre
    riqueza de contexto y tamaño del context window del LLM.
    """
    if not transcripts:
        return "No recent earnings call transcripts available."
    lines = [f"{len(transcripts)} recent earnings calls (most recent first):"]
    for t in transcripts:
        head = f"[{t.get('year')}-Q{t.get('quarter')}] {t.get('date', '')}"
        lines.append(f"\n### {head}")
        remarks = (t.get("prepared_remarks") or "")[:2000]
        qa = (t.get("qa") or "")[:3000]
        lines.append(f"Prepared remarks (excerpt):\n{remarks}")
        if qa:
            lines.append(f"Q&A (excerpt):\n{qa}")
    return "\n".join(lines)


def _build_user_prompt(
    ticker: str,
    company_name: str,
    def14a_text: str,
    form4_text: str,
    item10_text: str,
    tavily_text: str,
    transcripts_text: str = "",
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

## SOURCE 5: Earnings Call Transcripts (Finnhub)
{transcripts_text or "No earnings call transcripts available."}

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
            stub=False,
            insider_ownership_pct=data.get("insider_ownership_pct"),
            founder_ceo=data.get("founder_ceo"),
            ceo_tenure_years=data.get("ceo_tenure_years"),
            form4_transactions_12m=data.get("form4_transactions_12m"),
            insider_transaction_pattern=data.get("insider_transaction_pattern"),
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
    # Cada query tiene su propio time_range: None para contexto histórico (CEO background,
    # estructura corporativa), "year" para datos que deben ser recientes (ownership, compensación).
    tavily_queries = [
        (t.format(company_name=company_name), tr)
        for t, tr in _TAVILY_QUERY_TEMPLATES
    ]
    tavily_results = TavilyAdapter.search(tavily_queries)
    tavily_text = format_tavily_for_prompt(tavily_results)

    # --- 2b. Earnings call transcripts (ADR-016) — SOURCE 5 ---
    # quarters controlado por settings.transcript_quarters (default 2).
    # Configurable en .env con TRANSCRIPT_QUARTERS=N si se quiere más contexto histórico.
    # Devuelve [] si FINNHUB_API_KEY no configurada (degraded mode limpio).
    transcripts = FinnhubAdapter.fetch_transcripts(ticker, quarters=settings.transcript_quarters)

    # Tavily fallback (EC-48): si Finnhub no devuelve transcripts (free tier),
    # buscar resúmenes de earnings calls via web. No reemplaza el transcript full,
    # pero da contexto cualitativo sobre guidance, tone, y métricas mencionadas.
    if not transcripts:
        log.info("transcripts_tavily_fallback", ticker=ticker)
        transcript_queries = [
            f"{ticker} {company_name} latest earnings call highlights key takeaways",
            f"{ticker} {company_name} quarterly earnings call management commentary guidance",
        ]
        transcript_tavily = TavilyAdapter.search(transcript_queries, max_results_per_query=2)
        if transcript_tavily:
            fallback_lines = ["[Earnings call summaries from web — not verbatim transcripts]"]
            for r in transcript_tavily[:3]:
                fallback_lines.append(f"- {r.get('title', '')}: {r.get('content', '')[:600]}")
            transcripts_text = "\n".join(fallback_lines)
        else:
            transcripts_text = _format_transcripts([])
    else:
        transcripts_text = _format_transcripts(transcripts)

    # --- 3. Construir prompt y llamar al LLM ---
    user_prompt = _build_user_prompt(
        ticker=ticker,
        company_name=company_name,
        def14a_text=def14a_text,
        form4_text=form4_text,
        item10_text=item10_text,
        tavily_text=tavily_text,
        transcripts_text=transcripts_text,
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
