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
    6. LLMClient.structured(ManagementLLMOutput) → _build_report → ManagementReport(stub=False);
       si el LLM falla → ManagementReport(stub=True, error=...) (fail loud, ADR-032/EC-68)
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

import re

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.agents.llm_support import error_label, model_used
from due_diligence.llm.client import get_client
from due_diligence.llm.errors import LLMError
from due_diligence.schemas.base import Citation
from due_diligence.schemas.llm_outputs import ManagementLLMOutput
from due_diligence.schemas.reports import BeneficialOwnership, BeneficialOwnershipRow, ManagementReport
from due_diligence.tools.edgar import EdgarEventsAdapter, EdgarManagementAdapter, format_form4_for_prompt
from due_diligence.tools.finnhub import FinnhubAdapter
from due_diligence.tools.proxy_ownership import parse_beneficial_ownership, render_ownership
from due_diligence.tools.tavily import TavilyAdapter, format_tavily_for_prompt
from due_diligence.validation.citations import count_verified, gate_llm_value, validate_and_relabel, dedupe_citations

log = structlog.get_logger()

# Queries de Tavily para §3 del checklist canónico (ADR-012).
# Formato: (template, time_range) — None = sin filtro temporal (para contexto histórico).
# Las queries de ownership/compensation usan "year" para evitar datos obsoletos.
# Las queries de CEO background y estructura corporativa usan None — esa info no envejece.
#
# Se incluye {ticker} junto a {company_name} para reducir ambigüedad en empresas con
# nombre genérico (Apple, Target, Cardinal Health). La combinación ticker+nombre es más
# específica que solo el nombre sin aumentar el riesgo de colisión (ej. "BBW" → el ticker
# corto junto al nombre completo es inequívoco; ver comentario análogo en market.py).
#
# La query de estructura corporativa reemplaza VIE/listing (específico de ADRs chinos)
# por una query de capital allocation que es útil para cualquier empresa (cambio EC-67b).
_TAVILY_QUERY_TEMPLATES: list[tuple[str, str | None]] = [
    ("{ticker} {company_name} CEO founder background track record tenure history", None),  # historia no envejece
    ("{ticker} {company_name} insider ownership shareholder alignment", "year"),
    ("{ticker} {company_name} executive compensation structure incentives", "year"),
    ("{ticker} {company_name} management quality governance board directors", "year"),
    ("{ticker} {company_name} corporate structure capital allocation holding subsidiaries", None),  # estructura no envejece
]

# Prompt del sistema: instruye al LLM a actuar como analista value
# con citations explícitas. El formato lo impone ManagementLLMOutput (ADR-032).
_SYSTEM_PROMPT = """You are a value investing analyst specializing in management quality assessment.
Analyze the provided context and answer the checklist questions below.

RULES:
- Base every claim ONLY on the provided context. Never invent facts.
- CURRENT ROLE IDENTIFICATION — THIS IS CRITICAL: The authoritative source for a person's CURRENT role is their title in SOURCE 2 (Form 4) or SOURCE 3 (10-K Item 10). SOURCE 1 (DEF 14A) often contains historical appointment language that describes PAST roles. Do NOT use DEF 14A appointment language to assert someone is the current CEO. Examples of what to avoid:
  * "Mr. X was appointed CEO in 2015" in the DEF 14A does NOT mean X is currently CEO — they may have since become Chairman or Director.
  * If SOURCE 2 (Form 4) lists a person as "Director", they are NOT the current CEO, regardless of their historical title.
  * If SOURCE 6 (5.02 leadership events) shows a new CEO appointment, that overrides all prior sources for current role identification.
  * Identify the current CEO as the person titled "Chief Executive Officer" in SOURCE 3 (10-K Item 10) or SOURCE 6 (5.02 events), not from historical proxy language.
- CITATION SOURCES — follow this strictly:
  * Numerical facts (ownership %, compensation $, Form 4 counts): cite ONLY SOURCE 1 (DEF 14A) or SOURCE 2 (Form 4). Never use web articles for numbers — they go stale.
  * CEO founder status, company history, patents: SOURCE 4 (web) is appropriate.
  * Qualitative governance, board reputation, track record: SOURCE 4 (web) and SOURCE 5 (transcripts) are appropriate.
  * SOURCE 3 (10-K Item 10) for director bios and committee memberships.
  * SOURCE 6 (5.02 8-K events) for recent leadership transitions (CEO/CFO/COO departures and appointments).
- When citing earnings call transcripts (SOURCE 5), set source_url to "Finnhub transcript <id>" and copy the speaker's verbatim sentence into `excerpt`.
- If information is not available in the context, say "Not available in provided sources."
- Citation excerpts are verified automatically against the sources: copy them VERBATIM. Paraphrased excerpts are discarded.
- Excerpts must be ONE contiguous span copied from ONE source. Never join text from different places, never add
  words, and never add computed figures (e.g. a percentage you calculated) to an excerpt.
- OWNERSHIP TABLE: SOURCE 1b is the DEF 14A beneficial ownership table already parsed into one line per holder
  ("label | N shares | X%"). Cite those lines EXACTLY as written. Insider ownership % is computed by the system
  from that table — do not report or recompute it; use it only to reason about skin in the game.

FIELD GUIDANCE (the output format is enforced by the response schema):
- founder_ceo: true/false/null — is the CURRENT CEO the company founder?
- ceo_tenure_years: years the current CEO has been in role; null if not determinable.
- ceo_tenure_citation: REQUIRED whenever ceo_tenure_years is not null — the exact sentence stating when the CEO was appointed (it must contain the year).
  If no sentence in SOURCES 1, 1b, 2, 3 or 6 states the appointment year, set ceo_tenure_years to null — the year must come from the provided text, never from your own knowledge.
- Do NOT count Form 4 transactions: the count is computed deterministically from SOURCE 2.
- insider_transaction_pattern: WHO is buying vs selling (name the CEO and largest holders), NET direction (net buyer or net seller overall), and what signal this gives. E.g.: 'CEO David Wang net seller: sold 55K shares in March 2026, no open-market purchases in 12m. Board member X net buyer. Overall insider sentiment: neutral to slightly negative.' If no clear pattern, say so.
- compensation_structure_notes: 2-3 sentences on compensation alignment with shareholders (fixed vs variable, LTI metrics — ROIC-linked vs TSR-only vs revenue-only, clawbacks).
- capital_allocation_summary: 2-3 sentences on M&A discipline, buybacks, dividends, reinvestment quality.
"""


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


def _format_leadership_events(events: list[dict]) -> str:
    """
    Formatea eventos 5.02 (leadership transitions) como texto para el prompt.

    Item 5.02 = Departure of Directors or Certain Officers; Election of Directors;
    Appointment of Certain Officers. Es la fuente primaria para detectar cambios
    de CEO/CFO/COO que ocurren entre filing periódicos.
    """
    if not events:
        return "No Item 5.02 leadership transition events in the last 12 months."
    lines = ["Recent leadership transitions (Item 5.02 — SEC primary source):"]
    for e in events:
        date = e.get("filing_date", "?")
        summary = (e.get("summary_text") or "")[:600]
        lines.append(f"  [{date}] {summary}")
    return "\n".join(lines)


def _build_user_prompt(
    ticker: str,
    company_name: str,
    def14a_text: str,
    form4_text: str,
    item10_text: str,
    tavily_text: str,
    transcripts_text: str = "",
    leadership_events_text: str = "",
    ownership_text: str = "",
) -> str:
    """
    Construye el prompt de usuario con todo el contexto de fuentes.

    Cada sección está claramente etiquetada para que el LLM sepa de dónde
    viene cada fragmento — esto es crítico para generar citations correctas.
    SOURCE 6 (5.02 events) es la fuente autoritativa para transiciones de liderazgo
    recientes — overrides cualquier información histórica del DEF 14A sobre roles.
    """
    return f"""# Management Analysis: {ticker} ({company_name})

## ⚠️ CURRENT LEADERSHIP — READ THIS FIRST (8-K Item 5.02, last 36 months)
## This section OVERRIDES any biographical or historical appointment language in the sources below.
## Sources 1-4 contain 10-K/DEF 14A biographical text that may reference PAST roles.
## The current CEO/CFO/COO are identified HERE, not from historical proxy language.
{leadership_events_text or "No Item 5.02 leadership transition events found in the last 36 months. Use SOURCE 3 (10-K Item 10) current officer table to identify the current CEO — look for the person whose CURRENT TITLE is 'Chief Executive Officer', not for historical appointment language."}

---

## SOURCE 1: DEF 14A — Proxy Statement (compensation, ownership, board)
{def14a_text or "DEF 14A not available."}

---

## SOURCE 1b: DEF 14A — Beneficial ownership table (parsed from the proxy HTML; cite lines verbatim)
{ownership_text or "Beneficial ownership table not available."}

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
2. Does management have skin in the game? Use the ownership table in SOURCE 1b. What does Form 4 activity show?
3. How is management compensated? Is it aligned with shareholders (long-term equity, ROIC targets) or with short-term adjusted metrics?
4. Capital allocation track record: M&A discipline, buybacks at reasonable prices, dividends, reinvestment quality.
5. Is the board independent? Any red flags in governance, related-party transactions, or entrenchment?"""


# Fuentes PRIMARIAS (ADR-011): las únicas contra las que se puede verificar un número
# del LLM. Tavily y transcripts sirven para citas de texto, nunca para "verificar" un %.
_PRIMARY_SOURCES = ("DEF 14A", "DEF 14A ownership table", "10-K Item 10", "Form 4")
_OWNERSHIP_LABEL = "DEF 14A ownership table"
# Un excerpt que respalda la antigüedad del CEO debe contener el año de nombramiento.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _ownership_citation(group: BeneficialOwnershipRow) -> Citation:
    """Cita determinista de la fila "as a group": la escribe el código desde la tabla, no el LLM."""
    return Citation(
        claim="Insider ownership: all directors and executive officers as a group",
        source_url=_OWNERSHIP_LABEL, excerpt=group.text, confidence=1.0, verified=True,
        verification_note="deterministic: parsed from the DEF 14A beneficial ownership table (EC-93)",
    )


def _build_report(out: ManagementLLMOutput, ticker: str, corpus: dict[str, str],
                  form4_count: int | None, ownership: BeneficialOwnership | None = None) -> ManagementReport:
    """Valida citas, condiciona los campos numéricos y pone el conteo Form 4 determinista.

    - `corpus`: {etiqueta: texto} con exactamente lo que se envió al LLM (local al nodo).
    - `insider_ownership_*`: deterministas desde la fila "as a group" de `ownership` (EC-93); sin
      tabla → None. "*" en el proxy → below_1pct=True y % None (nunca un número inventado).
    - `ceo_tenure_years` solo sobrevive con cita verificada contra fuentes primarias y con un
      año en el excerpt. Si no → None (EC-69, ADR-005).
    - `form4_transactions_12m` = len(transacciones descargadas): nunca lo dice el LLM.
    - Citas de texto libre: se validan contra TODO el corpus y su source_url pasa a la
      etiqueta real donde se encontraron. Las no verificadas se conservan marcadas.
    """
    primary = {k: v for k, v in corpus.items() if k in _PRIMARY_SOURCES}
    group = ownership.group if ownership is not None else None
    tenure, ten_cit = gate_llm_value(out.ceo_tenure_years, out.ceo_tenure_citation,
                                     primary, numeric=False)
    if ten_cit is not None and ten_cit.verified and not _YEAR_RE.search(ten_cit.excerpt):
        tenure = None
        ten_cit = ten_cit.model_copy(update={"verified": False, "confidence": 0.0,
                                             "verification_note": "no appointment year in excerpt"})
    citations = validate_and_relabel([c.to_citation() for c in out.citations], corpus)
    own_cit = _ownership_citation(group) if group is not None else None
    citations = dedupe_citations(citations + [c for c in (own_cit, ten_cit) if c is not None])
    return ManagementReport(
        ticker=ticker, stub=False,
        insider_ownership_pct=group.percent if group is not None else None,
        insider_ownership_shares=group.shares if group is not None else None,
        insider_ownership_below_1pct=group.below_1pct if group is not None else False,
        insider_ownership_multi_class_row=group.text if group is not None and group.multi_class else None,
        insider_ownership_as_of=ownership.filing_date if group is not None else None,
        founder_ceo=out.founder_ceo, ceo_tenure_years=tenure,
        form4_transactions_12m=form4_count,   # determinista: len(fetch_form4_transactions)
        insider_transaction_pattern=out.insider_transaction_pattern,
        compensation_structure_notes=out.compensation_structure_notes,
        capital_allocation_summary=out.capital_allocation_summary,
        citations=citations,
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
    # EC-93: tabla de participaciones leída por estructura desde el HTML del proxy. El texto de
    # fetch_def14a_text se queda para compensación/gobierno, pero la tabla no siempre caía en él.
    proxy_html, proxy_date = EdgarManagementAdapter.fetch_def14a_html(ticker)
    ownership = parse_beneficial_ownership(proxy_html)
    if ownership is not None:
        ownership = ownership.model_copy(update={"filing_date": proxy_date})
    ownership_text = render_ownership(ownership) if ownership is not None else ""
    if ownership is None or ownership.group is None:
        log.warning("management_ownership_table_unavailable", ticker=ticker,
                    table_found=ownership is not None)

    # 5.02 leadership transition events (EC-52): fuente autoritativa para cambios
    # de CEO/CFO/COO. Lookback de 36 meses para capturar nombramientos de hasta ~3 años
    # atrás — el cambio de CEO puede ocurrir mucho antes del análisis (ej. Korn/CXDO 2023).
    # 12 meses era insuficiente: el nombramiento quedaba fuera de la ventana y SOURCE 6 llegaba vacío.
    leadership_events = EdgarEventsAdapter.fetch_8k_events(ticker, items=["5.02"], months=36)
    leadership_events_text = _format_leadership_events(leadership_events)
    log.info("management_agent_leadership_events", ticker=ticker, count=len(leadership_events))

    # Formateamos Form 4 como tabla texto para el prompt
    form4_text = format_form4_for_prompt(form4_transactions)

    # --- 2. Búsqueda web Tavily (contexto cualitativo, ADR-012) ---
    # Cada query tiene su propio time_range: None para contexto histórico (CEO background,
    # estructura corporativa), "year" para datos que deben ser recientes (ownership, compensación).
    tavily_queries = [
        (t.format(ticker=ticker, company_name=company_name), tr)
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
        leadership_events_text=leadership_events_text,
        ownership_text=ownership_text,
    )

    log.info("management_agent_llm_call", ticker=ticker)
    client = get_client()
    try:
        out = client.structured(
            ManagementLLMOutput,
            messages=[{"role": "user", "content": user_prompt}],
            system=_SYSTEM_PROMPT,
            max_tokens=4096,   # antes 2048: con strict + citas por campo numérico hace falta margen
        )
    except LLMError as e:
        # Fail loud (ADR-032, EC-68): stub=True + error; el resto del grafo ve el hueco.
        log.error("management_agent_llm_failed", ticker=ticker, error_type=type(e).__name__, error=str(e))
        return {"mgmt": ManagementReport(ticker=ticker, stub=True, error=error_label(e))}

    # --- 4. Salida tipada → report de dominio, con citas validadas (T3b, EC-69) ---
    # Corpus = el texto fuente exacto enviado al LLM, por fuente. Local al nodo: no
    # entra en el estado checkpointeado.
    corpus = {
        "DEF 14A": def14a_text or "",
        _OWNERSHIP_LABEL: ownership_text,
        "10-K Item 10": item10_text or "",
        "Form 4": form4_text or "",
        "8-K 5.02": leadership_events_text or "",
        "Web (Tavily)": tavily_text or "",
        "Transcripts": transcripts_text or "",
    }
    report = _build_report(out, ticker, corpus, form4_count=len(form4_transactions), ownership=ownership)
    # Evidencia Form 4 determinista para el Expert (T3c): la tabla tal cual, sin LLM.
    report = report.model_copy(update={"form4_summary": form4_text})
    n_ok, n_bad = count_verified(report.citations)
    log.info("management_citations_validated", ticker=ticker, verified=n_ok, unverified=n_bad)
    report = report.model_copy(update={"model_used": model_used(client)})

    log.info(
        "management_agent_complete",
        ticker=ticker,
        stub=report.stub,
        citations=len(report.citations),
    )

    # Retornar solo la clave que este nodo produce.
    # LangGraph hace merge automático con el resto del AnalysisState.
    return {"mgmt": report}
