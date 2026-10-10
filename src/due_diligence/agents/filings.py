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

import re

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.agents.llm_support import error_label, model_used
from due_diligence.llm.client import get_client
from due_diligence.llm.errors import LLMError
from due_diligence.schemas.llm_outputs import FilingsLLMOutput
from due_diligence.schemas.reports import FilingsRAGReport
from due_diligence.tools.edgar import EdgarFilingsAdapter
from due_diligence.validation.citations import (count_verified, dedupe_citations, gate_llm_value,
                                                llm_evidence_count, validate_and_relabel)

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
- active_sec_investigation: true if the text discloses an ACTIVE SEC investigation, subpoena or formal inquiry targeting this company; false if the text was checked and none is disclosed; null if undeterminable.
- material_weakness: true if the text discloses a material weakness in internal control over financial reporting; false/null otherwise.
- governance_citation: REQUIRED whenever active_sec_investigation or material_weakness is true — the exact verbatim sentence from the 10-K disclosing it. Without a verifiable citation both flags are discarded.
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
If NONE of questions 10-14 apply, set governance_red_flags to null.

§ Operational Metrics (from Item 7):
7. Is there a contracted backlog figure? If so, what is it in USD?
8. Does management provide revenue or growth guidance for the next period? Quote it exactly.
9. Are there any accounting changes, going-concern language, or capital raise announcements?
"""


_RETRY_INSTRUCTION = """Your previous answer had NO citation that matches the 10-K text above, so its
narrative cannot be used as evidence. Answer again. For each claim, copy the excerpt VERBATIM as ONE
contiguous span from the 10-K text above (no paraphrasing, no joining, no added words or numbers).
Rejected excerpts from your previous answer:"""


def _retry_for_evidence(client, user_prompt: str, first: FilingsRAGReport, ticker: str,
                        corpus: dict[str, str]) -> FilingsRAGReport:
    """Un único reintento cuando ninguna cita del LLM se verifica (EC-94).

    Solo filings: medido tras H1, 0 citas en 3/4 análisis con texto fuente limpio (BBW) —
    comportamiento del modelo, que una instrucción con los excerpts rechazados corrige. Se queda
    la respuesta con más evidencia verificada; si el reintento falla, la primera (no se pierde
    el análisis). En management no se reintenta: allí la causa es estructural (EC-93).
    """
    rejected = [f"- {c.excerpt[:200]} ({c.verification_note})" for c in first.citations if not c.verified]
    feedback = "\n".join(rejected) if rejected else "- (none: you returned no citations)"
    log.info("filings_evidence_retry", ticker=ticker, rejected=len(rejected))
    try:
        out = client.structured(
            FilingsLLMOutput,
            messages=[{"role": "user", "content": f"{user_prompt}\n\n{_RETRY_INSTRUCTION}\n{feedback}"}],
            system=_SYSTEM_PROMPT,
            max_tokens=16384,  # mismo techo que la llamada principal (EC-99)
        )
    except LLMError as e:
        log.warning("filings_evidence_retry_failed", ticker=ticker, error_type=type(e).__name__)
        return first
    second = _build_report(out, ticker, corpus)
    return second if llm_evidence_count(second.citations) > llm_evidence_count(first.citations) else first


# La frase ENTERA debe ser un "nada" (fullmatch): un prefijo descartaría "Nonetheless, the SEC opened…"
# o "No governance red flags found; however, the auditor identified a material weakness" (fail loud).
_NO_FLAGS_RE = re.compile(
    r"(no (governance )?red flags( were| are)?( found| identified| noted)?( in (the )?provided (text|sources))?"
    r"|not available in provided sources|none( found| identified)?|n/?a)")


def _governance_or_none(text: str | None) -> str | None:
    """Frases de "no hay banderas" → None (EC-97).

    El prompt pedía escribir "No governance red flags found in provided text." y los tres consumidores
    (Value Expert, Critic, archiver) solo miran si el campo tiene texto: mostraban un bloque de
    banderas rojas para empresas limpias y la regla 23 se activaba sin motivo. Se normaliza aquí, en
    el productor, para que los consumidores no tengan que conocer las variantes de "nada".
    """
    if text is None:
        return None
    norm = text.strip().lower().rstrip(".")
    return None if not norm or _NO_FLAGS_RE.fullmatch(norm) else text


def _build_report(out: FilingsLLMOutput, ticker: str, corpus: dict[str, str]) -> FilingsRAGReport:
    """Convierte la salida validada del LLM en el report de dominio, con citas verificadas.

    `out` ya cumple el schema (structured outputs): aquí no hay parseo, solo mapeo
    y reglas de negocio (EC-69):
    - citas validadas contra el corpus del 10-K (todo él es fuente primaria);
    - `backlog_usd` solo si `backlog_citation` se verifica y contiene la cifra.
    """
    backlog, backlog_cit = gate_llm_value(out.backlog_usd, out.backlog_citation, corpus, numeric=True)
    # Governance flags (T4a, EC-70): un True solo sobrevive con cita verificada, porque
    # dispara un tope de confianza determinista en el Value Expert. False se conserva
    # (no activa nada); un True sin evidencia pasa a None (no determinado).
    gov_flagged = bool(out.active_sec_investigation or out.material_weakness)
    _, gov_cit = gate_llm_value(True if gov_flagged else None, out.governance_citation, corpus, numeric=False)
    gov_ok = gov_cit is not None and gov_cit.verified
    sec_flag = out.active_sec_investigation if (out.active_sec_investigation is not True or gov_ok) else None
    mw_flag = out.material_weakness if (out.material_weakness is not True or gov_ok) else None
    citations = validate_and_relabel([c.to_citation() for c in out.citations], corpus)
    citations = dedupe_citations(citations + [c for c in (backlog_cit, gov_cit) if c is not None])
    return FilingsRAGReport(
        ticker=ticker, stub=False,
        business_description=out.business_description,
        risk_factors_summary=out.risk_factors_summary,
        mda_highlights=out.mda_highlights,
        backlog_usd=backlog,                  # None salvo cita verificada con la cifra
        revenue_guidance=out.revenue_guidance,
        geographic_revenue_breakdown=out.geographic_revenue_breakdown,
        key_regulatory_events=out.key_regulatory_events,
        governance_red_flags=_governance_or_none(out.governance_red_flags),
        active_sec_investigation=sec_flag,
        material_weakness=mw_flag,
        citations=citations,
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

    # Fail loud (invariante 5, EC-94): los fetchers devuelven "" sin lanzar. Sin ningún apartado
    # no hay nada que analizar: report con error, sin llamar al LLM (antes recibía "not available"
    # y producía una narrativa sin fuente). Un apartado vacío suelto se registra.
    items = {"Item 1": business_text, "Item 1A": risk_factors_text, "Item 7": mda_text}
    empty = [name for name, text in items.items() if not (text or "").strip()]
    if len(empty) == len(items):
        log.error("filings_10k_text_empty", ticker=ticker)
        return {"filings": FilingsRAGReport(
            ticker=ticker, stub=True,
            error="DataUnavailable: 10-K Items 1, 1A and 7 text empty (nothing to analyze)")}
    if empty:
        log.warning("filings_10k_item_empty", ticker=ticker, items=empty)

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
            max_tokens=16384,  # techo, no coste: gpt-6-luna razona y esos tokens cuentan aquí (EC-99)
        )
    except LLMError as e:
        # Fail loud (invariante 5, ADR-032): report vacío con error explícito. El grafo
        # continúa y el Value Expert (DATA GAPS), el archiver y la CLI muestran el hueco.
        # Antes: stub=False + "[Parse error]" en mda_highlights → parecía contenido real (EC-68).
        log.error("filings_agent_llm_failed", ticker=ticker, error_type=type(e).__name__, error=str(e))
        return {"filings": FilingsRAGReport(ticker=ticker, stub=True, error=error_label(e))}

    # --- 3. Salida tipada → report de dominio ---
    # Corpus = las tres secciones del 10-K tal como se enviaron al LLM (local al nodo).
    corpus = {"10-K Item 1": business_text or "", "10-K Item 1A": risk_factors_text or "",
              "10-K Item 7": mda_text or ""}
    report = _build_report(out, ticker, corpus)
    if llm_evidence_count(report.citations) == 0:
        report = _retry_for_evidence(client, user_prompt, report, ticker, corpus)
    n_ok, n_bad = count_verified(report.citations)
    log.info("filings_citations_validated", ticker=ticker, verified=n_ok, unverified=n_bad)
    # Modelo que realmente respondió (puede ser el fallback de otro proveedor).
    report = report.model_copy(update={"model_used": model_used(client)})

    log.info(
        "filings_agent_complete",
        ticker=ticker,
        stub=report.stub,
        citations=len(report.citations),
    )

    return {"filings": report}
