"""
CLI entry points para el Due Diligence system.

Qué hace este módulo:
    Define los comandos del CLI usando Typer. Es una capa thin — delega
    toda la lógica a los adapters y al grafo LangGraph. Solo formatea
    output con Rich.

Comandos disponibles:
    get-financials  — obtiene datos EDGAR directamente (Phase 0, debug)
    analyze         — corre el grafo completo con checkpoints interactivos

Flujo del comando analyze (Phase 1b+):
    1. Corre hasta checkpoint1 → muestra resultados de especialistas
    2. Pide confirmación al usuario para continuar
    3. Corre hasta checkpoint2 → muestra hints de valoración
    4. Pide assumptions DCF (WACC, growth, años) con defaults
    5. Corre hasta END → muestra VERDICT + report completo
    6. Informa ruta del markdown guardado

Por qué un loop en lugar de un invoke() único:
    El grafo tiene interrupt() en checkpoint1 y checkpoint2. Cada vez que
    graph.invoke() llega a un interrupt, retorna el estado actual sin completar
    el run. Para continuar, hay que llamar de nuevo con Command(resume=value)
    y el mismo config. El loop detecta si el grafo terminó o está pausado
    leyendo graph.get_state(config).next.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from due_diligence.config import settings

app = typer.Typer(help="Due Diligence — value investing analysis system")
console = Console()


# ---------------------------------------------------------------------------
# Helpers de display
# ---------------------------------------------------------------------------

def _b(v: float) -> str:
    """Formatea un valor en miles de millones con 1 decimal."""
    return f"{v / 1e9:.1f}"


def _show_financials_table(financials) -> None:
    table = Table(title=f"Financials — {financials.source_filing}", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="bold")
    table.add_column("Value (USD B)", justify="right")
    table.add_column("Period")
    table.add_column("Source")

    for label, metrics in [
        ("Revenue", financials.revenue),
        ("Gross Profit", financials.gross_profit),
        ("Operating Income", financials.operating_income),
        ("Net Income", financials.net_income),
        ("Free Cash Flow", financials.free_cash_flow),
    ]:
        if metrics:
            m = metrics[0]
            table.add_row(label, _b(m.value), m.period, m.source)

    if financials.total_debt:
        table.add_row("Total Debt", _b(financials.total_debt.value), financials.total_debt.period, "XBRL")
    if financials.cash_and_equivalents:
        table.add_row("Cash + STI", _b(financials.cash_and_equivalents.value),
                      financials.cash_and_equivalents.period, "XBRL")
    if financials.roic_series:
        r = financials.roic_series[0]
        table.add_row("ROIC", f"{r.value:.1%}", r.period, "calculated")
    console.print(table)


def _show_checkpoint1_summary(state: dict) -> None:
    """Muestra el resumen de los 4 especialistas en checkpoint1."""
    console.print("\n[bold yellow]-- Checkpoint 1: Specialist Results --[/bold yellow]\n")

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Specialist", style="bold")
    table.add_column("Key Result")
    table.add_column("Status")

    mgmt = state.get("mgmt")
    if mgmt and not mgmt.stub:
        ownership = f"{mgmt.insider_ownership_pct:.1f}%" if mgmt.insider_ownership_pct else "N/A"
        table.add_row("Management", f"Insider ownership: {ownership}", "[green]OK[/green]")
    else:
        table.add_row("Management", "—", "[yellow]degraded[/yellow]")

    filings = state.get("filings")
    if filings and not filings.stub:
        desc = (filings.business_description or "")[:60] + "…"
        table.add_row("Filings", desc, "[green]OK[/green]")
    else:
        table.add_row("Filings", "—", "[yellow]degraded[/yellow]")

    market = state.get("market")
    if market and not market.stub:
        price = f"${market.price_current.value:.2f}" if market.price_current else "N/A"
        mcap = f"${market.market_cap.value/1e9:.0f}B" if market.market_cap else "N/A"
        table.add_row("Market", f"Price: {price} | MCap: {mcap}", "[green]OK[/green]")
    else:
        table.add_row("Market", "—", "[yellow]degraded[/yellow]")

    competitive = state.get("competitive")
    if competitive and not competitive.stub:
        moat = competitive.moat_classification or "N/A"
        sources = ", ".join(competitive.moat_sources) if competitive.moat_sources else "N/A"
        table.add_row("Competitive", f"Moat: [bold]{moat}[/bold] ({sources})", "[green]OK[/green]")
    else:
        table.add_row("Competitive", "—", "[yellow]degraded[/yellow]")

    console.print(table)


def _ask_dcf_assumptions(state: dict) -> dict:
    """Muestra el hint de valoración y recoge los assumptions DCF del usuario."""
    console.print("\n[bold yellow]-- Checkpoint 2: DCF Assumptions --[/bold yellow]\n")

    # Mostrar contexto disponible
    financials = state.get("financials")
    market = state.get("market")

    if financials and financials.free_cash_flow:
        fcf = financials.free_cash_flow[0]
        console.print(f"  Latest FCF:    [cyan]${fcf.value/1e9:.1f}B[/cyan] ({fcf.period})")
    if market and market.market_cap:
        console.print(f"  Market cap:    [cyan]${market.market_cap.value/1e9:.0f}B[/cyan]")
    if market and market.price_current:
        console.print(f"  Current price: [cyan]${market.price_current.value:.2f}[/cyan]")
    if financials and financials.roic_series:
        r = financials.roic_series[0]
        console.print(f"  Latest ROIC:   [cyan]{r.value:.1%}[/cyan] ({r.period})")
    console.print()

    # Recoger inputs con defaults
    def _ask_float(prompt: str, default: float) -> float:
        val = typer.prompt(prompt, default=str(default))
        try:
            return float(val)
        except ValueError:
            console.print(f"[yellow]Invalid input, using default {default}[/yellow]")
            return default

    def _ask_int(prompt: str, default: int) -> int:
        val = typer.prompt(prompt, default=str(default))
        try:
            return int(val)
        except ValueError:
            return default

    wacc = _ask_float("  WACC % (e.g. 9.0)", 9.0)
    terminal = _ask_float("  Terminal growth % (e.g. 2.5)", 2.5)
    years = _ask_int("  Projection years (e.g. 10)", 10)
    growth_input = typer.prompt("  Near-term FCF growth % [auto from history]", default="auto")
    growth: Optional[float] = None
    if growth_input.strip().lower() != "auto":
        try:
            growth = float(growth_input)
        except ValueError:
            pass

    result: dict = {
        "wacc_pct": wacc,
        "terminal_growth_pct": terminal,
        "projection_years": years,
    }
    if growth is not None:
        result["revenue_growth_yr1_pct"] = growth

    return result


def _show_valuation(state: dict) -> None:
    valuation = state.get("valuation")
    if not valuation or valuation.stub:
        return

    table = Table(title="Valuation", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    if valuation.dcf_intrinsic_value:
        table.add_row("DCF equity value", f"${valuation.dcf_intrinsic_value.value/1e9:.0f}B")
    if valuation.dcf_upside_pct is not None:
        color = "green" if valuation.dcf_upside_pct > 0 else "red"
        table.add_row("DCF upside", f"[{color}]{valuation.dcf_upside_pct:+.1f}%[/{color}]")
    if valuation.reverse_dcf_implied_growth is not None:
        table.add_row("Implied growth (reverse DCF)", f"{valuation.reverse_dcf_implied_growth:.1%}")
    if valuation.ev_ebit_multiple:
        table.add_row("EV/EBIT", f"{valuation.ev_ebit_multiple.value:.1f}x")
    if valuation.ev_fcf_multiple:
        table.add_row("EV/FCF", f"{valuation.ev_fcf_multiple.value:.1f}x")
    if valuation.pe_multiple:
        table.add_row("P/E", f"{valuation.pe_multiple.value:.1f}x")
    if valuation.earnings_yield_pct is not None:
        table.add_row("Earnings yield (EBIT/EV)", f"{valuation.earnings_yield_pct:.1f}%")
    console.print(table)

    if valuation.assumptions:
        a = valuation.assumptions
        console.print(
            f"[dim]  DCF: WACC={a.wacc_pct}%, terminal={a.terminal_growth_pct}%, {a.projection_years}y[/dim]"
        )


def _show_verdict(state: dict) -> None:
    synthesis = state.get("synthesis")
    if not synthesis or synthesis.stub:
        return

    verdict = synthesis.verdict or "N/A"
    color = {"BUY": "bold green", "HOLD": "bold yellow", "SELL": "bold red"}.get(verdict, "white")
    border = color.split()[-1]
    confidence = synthesis.verdict_confidence or 0.0
    sizing = synthesis.suggested_sizing_pct or 0.0
    rationale = synthesis.verdict_rationale or ""

    # Panel del VERDICT con rationale
    body = (
        f"[{color}]{verdict}[/{color}]  "
        f"confidence: {confidence:.0%}  |  suggested sizing: {sizing:.0f}%"
    )
    if rationale:
        body += f"\n\n{rationale}"

    console.print(Panel(body, title="VERDICT", border_style=border))

    # Tabla de comparación precio actual vs intrínseco + múltiplos
    valuation = state.get("valuation")
    if valuation and not valuation.stub:
        console.print()

        # --- Comparación por acción ---
        price_mv = valuation.price_current
        dcf_ps = valuation.dcf_intrinsic_per_share
        if price_mv is not None or dcf_ps is not None:
            ps_table = Table(title="Price vs Intrinsic Value (per share)", box=box.SIMPLE_HEAVY)
            ps_table.add_column("", style="bold")
            ps_table.add_column("Value", justify="right")
            ps_table.add_column("Notes", justify="left")

            if price_mv is not None:
                ps_table.add_row("Market price", f"${price_mv.value:.2f}", "current")
            if dcf_ps is not None:
                delta = ""
                if price_mv is not None and price_mv.value > 0:
                    pct = (dcf_ps.value - price_mv.value) / price_mv.value * 100
                    color = "green" if pct > 0 else "red"
                    delta = f"[{color}]{pct:+.1f}% vs market[/{color}]"
                ps_table.add_row("DCF intrinsic (per share)", f"${dcf_ps.value:.2f}", delta)

            # Equity value total para contexto
            if valuation.dcf_intrinsic_value:
                mcap_str = ""
                if valuation.dcf_upside_pct is not None:
                    color = "green" if valuation.dcf_upside_pct > 0 else "red"
                    mcap_str = f"[{color}]{valuation.dcf_upside_pct:+.1f}% vs market cap[/{color}]"
                ps_table.add_row(
                    "DCF equity value (total)",
                    f"${valuation.dcf_intrinsic_value.value/1e9:.0f}B",
                    mcap_str,
                )
            if valuation.reverse_dcf_implied_growth is not None:
                ps_table.add_row(
                    "Reverse DCF implied growth",
                    f"{valuation.reverse_dcf_implied_growth:.1%}",
                    "embedded in market price",
                )
            console.print(ps_table)

        # --- Múltiplos ---
        mult_table = Table(title="Valuation Multiples", box=box.SIMPLE_HEAVY)
        mult_table.add_column("Multiple", style="bold")
        mult_table.add_column("Value", justify="right")

        if valuation.ev_ebit_multiple:
            mult_table.add_row("EV/EBIT", f"{valuation.ev_ebit_multiple.value:.1f}x")
        if valuation.ev_fcf_multiple:
            mult_table.add_row("EV/FCF", f"{valuation.ev_fcf_multiple.value:.1f}x")
        if valuation.pe_multiple:
            mult_table.add_row("P/E", f"{valuation.pe_multiple.value:.1f}x")
        if valuation.earnings_yield_pct is not None:
            mult_table.add_row("Earnings yield (EBIT/EV)", f"{valuation.earnings_yield_pct:.1f}%")
        if valuation.assumptions:
            a = valuation.assumptions
            mult_table.add_row(
                "[dim]DCF assumptions[/dim]",
                f"[dim]WACC {a.wacc_pct}% / g={a.terminal_growth_pct}% / {a.projection_years}y[/dim]",
            )
        if mult_table.row_count:
            console.print(mult_table)

    # Secciones del Expert
    sections = [
        ("Business", synthesis.business_understanding),
        ("Moat", synthesis.moat_analysis),
        ("Management", synthesis.management_assessment),
        ("Financial quality", synthesis.financial_quality),
        ("Growth", synthesis.growth_reinvestment),
        ("Valuation", synthesis.valuation_section),
        ("Risks", synthesis.risks),
        ("Psychological lens", synthesis.psychological_lens),
        ("Decision frame", synthesis.decision_frame),
    ]
    console.print()
    for title, content in sections:
        if content:
            console.print(f"[bold]{title}:[/bold] {content}")
            console.print()


def _show_critic(state: dict) -> None:
    critique = state.get("critique")
    if not critique or critique.stub:
        return

    console.print("[bold red]-- Critic (Red Team) --[/bold red]\n")

    if critique.weaknesses:
        console.print("[bold]Key weaknesses:[/bold]")
        for w in critique.weaknesses:
            console.print(f"  • {w}")
        console.print()

    if critique.contra_thesis:
        console.print(f"[bold]Contra-thesis:[/bold] {critique.contra_thesis}\n")

    if critique.pre_mortem_scenario:
        console.print(f"[bold]Pre-mortem:[/bold] {critique.pre_mortem_scenario}\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command("get-financials")
def get_financials_cmd(
    ticker: str = typer.Argument(..., help="US stock ticker (e.g. AAPL)"),
    save_fixture: Optional[Path] = typer.Option(
        None, "--save-fixture", help="Save raw XBRL response to a JSON fixture file"
    ),
) -> None:
    """Fetch and display key financials for a US ticker from SEC EDGAR."""
    from due_diligence.tools.edgar import EdgarAdapter

    console.print(f"[bold]Fetching EDGAR data for {ticker.upper()}…[/bold]")
    raw = EdgarAdapter.fetch_raw(ticker)

    if save_fixture:
        save_fixture.write_text(json.dumps(raw, indent=2))
        console.print(f"[green]Fixture saved to {save_fixture}[/green]")

    report = EdgarAdapter.parse_financials(raw)
    _show_financials_table(report)


@app.command("analyze")
def analyze_cmd(
    ticker: str = typer.Argument(..., help="US stock ticker (e.g. AAPL)"),
) -> None:
    """
    Run full due diligence analysis with interactive checkpoints.

    Flujo:
      Phase 1b → checkpoint1 (review specialists) →
      checkpoint2 (DCF assumptions) →
      Phase 1c (valuation + expert + critic + archive)
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command
    from due_diligence.graph.graph import build_graph

    ticker = ticker.upper()
    # MemorySaver mantiene el estado en RAM durante el run interactivo.
    # Evita completamente el problema de serialización msgpack de Pydantic con
    # SqliteSaver. La persistencia real (markdown + SQLite) la hace el archiver_node.
    # SqliteSaver se usará en Phase 5 (webapp) donde se necesita reanudar entre sesiones.
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"{ticker}-latest"}}

    console.print(f"\n[bold cyan]Due Diligence Analysis — {ticker}[/bold cyan]\n")

    # ── Phase 1a + 1b: supervisor → financials → specialists ──────────────
    with console.status("[bold green]Running Phase 1a+1b (EDGAR + specialists)…"):
        graph.invoke({"ticker": ticker}, config=config)

    state = graph.get_state(config).values

    # Mostrar financials
    if state.get("financials"):
        _show_financials_table(state["financials"])

    # Mostrar resumen de especialistas en checkpoint1
    _show_checkpoint1_summary(state)

    # Preguntar si continuar
    if not typer.confirm("\nProceed to valuation (Phase 1c)?", default=True):
        console.print("[yellow]Analysis stopped at checkpoint 1.[/yellow]")
        return

    # ── Reanudar: checkpoint1 → checkpoint2 ───────────────────────────────
    # NOTE: Command(resume={}) no funciona en LangGraph — dict vacío es falsy
    # y LangGraph no procesa el resume. Usar un valor truthy explícito.
    with console.status("[bold green]Passing checkpoint 1…"):
        graph.invoke(Command(resume={"proceed": True}), config=config)

    state = graph.get_state(config).values

    # checkpoint2: pedir assumptions DCF
    assumptions = _ask_dcf_assumptions(state)

    # ── Reanudar: checkpoint2 → valuation → expert → critic → archiver ────
    with console.status("[bold green]Running Phase 1c (valuation + expert + critic)…"):
        graph.invoke(Command(resume=assumptions), config=config)

    state = graph.get_state(config).values

    # Mostrar resultados finales
    console.print("\n[bold cyan]-- Analysis Complete --[/bold cyan]\n")
    _show_valuation(state)
    console.print()
    _show_verdict(state)
    _show_critic(state)

    # Mostrar ruta del report guardado
    valuation = state.get("valuation")
    if valuation and valuation.assumptions:
        # La ruta la conoce el archiver — la buscamos en data/reports/
        from datetime import date
        report_file = Path(settings.reports_dir).resolve() / f"{ticker}-{date.today().isoformat()}.md"
        if report_file.exists():
            console.print(f"\n[bold green]Report saved:[/bold green] {report_file}")
