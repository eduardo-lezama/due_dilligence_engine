"""
Archiver Node — persistencia del análisis completo.

Qué hace este módulo:
    Implementa el nodo LangGraph `archiver_node` que persiste el análisis
    completo en dos formatos:
    - Markdown: data/reports/<TICKER>-<DATE>.md (legible, archivable en git)
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
    report_path     TEXT
)
"""


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

        # Precio actual vs intrínseco por acción (primera tabla — más relevante)
        if valuation.price_current is not None or valuation.dcf_intrinsic_per_share is not None:
            lines += ["### Price vs Intrinsic Value (per share)", ""]
            lines.append("| | Per share |")
            lines.append("|---|---|")
            if valuation.price_current:
                lines.append(f"| Market price | **${valuation.price_current.value:.2f}** |")
            if valuation.dcf_intrinsic_per_share:
                upside_str = ""
                if valuation.price_current and valuation.price_current.value > 0:
                    pct = (valuation.dcf_intrinsic_per_share.value - valuation.price_current.value) / valuation.price_current.value * 100
                    upside_str = f" ({pct:+.1f}%)"
                lines.append(f"| DCF intrinsic (per share) | **${valuation.dcf_intrinsic_per_share.value:.2f}**{upside_str} |")
            lines.append("")

        # Métricas agregadas
        rows = []
        if valuation.dcf_intrinsic_value:
            rows.append(("DCF equity value (total)", f"${valuation.dcf_intrinsic_value.value/1e9:.0f}B"))
        if valuation.dcf_upside_pct is not None:
            rows.append(("DCF upside vs market cap", f"{valuation.dcf_upside_pct:+.1f}%"))
        if valuation.reverse_dcf_implied_growth is not None:
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
    if competitive and not competitive.stub and competitive.peer_roic_comparison:
        lines += ["## Peer Comparison — ROIC", ""]
        lines += ["| Ticker (peer) | ROIC | Period |", "|---|---|---|"]
        for pr in competitive.peer_roic_comparison:
            lines.append(f"| {pr.ticker} | {pr.roic:.1%} | {pr.period} |")
        lines.append("")
    elif competitive and not competitive.stub:
        # Peer comparison explícitamente skipped por el usuario — informar al lector
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
    """Crea la tabla analyses si no existe. Idempotente."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_TABLE_SQL)
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
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_filename = f"{ticker}-{today}.md"
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
