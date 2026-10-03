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
    5. LLMClient.structured(FilingsLLMOutput) → salida tipada y validada (ADR-032)
    6. _build_report → FilingsRAGReport(stub=False); si el LLM falla →
       FilingsRAGReport(stub=True, error="<Clase>: msg") (fail loud, EC-68)
    7. Return {"filings": report} → LangGraph hace merge en AnalysisState

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

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.agents.llm_support import error_label, model_used
from due_diligence.llm.client import get_client
from due_diligence.llm.errors import LLMError
from due_diligence.schemas.llm_outputs import FilingsLLMOutput
from due_diligence.schemas.reports import FilingsRAGReport
from due_diligence.tools.edgar import EdgarFilingsAdapter

log = structlog.get_logger()

# Instrucciones al LLM: actúa como analista value y responde §1+§7 del checklist.
# El FORMATO lo impone el schema FilingsLLMOutput vía structured outputs (ADR-032): aquí
# solo se describe el CONTENIDO de cada campo. Antes el prompt incluía un "OUTPUT SCHEMA"
# en JSON que el parser intentaba recortar con find("{") — origen de EC-68.
_SYSTEM_PROMPT = """You are a value investing analyst specializing in SEC filing analysis.
Analyze the provided 10-K sections and answer the checklist questions below.

RULES:
- Base every claim ONLY on the provided 10-K text. Never invent facts.
- For each qualitative claim, include a citation with the section name ('10-K Item 1', '10-K Item 1A' or '10-K Item 7') and an EXACT verbatim excerpt copied from the text. Excerpts are verified automatically against the source; paraphrased excerpts are discarded.
- REGULATORY EVENTS: If Item 1A or Item 7 mentions ANY specific government action — entity list addition, export restriction, BIS/OFAC/SEC enforcement, sanctions, going-concern language — report it verbatim in key_regulatory_events with the entity name and date. Do NOT generalize these into "regulatory risk" — name the specific event.
- GOVERNANCE RED FLAGS (EC-66): You MUST check for and separately report ALL of the following if mentioned ANYWHERE in the provided text:
    1. Active SEC investigations or formal inquiries targeting the company itself (not industry-wide)
    2. Material weaknesses or significant deficiencies in internal controls over financial reporting
    3. Non-reliance on previously issued financial statements (restatements)
    4. Class action lawsuits or securities fraud allegations
    5. Whistleblower interference or obstruction allegations
  Report these in governance_red_flags with the specific event, date if available, and verbatim excerpt.
  If NONE of the above are found, set governance_red_flags to null. Do NOT skip this field.
- If information is not available in the provided text, say "Not available in provided sources."

FIELD GUIDANCE:
- business_description: 3-5 sentences — what the company sells, to whom, how it makes money, key segments.
- risk_factors_summary: 3-5 sentences — most material risks: regulatory, competitive, technology, macro, customer/supplier concentration.
- mda_highlights: 3-5 sentences — key revenue/margin drivers, management concerns, notable trends.
- backlog_usd: contracted backlog in USD if stated in Item 7 (e.g. 1270000000.0), else null.
- backlog_citation: REQUIRED whenever backlog_usd is not null — the exact sentence from the 10-K that states the backlog figure (with its number and unit). Without a verifiable citation the backlog figure is discarded.
- revenue_guidance: management's stated guidance for next period, quoted, else null.
- geographic_revenue_breakdown: revenue split by geography if disclosed (e.g. 'China: 85%, US: 8%, Other: 7%'), else null.
- key_regulatory_events: SPECIFIC external government actions with dates/entities, else null.
- governance_red_flags: CRITICAL INTERNAL governance failures (see rule above), else null.
"""


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

§ Governance Red Flags (MANDATORY — do not skip):
10. Does the filing disclose an ACTIVE SEC investigation or formal inquiry targeting this specific company (not a general industry risk)? Quote the exact language.
11. Does the filing mention material weaknesses or significant deficiencies in internal controls over financial reporting? Quote the exact language with dates.
12. Does the filing reference any financial restatement, non-reliance on previously issued statements, or revenue recognition errors? Quote the exact language.
13. Does the filing mention any pending or threatened class action securities lawsuits? Provide the claim period and allegations if available.
14. Is there any mention of whistleblower-related issues, obstruction, or retaliation allegations?
If NONE of questions 10-14 apply, explicitly state "No governance red flags found in provided text."

§ Operational Metrics (from Item 7):
7. Is there a contracted backlog figure? If so, what is it in USD?
8. Does management provide revenue or growth guidance for the next period? Quote it exactly.
9. Are there any accounting changes, going-concern language, or capital raise announcements?
"""


def _build_report(out: FilingsLLMOutput, ticker: str) -> FilingsRAGReport:
    """Convierte la salida validada del LLM en el report de dominio.

    `out` ya cumple el schema (structured outputs): aquí no hay parseo, solo mapeo
    y reglas de negocio. Las citas se convierten a Citation con verified=False; la
    validación contra el texto fuente llega en T3b.
    """
    return FilingsRAGReport(
        ticker=ticker, stub=False,
        business_description=out.business_description,
        risk_factors_summary=out.risk_factors_summary,
        mda_highlights=out.mda_highlights,
        backlog_usd=out.backlog_usd,          # T3b lo condiciona a una cita verificada
        revenue_guidance=out.revenue_guidance,
        geographic_revenue_breakdown=out.geographic_revenue_breakdown,
        key_regulatory_events=out.key_regulatory_events,
        governance_red_flags=out.governance_red_flags,
        citations=[c.to_citation() for c in out.citations],
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
    client = get_client()
    try:
        out = client.structured(
            FilingsLLMOutput,
            messages=[{"role": "user", "content": user_prompt}],
            system=_SYSTEM_PROMPT,
            max_tokens=4096,   # antes 2048; con strict + citas la truncación ya no es silenciosa
        )
    except LLMError as e:
        # Fail loud (invariante 5, ADR-032): report vacío con error explícito. El grafo
        # continúa y el Value Expert (DATA GAPS), el archiver y la CLI muestran el hueco.
        # Antes: stub=False + "[Parse error]" en mda_highlights → parecía contenido real (EC-68).
        log.error("filings_agent_llm_failed", ticker=ticker, error_type=type(e).__name__, error=str(e))
        return {"filings": FilingsRAGReport(ticker=ticker, stub=True, error=error_label(e))}

    # --- 3. Salida tipada → report de dominio ---
    report = _build_report(out, ticker)
    # Modelo que realmente respondió (puede ser el fallback de otro proveedor).
    report = report.model_copy(update={"model_used": model_used(client)})

    log.info(
        "filings_agent_complete",
        ticker=ticker,
        stub=report.stub,
        citations=len(report.citations),
    )

    return {"filings": report}
