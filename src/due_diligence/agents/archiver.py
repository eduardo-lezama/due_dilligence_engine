"""
Archiver Node — persistencia del análisis completo.

Qué hace este módulo:
    Implementa el nodo LangGraph `archiver_node` que persiste el análisis
    completo en dos formatos:
    - Markdown: data/reports/<DATE>-<TICKER>-<VERDICT>.md (legible, archivable en git)
    - SQLite: data/analyses.db (consultable, historial comparativo)

Por qué dos formatos:
    El markdown es el artefacto primario — se puede leer, imprimir, enviar.
    SQLite permite consultas posteriores: "dame todos los BUY con upside >20%"
    y será la fuente del RAG en Phase 2. Son complementarios, no redundantes.

Posición en el grafo:
    critic_agent → archiver_node → END

El archiver siempre escribe aunque algunos campos estén vacíos (degraded mode).
Un análisis parcial persistido es mejor que no persistir nada.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.utils.formatting import fmt_usd as _fmt_usd

log = structlog.get_logger()

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    run_id          TEXT,
    as_of_date      TEXT,
    analysis_date   TEXT NOT NULL,
    verdict         TEXT,
    verdict_confidence REAL,
    suggested_sizing_pct REAL,
    dcf_upside_pct  REAL,
    moat_classification TEXT,
    report_path     TEXT,
    latency_ms      REAL,
    cost_eur        REAL,
    tokens_in       INTEGER,
    tokens_out      INTEGER
)
"""

# Columnas añadidas en Phase 2.5 — migration para bases de datos existentes
_MIGRATION_SQLS = [
    "ALTER TABLE analyses ADD COLUMN latency_ms REAL",
    "ALTER TABLE analyses ADD COLUMN cost_eur REAL",
    "ALTER TABLE analyses ADD COLUMN tokens_in INTEGER",
    "ALTER TABLE analyses ADD COLUMN tokens_out INTEGER",
]


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _render_markdown(state: AnalysisState) -> str:
    """
    Serializa el estado completo en un report markdown estructurado.

    Secciones: header → financials → valuation → expert analysis (9 §) → critic → footer.
    Los campos None se omiten silenciosamente para no contaminar el report.
    """
    ticker = state["ticker"]
    as_of = state.get("as_of_date", "N/A")  # type: ignore[typeddict-item]
    run_id = state.get("run_id", "N/A")     # type: ignore[typeddict-item]
    today = date.today().isoformat()

    lines: list[str] = []

    # --- Header ---
    synthesis = state.get("synthesis")      # type: ignore[typeddict-item]
    verdict = synthesis.verdict if synthesis and not synthesis.stub else "N/A"
    confidence = synthesis.verdict_confidence if synthesis and not synthesis.stub else None
    sizing = synthesis.suggested_sizing_pct if synthesis and not synthesis.stub else None

    lines += [
        f"# Due Diligence Report: {ticker}",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| Ticker | {ticker} |",
        f"| 10-K as of | {as_of} |",
        f"| Analysis date | {today} |",
        f"| Run ID | {run_id} |",
        f"| **VERDICT** | **{verdict}** |",
    ]
    if confidence is not None:
        lines.append(f"| Confidence | {confidence:.0%} |")
    if sizing is not None:
        lines.append(f"| Suggested sizing | {sizing:.0f}% of portfolio |")
    lines.append("")
    if synthesis and not synthesis.stub and synthesis.verdict_rationale:
        lines += [f"> {synthesis.verdict_rationale}", ""]

    # --- Valuation summary table ---
    valuation = state.get("valuation")      # type: ignore[typeddict-item]
    if valuation and not valuation.stub:
        lines += ["## Valuation Summary", ""]

        # MCE-21: cuando hay contradicción DCF detectada, el warning sube a la cabecera
        # de la sección Valuation antes de la tabla — el lector ve primero la advertencia,
        # no el "+79% upside" engañoso.
        has_contradiction = (
            valuation.dcf_reliability_note
            and "contradiction" in valuation.dcf_reliability_note.lower()
        )
        if has_contradiction:
            lines += [
                f"> {valuation.dcf_reliability_note}",
                "",
                "> **Reading guide**: when forward DCF and reverse DCF diverge materially, "
                "the **reverse DCF (market-implied growth)** is the more informative anchor — "
                "it tells you what assumptions the current price already discounts.",
                "",
            ]

        # Precio actual vs intrínseco por acción (primera tabla — más relevante)
        market_for_range = state.get("market")      # type: ignore[typeddict-item]
        if valuation.price_current is not None or valuation.dcf_intrinsic_per_share is not None:
            lines += ["### Price vs Intrinsic Value (per share)", ""]
            lines.append("| | Per share |")
            lines.append("|---|---|")
            if valuation.price_current:
                price_line = f"**${valuation.price_current.value:.2f}**"
                # Añadir contexto 52w: dónde está el precio en el rango (Fix D)
                if (market_for_range and not market_for_range.stub
                        and market_for_range.price_52w_low is not None
                        and market_for_range.price_52w_high is not None):
                    lo = market_for_range.price_52w_low.value
                    hi = market_for_range.price_52w_high.value
                    price_line += f" (52w: ${lo:.2f}–${hi:.2f})"
                lines.append(f"| Market price | {price_line} |")
            if valuation.dcf_intrinsic_per_share:
                upside_str = ""
                if valuation.price_current and valuation.price_current.value > 0:
                    pct = (valuation.dcf_intrinsic_per_share.value - valuation.price_current.value) / valuation.price_current.value * 100
                    upside_str = f" ({pct:+.1f}%)"
                # MCE-21: cuando hay contradicción, etiquetar forward DCF como condicional
                label = "DCF intrinsic (per share)"
                if has_contradiction:
                    label += " — *conditional on growth assumption*"
                lines.append(f"| {label} | **${valuation.dcf_intrinsic_per_share.value:.2f}**{upside_str} |")
            else:
                # DCF no computable — explicar por qué, no dejar la tabla vacía
                lines.append("| DCF intrinsic (per share) | N/A — FCF negativo o insuficiente para DCF |")
            lines.append("")

        # Métricas agregadas — orden depende de si hay contradicción (MCE-21)
        rows = []
        if has_contradiction and valuation.reverse_dcf_implied_growth is not None:
            # Reverse DCF como métrica primaria (headline) cuando contradice forward DCF
            rows.append(
                ("**Implied growth (reverse DCF — primary anchor)**",
                 f"**{valuation.reverse_dcf_implied_growth:.1%}**")
            )
        if valuation.dcf_intrinsic_value:
            label = "DCF equity value (total)"
            if has_contradiction:
                label += " — *conditional*"
            rows.append((label, _fmt_usd(valuation.dcf_intrinsic_value.value)))
        if valuation.dcf_upside_pct is not None:
            label = "DCF upside vs market cap"
            if has_contradiction:
                label += " — *conditional*"
            rows.append((label, f"{valuation.dcf_upside_pct:+.1f}%"))
        if not has_contradiction and valuation.reverse_dcf_implied_growth is not None:
            # Si no hay contradicción, reverse DCF va en su orden natural (después del forward)
            rows.append(("Implied growth (reverse DCF)", f"{valuation.reverse_dcf_implied_growth:.1%}"))
        if valuation.ev_ebit_multiple:
            rows.append(("EV/EBIT", f"{valuation.ev_ebit_multiple.value:.1f}x"))
        if valuation.ev_fcf_multiple:
            rows.append(("EV/FCF", f"{valuation.ev_fcf_multiple.value:.1f}x"))
        if valuation.pe_multiple:
            rows.append(("P/E", f"{valuation.pe_multiple.value:.1f}x"))
        if valuation.earnings_yield_pct is not None:
            rows.append(("Earnings yield (EBIT/EV)", f"{valuation.earnings_yield_pct:.1f}%"))
        if rows:
            lines += ["### Aggregate Metrics", ""]
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            for label, val in rows:
                lines.append(f"| {label} | {val} |")
            lines.append("")
        if valuation.assumptions:
            a = valuation.assumptions
            lines.append(
                f"*DCF assumptions: WACC={a.wacc_pct}%, terminal growth={a.terminal_growth_pct}%, "
                f"{a.projection_years} years*"
            )
            lines.append("")
        # Si la nota ya se mostró arriba (contradicción), no duplicarla. Si es de otro tipo
        # (e.g. growth capped at 30% sin contradicción), mostrarla aquí.
        if valuation.dcf_reliability_note and not has_contradiction:
            lines += [f"> {valuation.dcf_reliability_note}", ""]

    # --- Sensitivity table ---
    if valuation and valuation.sensitivity_table:
        lines += ["### DCF Sensitivity (equity value, USD B)", ""]
        header_row = valuation.sensitivity_table[0]
        wacc_labels = list(header_row["values"].keys())
        lines.append("| Near-term growth \\ WACC | " + " | ".join(wacc_labels) + " |")
        lines.append("|---|" + "---|" * len(wacc_labels))
        for row in valuation.sensitivity_table:
            vals = " | ".join(str(v) for v in row["values"].values())
            lines.append(f"| {row['near_term_growth_pct']}% | {vals} |")
        lines.append("")

    # --- Key operational data from filings (backlog, guidance, regulatory events) ---
    filings = state.get("filings")            # type: ignore[typeddict-item]
    if filings and not filings.stub:
        has_ops = any([filings.backlog_usd, filings.revenue_guidance,
                       filings.geographic_revenue_breakdown, filings.key_regulatory_events])
        if has_ops:
            lines += ["## Key Operational Data (10-K)", ""]
            if filings.geographic_revenue_breakdown:
                lines += [f"**Revenue by geography:** {filings.geographic_revenue_breakdown}", ""]
            if filings.backlog_usd:
                lines += [f"**Contracted backlog:** {_fmt_usd(filings.backlog_usd)}", ""]
            if filings.revenue_guidance:
                lines += [f"**Management guidance:** {filings.revenue_guidance}", ""]
            if filings.key_regulatory_events:
                lines += [f"**Regulatory events:** {filings.key_regulatory_events}", ""]

    # --- Expert analysis: 9 sections ---
    if synthesis and not synthesis.stub:
        lines += ["## Value Expert Analysis", ""]
        sections = [
            ("§1 Business Understanding", synthesis.business_understanding),
            ("§2 Moat Analysis", synthesis.moat_analysis),
            ("§3 Management Assessment", synthesis.management_assessment),
            ("§4 Financial Quality", synthesis.financial_quality),
            ("§5 Growth & Reinvestment", synthesis.growth_reinvestment),
            ("§6 Valuation", synthesis.valuation_section),
            ("§7 Risks & Red Flags", synthesis.risks),
            ("§8 Psychological Lens", synthesis.psychological_lens),
            ("§9 Decision Frame", synthesis.decision_frame),
        ]
        for title, content in sections:
            if content:
                lines += [f"### {title}", "", content, ""]

    # --- Critic ---
    critique = state.get("critique")        # type: ignore[typeddict-item]
    if critique and not critique.stub:
        lines += ["## Critic (Red Team)", ""]
        if critique.weaknesses:
            lines.append("**Key weaknesses:**")
            lines.append("")
            for w in critique.weaknesses:
                lines.append(f"- {w}")
            lines.append("")
        if critique.contra_thesis:
            lines += ["**Contra-thesis:**", "", critique.contra_thesis, ""]
        if critique.pre_mortem_scenario:
            lines += ["**Pre-mortem scenario:**", "", critique.pre_mortem_scenario, ""]

    # --- Peer ROIC comparison (ADR-015) ---
    competitive = state.get("competitive")    # type: ignore[typeddict-item]
    financials_for_roic = state.get("financials")  # type: ignore[typeddict-item]
    if competitive and not competitive.stub and competitive.peer_roic_comparison:
        lines += ["## Peer Comparison — ROIC", ""]
        lines += ["| Ticker | Latest ROIC | Trend (oldest→latest) | Note |", "|---|---|---|---|"]
        # Primera fila: la empresa analizada (referencia) — SIEMPRE presente (EC-20).
        # Aunque no haya ROIC computable (IC indeterminado por buybacks), el lector
        # debe ver el ticker como ancla de la tabla, con explicación honesta.
        ticker_label = state.get("ticker", "Subject")  # type: ignore[typeddict-item]
        if financials_for_roic and financials_for_roic.roic_series:
            own_series = financials_for_roic.roic_series[:3]
            own_trend = " → ".join(f"{r.value:.1%}" for r in reversed(own_series))
            own_latest = own_series[0]
            lines.append(f"| **{ticker_label}** | **{own_latest.value:.1%}** ({own_latest.period}) | {own_trend} | ← subject |")
        else:
            # ROIC del subject no calculable (típicamente IC indeterminado por buybacks).
            # El subject row aparece igualmente — sin él, la peer comparison es estéril.
            lines.append(f"| **{ticker_label}** | N/A — IC indeterminate | — | ← subject |")
        # Filas de peers con tendencia
        for pr in competitive.peer_roic_comparison:
            if pr.roic_trail and len(pr.roic_trail) > 1:
                trend = " → ".join(f"{r['roic']:.1%}" for r in reversed(pr.roic_trail))
            else:
                trend = f"{pr.roic:.1%}"
            lines.append(f"| {pr.ticker} | {pr.roic:.1%} ({pr.period}) | {trend} | |")
        lines.append("")
    elif competitive and not competitive.stub:
        # Distinguir entre skip explícito y intento fallido
        attempted_no_roic = state.get("peers_attempted_no_roic", [])  # type: ignore[typeddict-item]
        peers_provided = state.get("peers", []) or []                  # type: ignore[typeddict-item]
        if attempted_no_roic:
            tickers_str = ", ".join(attempted_no_roic)
            lines += [
                "## Peer Comparison — ROIC", "",
                f"Peers fetched ({tickers_str}) but ROIC could not be computed from XBRL "
                f"(possible causes: foreign filer / 20-F, incomplete XBRL, or no invested_capital data).",
                "",
            ]
        elif peers_provided:
            # Peers se intentaron pero todos fallaron en el fetch
            lines += [
                "## Peer Comparison — ROIC", "",
                f"Peer fetch attempted ({', '.join(peers_provided)}) but all failed "
                f"(possible causes: foreign filer / 20-F, EDGAR data unavailable). "
                f"Check logs for details.",
                "",
            ]
        else:
            lines += ["## Peer Comparison — ROIC", "", "Peer comparison skipped by user.", ""]

    # --- Market & News (ADR-014: news URLs preserved inline) ---
    market = state.get("market")              # type: ignore[typeddict-item]
    if market and not market.stub and market.recent_news_summary:
        lines += ["## Market & News", "", market.recent_news_summary, ""]

    # --- Material Events — 8-K (ADR-017) ---
    if market and not market.stub:
        if market.material_events:
            lines += ["## Material Events (8-K, last 12 months)", ""]
            lines += ["| Date | Item(s) | Summary | Source |", "|---|---|---|---|"]
            for e in market.material_events:
                items_str = ", ".join(e.get("item_numbers", []))
                summary = (e.get("summary_text") or "")[:200].replace("\n", " ")
                url = e.get("filing_url", "")
                link = f"[filing]({url})" if url else "—"
                lines.append(f"| {e.get('filing_date', '?')} | {items_str} | {summary} | {link} |")
            lines.append("")
        else:
            lines += ["## Material Events (8-K, last 12 months)", "", "No material 8-K events in the last 12 months.", ""]

    # --- Citations appendix (ADR-014) ---
    lines += _render_citations_section(state)

    # --- Footer ---
    lines += [
        "---",
        f"*Generated by due-diligence system · {today} · run_id: {run_id}*",
    ]

    return "\n".join(lines)


def _render_citations_section(state: AnalysisState) -> list[str]:
    """
    Agrupa todas las citas de los agentes LLM por agente y las renderiza.

    Cada cita muestra: claim, excerpt verbatim, confidence y URL clickable.
    Solo incluye agentes con citas reales (stub=False y citations no vacío).
    Referencia ADR-014: auditoría del trail de claims cualitativos.
    """
    groups: list[tuple[str, list]] = []
    for field, label in [
        ("mgmt", "Management"),
        ("filings", "Filings (10-K)"),
        ("competitive", "Competitive & Moat"),
    ]:
        rep = state.get(field)  # type: ignore[literal-required]
        if rep is not None and not rep.stub and getattr(rep, "citations", None):
            groups.append((label, rep.citations))
    if not groups:
        return []
    lines: list[str] = ["## Sources & Citations", ""]
    for label, cits in groups:
        lines.append(f"### {label}")
        lines.append("")
        for c in cits:
            conf = f"{c.confidence:.0%}"
            lines.append(f"- **{c.claim}** (confidence: {conf})")
            lines.append(f"  > {c.excerpt}")
            lines.append(f"  Source: [{c.source_url}]({c.source_url})")
            lines.append("")
    return lines


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

def _ensure_db(db_path: str) -> None:
    """Crea la tabla analyses si no existe y aplica migrations. Idempotente."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_TABLE_SQL)
        # Migration: añadir columnas de performance si la DB ya existía sin ellas
        for sql in _MIGRATION_SQLS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # columna ya existe — ignorar
        conn.commit()


def update_performance_metrics(
    db_path: str,
    run_id: str,
    latency_ms: float,
    cost_eur: float,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """
    Actualiza las métricas de performance en la fila ya insertada por archiver_node.
    Llamada desde CLI después de que el análisis completa (ADR-021).
    """
    sql = """
    UPDATE analyses
    SET latency_ms=:latency_ms, cost_eur=:cost_eur,
        tokens_in=:tokens_in, tokens_out=:tokens_out
    WHERE run_id=:run_id
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "latency_ms": round(latency_ms, 1),
            "cost_eur": round(cost_eur, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        })
        conn.commit()


def _insert_row(db_path: str, row: dict) -> None:
    sql = """
    INSERT INTO analyses
        (ticker, run_id, as_of_date, analysis_date, verdict, verdict_confidence,
         suggested_sizing_pct, dcf_upside_pct, moat_classification, report_path)
    VALUES
        (:ticker, :run_id, :as_of_date, :analysis_date, :verdict, :verdict_confidence,
         :suggested_sizing_pct, :dcf_upside_pct, :moat_classification, :report_path)
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(sql, row)
        conn.commit()


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def archiver_node(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: persiste el análisis en markdown + SQLite.

    No modifica el estado del grafo — devuelve {} porque el archivado
    es un efecto secundario, no un output que otros nodos consuman.

    En caso de error de I/O, loguea y continúa — nunca bloquea el pipeline.
    """
    ticker = state["ticker"]
    today = date.today().isoformat()
    run_id = state.get("run_id", "unknown")  # type: ignore[typeddict-item]

    log.info("archiver_start", ticker=ticker, run_id=run_id)

    # --- Extraer campos para SQLite ---
    synthesis = state.get("synthesis")      # type: ignore[typeddict-item]
    valuation = state.get("valuation")      # type: ignore[typeddict-item]
    competitive = state.get("competitive")  # type: ignore[typeddict-item]

    verdict = synthesis.verdict if synthesis and not synthesis.stub else None
    confidence = synthesis.verdict_confidence if synthesis and not synthesis.stub else None
    sizing = synthesis.suggested_sizing_pct if synthesis and not synthesis.stub else None
    dcf_upside = valuation.dcf_upside_pct if valuation and not valuation.stub else None
    moat = competitive.moat_classification if competitive and not competitive.stub else None

    # --- Render markdown ---
    report_md = _render_markdown(state)

    # --- Escribir markdown ---
    # Formato: <DATE>-<TICKER>-<VERDICT>.md (ordenable cronológicamente + filtrable por verdict).
    # Si el análisis no produjo VERDICT (stub o parse error), usar "PENDING" como placeholder.
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    verdict_label = verdict if verdict in ("BUY", "HOLD", "SELL") else "PENDING"
    report_filename = f"{today}-{ticker}-{verdict_label}.md"
    report_path = reports_dir / report_filename

    try:
        report_path.write_text(report_md, encoding="utf-8")
        log.info("archiver_markdown_saved", path=str(report_path))
    except Exception as e:
        log.error("archiver_markdown_failed", error=str(e), path=str(report_path))
        report_path = Path("N/A")

    # --- Insertar en SQLite ---
    row = {
        "ticker": ticker,
        "run_id": run_id,
        "as_of_date": state.get("as_of_date"),  # type: ignore[typeddict-item]
        "analysis_date": today,
        "verdict": verdict,
        "verdict_confidence": confidence,
        "suggested_sizing_pct": sizing,
        "dcf_upside_pct": dcf_upside,
        "moat_classification": moat,
        "report_path": str(report_path),
    }
    try:
        _ensure_db(settings.db_path)
        _insert_row(settings.db_path, row)
        log.info("archiver_db_saved", ticker=ticker, verdict=verdict)
    except Exception as e:
        log.error("archiver_db_failed", error=str(e))

    log.info("archiver_complete", ticker=ticker, report_path=str(report_path))

    # El archiver no modifica el estado — su trabajo es I/O, no estado
    return {}
