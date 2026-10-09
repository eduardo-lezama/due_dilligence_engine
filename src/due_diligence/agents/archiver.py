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
Un análisis parcial persistido es mejor que no persistir nada — pero DEBE decir que
es parcial (ADR-032, EC-68): sin VERDICT el archivo se llama -INCOMPLETE.md, la
sección "## Analysis incomplete" lista los agentes que fallaron y SQLite guarda
status='incomplete'. `report_filename` y `collect_failures` son el único sitio donde
se decide eso; la CLI los reutiliza.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
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
    tokens_out      INTEGER,
    status          TEXT,
    models_used     TEXT,
    n_verified_citations INTEGER,
    methodology_version TEXT
)
"""

# Columnas añadidas en Phase 2.5 — migration para bases de datos existentes
_MIGRATION_SQLS = [
    "ALTER TABLE analyses ADD COLUMN latency_ms REAL",
    "ALTER TABLE analyses ADD COLUMN cost_eur REAL",
    "ALTER TABLE analyses ADD COLUMN tokens_in INTEGER",
    "ALTER TABLE analyses ADD COLUMN tokens_out INTEGER",
    # H1 (ADR-032): hacen medible la métrica de éxito del plan. Filas antiguas → NULL
    # (= pre-H1, metodología previa a FCFF y sin validación de citas).
    "ALTER TABLE analyses ADD COLUMN status TEXT",               # 'complete' | 'incomplete'
    "ALTER TABLE analyses ADD COLUMN models_used TEXT",          # "gpt-4.1: 3, gpt-5.4-mini: 2"
    "ALTER TABLE analyses ADD COLUMN n_verified_citations INTEGER",
    "ALTER TABLE analyses ADD COLUMN methodology_version TEXT",  # "H1"
]

# Versión de metodología que se graba en cada fila nueva (comparabilidad histórica).
METHODOLOGY_VERSION = "H1"

# Agentes cuyo report puede llevar `error` (fail loud, ADR-032). financials y valuation
# ganan `error` en H1 (T6 DataUnavailableError / T12a ValuationInputError); getattr
# tolera reports que aún no tengan el campo.
_FAILABLE_AGENTS = [("financials", "Financials"), ("mgmt", "Management"), ("filings", "Filings"),
                    ("competitive", "Competitive"), ("valuation", "Valuation"),
                    ("synthesis", "Value Expert"), ("critique", "Critic")]


def report_filename(today: str, ticker: str, synthesis) -> str:
    """Nombre canónico del report. Lo usan archiver_node y la CLI (un solo sitio).

    Sin VERDICT válido → INCOMPLETE (antes PENDING). Si el grafo llegó al archiver el
    run terminó; si no hay VERDICT, está incompleto, no "pendiente" (ADR-032).
    """
    verdict = synthesis.verdict if synthesis is not None and not synthesis.stub else None
    label = verdict if verdict in ("BUY", "HOLD", "SELL") else "INCOMPLETE"
    return f"{today}-{ticker}-{label}.md"


def collect_failures(state) -> list[tuple[str, str]]:
    """(agente, error) de cada report con `error`. También lo usan la CLI y el Value Expert."""
    out = []
    for key, label in _FAILABLE_AGENTS:
        rep = state.get(key)
        err = getattr(rep, "error", None) if rep is not None else None
        if err:
            out.append((label, err))
    return out


def _render_failures(state) -> list[str]:
    """Sección visible justo después del header: qué falló y por qué."""
    failures = collect_failures(state)
    if not failures:
        return []
    lines = ["## Analysis incomplete", "",
             "The following agents failed; their sections are absent from this report:", ""]
    lines += [f"- **{label}**: `{err}`" for label, err in failures]
    return lines + [""]


def _format_models_used(run_stats) -> str:
    """'gpt-4.1: 3, gpt-5.4-mini: 2' desde RunStats.calls_by_model ('' si no hay llamadas)."""
    calls = getattr(run_stats, "calls_by_model", None) or {}
    return ", ".join(f"{m}: {n}" for m, n in sorted(calls.items()))


def _count_verified_citations(state) -> int:
    """Nº de citas verificadas (validation/citations.py) entre los especialistas LLM."""
    total = 0
    for key in ("mgmt", "filings", "competitive"):
        rep = state.get(key)
        for c in (getattr(rep, "citations", None) or []):
            if getattr(c, "verified", False):
                total += 1
    return total


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _render_equity_bridge(bridge, share_count, per_share, *, share_count_source=None) -> list[str]:
    """
    Sección "### Enterprise → Equity Bridge" (ADR-033): EV del DCF → equity → por acción.

    Cada derecho sobre el EV queda visible para auditoría: deuda, minoritarios y preferentes
    restan; caja, STI y LTI suman. Los arrendamientos operativos aparecen como MEMO (no se
    restan: su pago ya está en OCF/EBIT, ASC 842). Componentes ausentes se listan aparte
    para que "0" no se confunda con "dato no disponible".

    Args:
        bridge: EquityBridge del ValuationReport.
        share_count: acciones diluidas usadas (None si no disponibles).
        per_share: valor por acción ya calculado por el agente (no se recalcula aquí).
    """
    lines = ["### Enterprise → Equity Bridge", "", "| Component | Value |", "|---|---|",
             f"| Enterprise value (DCF, FCFF) | {_fmt_usd(bridge.enterprise_value)} |",
             f"| (−) Total debt | {_fmt_usd(bridge.total_debt)} |"]
    if bridge.minority_interest:
        lines.append(f"| (−) Minority interest | {_fmt_usd(bridge.minority_interest)} |")
    if bridge.preferred_stock:
        lines.append(f"| (−) Preferred stock | {_fmt_usd(bridge.preferred_stock)} |")
    lines.append(f"| (+) Cash | {_fmt_usd(bridge.cash)} |")
    if bridge.short_term_investments:
        lines.append(f"| (+) Short-term investments | {_fmt_usd(bridge.short_term_investments)} |")
    if bridge.long_term_investments:
        lines.append(f"| (+) Long-term investments (non-operating) | {_fmt_usd(bridge.long_term_investments)} |")
    lines.append(f"| = Net claims | {_fmt_usd(bridge.net_claims)} |")
    lines.append(f"| = Equity value | {_fmt_usd(bridge.equity_value)} |")
    if share_count:
        src = f" ({share_count_source})" if share_count_source else ""
        lines.append(f"| ÷ Diluted shares{src} | {share_count / 1e6:,.2f}M |")  # /1e6 aceptado: nº de acciones (siempre ≥ 1M), no USD (MCE-26)
    if per_share is not None:
        lines.append(f"| = Per share | **${per_share:.2f}** |")
    if bridge.operating_lease_liabilities_memo is not None:
        lines.append(f"| Operating lease liabilities (memo, not deducted — ASC 842) "
                     f"| {_fmt_usd(bridge.operating_lease_liabilities_memo)} |")
    lines.append("")
    if bridge.components_missing:
        lines += [f"*Bridge components unavailable (treated as 0): {', '.join(bridge.components_missing)}*", ""]
    return lines


def _render_markdown(state: AnalysisState, run_stats=None) -> str:
    """
    Serializa el estado completo en un report markdown estructurado.

    Secciones: header → (analysis incomplete) → valuation → expert analysis (9 §) →
    critic → citations → footer (con "Models used" si se pasa `run_stats`).
    Los campos None se omiten para no contaminar el report; los FALLOS no se omiten
    (sección "Analysis incomplete", ADR-032).
    """
    ticker = state["ticker"]
    as_of = state.get("as_of_date", "N/A")  # type: ignore[typeddict-item]
    run_id = state.get("run_id", "N/A")     # type: ignore[typeddict-item]
    today = date.today().isoformat()

    lines: list[str] = []

    # --- Header ---
    synthesis = state.get("synthesis")      # type: ignore[typeddict-item]
    # Sin síntesis válida → INCOMPLETE (no "N/A"): el lector debe ver que falta el VERDICT.
    verdict = (synthesis.verdict if synthesis and not synthesis.stub else None) or "INCOMPLETE"
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

    # --- Fallos visibles (ADR-032, EC-68) ---
    lines += _render_failures(state)

    # --- Valuation summary table ---
    valuation = state.get("valuation")      # type: ignore[typeddict-item]
    if valuation and not valuation.stub:
        lines += ["## Valuation Summary", ""]

        # MCE-21: cuando hay spread DCF detectado, el warning sube a la cabecera
        # de la sección Valuation antes de la tabla — el lector ve primero la advertencia,
        # no el "+79% upside" engañoso.
        # EC-54: la nota ahora siempre existe (contiene al menos el growth context),
        # pero el reading guide y el reordenado de métricas solo se activan si hay spread.
        has_contradiction = bool(
            valuation.dcf_reliability_note
            and "spread note" in valuation.dcf_reliability_note.lower()
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
            # EPV per share — piso de valor sin crecimiento (Greenwald, ADR-029)
            if valuation.epv_per_share is not None:
                epv_upside_str = ""
                if valuation.price_current and valuation.price_current.value > 0:
                    epv_pct = (valuation.epv_per_share.value - valuation.price_current.value) / valuation.price_current.value * 100
                    epv_upside_str = f" ({epv_pct:+.1f}%)"
                src = valuation.epv_source or "EBIT median"
                lines.append(
                    f"| EPV per share *(zero-growth floor, {src})* "
                    f"| ${valuation.epv_per_share.value:.2f}{epv_upside_str} |"
                )
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
            label = "DCF equity value (after bridge)"
            if has_contradiction:
                label += " — *conditional*"
            rows.append((label, _fmt_usd(valuation.dcf_intrinsic_value.value)))
        if valuation.dcf_upside_pct is not None:
            # Upside SIEMPRE por acción frente al precio (ADR-033 §10, EC-73).
            label = "DCF upside vs price"
            if has_contradiction:
                label += " — *conditional*"
            rows.append((label, f"{valuation.dcf_upside_pct:+.1f}%"))
        if not has_contradiction and valuation.reverse_dcf_implied_growth is not None:
            # Si no hay contradicción, reverse DCF va en su orden natural (después del forward)
            rows.append(("Implied growth (reverse DCF)", f"{valuation.reverse_dcf_implied_growth:.1%}"))
        if valuation.ev_ebit_multiple:
            rows.append(("EV/EBIT", f"{valuation.ev_ebit_multiple.value:.1f}x"))
        if valuation.ev_fcf_multiple:
            # Market EV / FCFF desde ADR-033; reports viejos (sin valuation_basis) eran EV/FCF apalancado.
            ev_fcf_label = "EV/FCFF" if valuation.valuation_basis else "EV/FCF"
            rows.append((ev_fcf_label, f"{valuation.ev_fcf_multiple.value:.1f}x"))
        if valuation.pe_multiple:
            rows.append(("P/E", f"{valuation.pe_multiple.value:.1f}x"))
        if valuation.earnings_yield_pct is not None:
            rows.append(("Earnings yield (EBIT/EV)", f"{valuation.earnings_yield_pct:.1f}%"))
        if valuation.magic_formula_rank is not None:
            pv_list = state.get("peer_valuations") or []       # type: ignore[typeddict-item]
            valid_pvs = [pv for pv in pv_list if pv.mf_rank is not None]
            total_participants = len(valid_pvs) + 1            # +1 for subject
            peer_tickers_str = ", ".join(pv.ticker for pv in valid_pvs)
            rows.append((
                "Magic Formula rank",
                f"**#{valuation.magic_formula_rank} of {total_participants}**"
                f" (peers: {peer_tickers_str}) — Greenblatt earnings yield + ROIC",
            ))
        if rows:
            lines += ["### Aggregate Metrics", ""]
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            for label, val in rows:
                lines.append(f"| {label} | {val} |")
            lines.append("")
        if valuation.assumptions:
            a = valuation.assumptions
            # EC-54: incluir near_term_growth en el footnote con stage split (ADR-029)
            growth_str = ""
            if valuation.near_term_growth_used_pct is not None:
                g_pct = valuation.near_term_growth_used_pct * 100
                src = valuation.near_term_growth_source or "auto"
                growth_str = f", g1={g_pct:.1f}% ({src})"
            stage_str = ""
            if valuation.stage1_years_used is not None and valuation.fade_years_used is not None:
                stage_str = (
                    f" × {valuation.stage1_years_used}y constant"
                    f", then {valuation.fade_years_used}y fade to terminal"
                )
            lines.append(
                f"*DCF assumptions: WACC={a.wacc_pct}%{growth_str}{stage_str}, "
                f"terminal={a.terminal_growth_pct}%, "
                f"{a.projection_years}y explicit (3-stage Damodaran fade)*"
            )
            cyc = valuation.cyclicality
            if cyc is not None and cyc.is_cyclical:
                # EC-92: el lector debe saber que la tabla parte del nivel mid-cycle, no del último año.
                lines.append(f"*Cyclical: yes — {cyc.evidence}. DCF base, EPV and terminal value use the "
                             f"mid-cycle level (median margins × latest revenue); stage-1 growth = terminal "
                             f"unless overridden.*")
            if valuation.suggested_wacc_pct is not None:
                lines.append(
                    f"*Suggested WACC (real: Ke + Kd after tax, market weights): "
                    f"{valuation.suggested_wacc_pct:.1f}% — {valuation.suggested_wacc_breakdown}*"
                )
            # Pie del plano enterprise (ADR-033): base, tipo, mid-year y dependencia del terminal.
            footer_parts = []
            if valuation.fcff_base is not None:
                footer_parts.append(f"FCFF base {_fmt_usd(valuation.fcff_base.value)} ({valuation.fcff_base.source})")
            if valuation.tax_rate_used is not None:
                src = f" ({valuation.tax_rate_source})" if valuation.tax_rate_source else ""
                footer_parts.append(f"tax rate {valuation.tax_rate_used:.1%}{src}")
            if valuation.mid_year_convention_used is not None:
                footer_parts.append(f"mid-year convention {'ON' if valuation.mid_year_convention_used else 'OFF'}")
            if valuation.terminal_value_share is not None:
                footer_parts.append(f"Terminal value share {valuation.terminal_value_share:.0%} of EV")
            if footer_parts:
                lines.append(f"*{'; '.join(footer_parts)}*")
            lines.append("")

        # --- Enterprise → Equity bridge (ADR-033) ---
        if valuation.equity_bridge is not None:
            lines += _render_equity_bridge(
                valuation.equity_bridge, valuation.share_count_used,
                valuation.dcf_intrinsic_per_share.value if valuation.dcf_intrinsic_per_share else None,
                share_count_source=valuation.share_count_source,
            )
        # Mostrar reliability note si no se mostró ya arriba (spread → showed at top).
        # Con EC-54, la nota existe siempre que se calculó DCF, no solo en error conditions.
        if valuation.dcf_reliability_note and not has_contradiction:
            lines += [f"> {valuation.dcf_reliability_note}", ""]

        # --- Convergence table (ADR-030) ---
        if valuation.valuation_methods_summary:
            cheap_count = sum(1 for r in valuation.valuation_methods_summary if r["classification"] == "cheap")
            expensive_count = sum(1 for r in valuation.valuation_methods_summary if r["classification"] == "expensive")
            fair_count = sum(1 for r in valuation.valuation_methods_summary if r["classification"] == "fair")
            na_count = sum(1 for r in valuation.valuation_methods_summary if r["classification"] == "n/a")
            lines += ["### Valuation Methods Convergence", ""]
            lines.append("| Method | Per Share | Signal | Classification |")
            lines.append("|---|---|---|---|")
            for row in valuation.valuation_methods_summary:
                cls = row["classification"]
                cls_fmt = f"**{cls}**" if cls in ("cheap", "expensive") else cls
                lines.append(
                    f"| {row['method']} | {row['per_share']} "
                    f"| {row['display_upside']} | {cls_fmt} |"
                )
            summary_parts = []
            if cheap_count:
                summary_parts.append(f"{cheap_count} cheap")
            if fair_count:
                summary_parts.append(f"{fair_count} fair")
            if expensive_count:
                summary_parts.append(f"{expensive_count} expensive")
            if na_count:
                summary_parts.append(f"{na_count} n/a")
            lines.append("")
            lines.append(f"*Convergence: {', '.join(summary_parts)} out of {len(valuation.valuation_methods_summary)} methods.*")
            lines.append("")

    # Valoración fallida con error tipado (ADR-033 §6): se muestra, no se omite en silencio.
    if valuation and valuation.stub and valuation.error:
        lines += ["## Valuation Summary", "",
                  f"> ⚠️ Valuation not computed — {valuation.error}", ""]

    # --- Sensitivity table ---
    if valuation and valuation.sensitivity_table:
        # Unidad explícita: USD/share desde ADR-033; reports viejos sin el campo eran equity en USD B.
        unit = valuation.sensitivity_unit or "USD B (legacy)"
        lines += [f"### DCF Sensitivity — 3-stage Damodaran fade ({unit})", ""]
        header_row = valuation.sensitivity_table[0]
        wacc_labels = list(header_row["values"].keys())
        lines.append("| g1 (Stage 1 growth) \\ WACC | " + " | ".join(wacc_labels) + " |")
        lines.append("|---|" + "---|" * len(wacc_labels))
        for row in valuation.sensitivity_table:
            # None = celda inválida (WACC − g < 1pp) → "n/m", nunca un número inventado.
            vals = " | ".join("n/m" if v is None else str(v) for v in row["values"].values())
            lines.append(f"| {row['near_term_growth_pct']}% | {vals} |")
        lines.append("")

    # --- Data quality flags (deterministas, de parse_financials) ---
    # Identidades contables rotas, fallbacks de conceptos XBRL... Antes no tenían consumidor:
    # el lector del informe debe verlos junto a las cifras que pueden estar afectadas.
    fin_for_flags = state.get("financials")  # type: ignore[typeddict-item]
    if fin_for_flags is not None and fin_for_flags.data_quality_flags:
        lines += ["## Data quality flags", ""]
        lines += [f"- {flag}" for flag in fin_for_flags.data_quality_flags]
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

    # --- Governance red flags (EC-70): sección propia, no diluida en "Key Operational Data" ---
    if filings and not filings.stub and filings.governance_red_flags:
        lines += ["## Governance Red Flags (10-K)", "", filings.governance_red_flags, ""]
        verified_flags = [name for name, on in (("Active SEC investigation", filings.active_sec_investigation),
                                                ("Material weakness in ICFR", filings.material_weakness)) if on]
        if verified_flags:
            lines += [f"**Citation-verified flags:** {', '.join(verified_flags)} "
                      f"(verdict confidence capped at 60%)", ""]

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
            own_series = financials_for_roic.roic_series[:5]  # serie completa (EC-38)
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

    # --- Peer Relative Valuation (MCE-18): EV/EBIT, FCF yield, MF rank ---
    lines += _render_peer_relative_valuation(state)

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
    # Modelo(s) que realmente respondieron (con fallback pueden diferir del configurado).
    models = _format_models_used(run_stats) if run_stats is not None else ""
    if models:
        lines += ["", f"*Models used: {models}*"]

    return "\n".join(lines)


def _render_peer_relative_valuation(state: AnalysisState) -> list[str]:
    """
    Render ## Peer Comparison - Relative Valuation table. MCE-18.

    Shows EV/EBIT, FCF yield, ROIC, MF rank for subject + each peer.
    Subject FCF yield computed inline from financials + market (not stored in ValuationReport).
    Returns empty list if no peer_valuations in state.
    """
    peer_valuations = state.get("peer_valuations") or []          # type: ignore[typeddict-item]
    if not peer_valuations:
        return []

    has_any_multiples = any(
        pv.ev_ebit is not None or pv.fcf_yield_pct is not None
        for pv in peer_valuations
    )
    if not has_any_multiples:
        return []

    valuation = state.get("valuation")                            # type: ignore[typeddict-item]
    financials = state.get("financials")                          # type: ignore[typeddict-item]
    market = state.get("market")                                  # type: ignore[typeddict-item]
    ticker_label = state.get("ticker", "Subject")                 # type: ignore[typeddict-item]

    lines: list[str] = ["## Peer Comparison — Relative Valuation", ""]
    lines += ["| Ticker | EV/EBIT | FCF Yield | ROIC | MF Rank |", "|---|---|---|---|---|"]

    # --- Subject row ---
    subj_ev_ebit = (
        f"{valuation.ev_ebit_multiple.value:.1f}x"
        if (valuation and valuation.ev_ebit_multiple)
        else "N/A"
    )

    # Compute subject FCF yield from raw data (FCF / market_cap * 100)
    subj_fcf_yield = "N/A"
    if (
        financials and financials.free_cash_flow
        and market and market.market_cap and market.market_cap.value
    ):
        fcf_val = financials.free_cash_flow[0].value
        mkt_val = market.market_cap.value
        if fcf_val > 0 and mkt_val > 0:
            subj_fcf_yield = f"{fcf_val / mkt_val * 100:.1f}%"

    subj_roic = "N/A"
    if financials and financials.roic_series:
        subj_roic = f"{financials.roic_series[0].value:.1%}"

    subj_mf = (
        f"**#{valuation.magic_formula_rank}**"
        if (valuation and valuation.magic_formula_rank is not None)
        else "N/A"
    )

    lines.append(
        f"| **{ticker_label}** | **{subj_ev_ebit}** | **{subj_fcf_yield}** "
        f"| **{subj_roic}** | {subj_mf} <- subject |"
    )

    # --- Peer rows ---
    excluded_tickers: list[str] = []
    for pv in peer_valuations:
        if pv.no_market_data:
            ev_str = "N/A — no market data"
            fcf_str = "N/A — no market data"
            excluded_tickers.append(pv.ticker)
        else:
            ev_str = f"{pv.ev_ebit:.1f}x" if pv.ev_ebit is not None else "N/A (EBIT<=0)"
            fcf_str = f"{pv.fcf_yield_pct:.1f}%" if pv.fcf_yield_pct is not None else "N/A (FCF<0)"
        roic_str = f"{pv.roic:.1%}"
        rank_str = f"#{pv.mf_rank}" if pv.mf_rank is not None else "—"
        lines.append(f"| {pv.ticker} | {ev_str} | {fcf_str} | {roic_str} | {rank_str} |")

    lines.append("")
    has_any_mf_rank = (
        any(pv.mf_rank is not None for pv in peer_valuations)
    )
    if has_any_mf_rank:
        lines.append(
            "*Magic Formula rank = combined rank by earnings yield (EBIT/EV) + ROIC (Greenblatt). #1 = best.*"
        )
    if excluded_tickers:
        lines.append(
            f"*{', '.join(excluded_tickers)}: excluded from MF rank — no market data available.*"
        )
    lines.append("")

    return lines


def _render_citations_section(state: AnalysisState) -> list[str]:
    """
    Agrupa las citas de los agentes LLM por agente: verificadas y no verificadas aparte.

    - "## Sources & Citations": solo citas verified=True (validation/citations.py) con
      claim, excerpt verbatim, confidence y fuente.
    - "## Unverified citations (excluded from synthesis)": las rechazadas, con el motivo
      (verification_note). Se muestran para auditoría, pero no decidieron nada (EC-69).
    Referencia ADR-014 (trail de auditoría) y ADR-005 (invariante 2).
    """
    verified_groups: list[tuple[str, list]] = []
    unverified: list[tuple[str, object]] = []
    for field, label in [
        ("mgmt", "Management"),
        ("filings", "Filings (10-K)"),
        ("competitive", "Competitive & Moat"),
    ]:
        rep = state.get(field)  # type: ignore[literal-required]
        if rep is None or rep.stub or not getattr(rep, "citations", None):
            continue
        ok = [c for c in rep.citations if c.verified]
        if ok:
            verified_groups.append((label, ok))
        unverified += [(label, c) for c in rep.citations if not c.verified]

    lines: list[str] = []
    if verified_groups:
        lines += ["## Sources & Citations", ""]
        for label, cits in verified_groups:
            lines.append(f"### {label}")
            lines.append("")
            for c in cits:
                conf = f"{c.confidence:.0%}"
                lines.append(f"- **{c.claim}** (confidence: {conf})")
                lines.append(f"  > {c.excerpt}")
                lines.append(f"  Source: [{c.source_url}]({c.source_url})")
                lines.append("")
    if unverified:
        lines += ["## Unverified citations (excluded from synthesis)", "",
                  "These citations could not be found in the source text the model saw. "
                  "They are listed for audit only and did not inform the verdict.", ""]
        for label, c in unverified:
            lines.append(f"- **{c.claim}** — _{c.verification_note or 'not verified'}_ ({label}, claimed source: {c.source_url})")
            lines.append(f"  > {c.excerpt}")
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
    models_used: str | None = None,
) -> None:
    """
    Actualiza las métricas de performance en la fila ya insertada por archiver_node.
    Llamada desde CLI después de que el análisis completa (ADR-021).

    `models_used` (opcional) refresca la columna con el RunStats final del CLI; si es
    None se conserva lo que grabó el archiver.
    """
    sql = """
    UPDATE analyses
    SET latency_ms=:latency_ms, cost_eur=:cost_eur,
        tokens_in=:tokens_in, tokens_out=:tokens_out,
        models_used=COALESCE(:models_used, models_used)
    WHERE run_id=:run_id
    """
    _ensure_db(db_path)  # garantiza las columnas H1 aunque la DB sea antigua
    with sqlite3.connect(db_path) as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "latency_ms": round(latency_ms, 1),
            "cost_eur": round(cost_eur, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "models_used": models_used,
        })
        conn.commit()


def _insert_row(db_path: str, row: dict) -> None:
    """Inserta una fila en `analyses`. Las columnas H1 (status, models_used, ...) son opcionales."""
    sql = """
    INSERT INTO analyses
        (ticker, run_id, as_of_date, analysis_date, verdict, verdict_confidence,
         suggested_sizing_pct, dcf_upside_pct, moat_classification, report_path,
         status, models_used, n_verified_citations, methodology_version)
    VALUES
        (:ticker, :run_id, :as_of_date, :analysis_date, :verdict, :verdict_confidence,
         :suggested_sizing_pct, :dcf_upside_pct, :moat_classification, :report_path,
         :status, :models_used, :n_verified_citations, :methodology_version)
    """
    # Columnas H1 opcionales para llamadores antiguos: ausentes → NULL (no inventar status).
    h1_defaults = {"status": None, "models_used": None, "n_verified_citations": None,
                   "methodology_version": None}
    with sqlite3.connect(db_path) as conn:
        conn.execute(sql, {**h1_defaults, **row})
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

    # El archiver es el último nodo: todas las llamadas LLM del run ya se hicieron.
    # Si leer las métricas falla, el report se escribe igual (sin pie de modelos).
    try:
        run_stats = get_client().get_run_stats()
    except Exception as e:  # noqa: BLE001 — el archivado nunca bloquea el pipeline
        log.warning("archiver_run_stats_unavailable", error=str(e))
        run_stats = None

    # --- Render markdown ---
    report_md = _render_markdown(state, run_stats=run_stats)

    # --- Escribir markdown ---
    # Formato: <DATE>-<TICKER>-<VERDICT>.md (ordenable cronológicamente + filtrable por verdict).
    # Sin VERDICT → -INCOMPLETE.md (report_filename, ADR-032). La variable local se llama
    # `fname` para no sombrear la función report_filename (UnboundLocalError).
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    fname = report_filename(today, ticker, synthesis)
    report_path = reports_dir / fname

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
        "status": "complete" if verdict in ("BUY", "HOLD", "SELL") else "incomplete",
        "models_used": (_format_models_used(run_stats) or None) if run_stats is not None else None,
        "n_verified_citations": _count_verified_citations(state),
        "methodology_version": METHODOLOGY_VERSION,
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
