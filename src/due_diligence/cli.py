"""
CLI entry points para el Due Diligence system.

Qué hace este módulo:
    Define los comandos del CLI usando Typer. Es una capa thin — delega
    toda la lógica a los adapters y al grafo LangGraph. Solo formatea
    output con Rich.

Comandos disponibles:
    get-financials  — obtiene datos EDGAR directamente (Phase 0, debug)
    analyze         — corre el grafo completo de análisis (Phase 1a+)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from due_diligence.config import settings

app = typer.Typer(help="Due Diligence — value investing analysis system")
console = Console()


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

    table = Table(title=f"{report.ticker} — {report.source_filing}", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value (USD B)", justify="right")
    table.add_column("Period")
    table.add_column("Source")

    def _b(v: float) -> str:
        return f"{v / 1e9:.1f}"

    if report.revenue:
        r = report.revenue[0]
        table.add_row("Revenue", _b(r.value), r.period, r.source)
    if report.gross_profit:
        g = report.gross_profit[0]
        table.add_row("Gross Profit", _b(g.value), g.period, g.source)
    if report.operating_income:
        o = report.operating_income[0]
        table.add_row("Operating Income", _b(o.value), o.period, o.source)
    if report.net_income:
        n = report.net_income[0]
        table.add_row("Net Income", _b(n.value), n.period, n.source)
    if report.free_cash_flow:
        f = report.free_cash_flow[0]
        table.add_row("Free Cash Flow", _b(f.value), f.period, f.source)
    table.add_row(
        "Total Debt", _b(report.total_debt.value), report.total_debt.period, report.total_debt.source
    )
    table.add_row(
        "Cash + STI",
        _b(report.cash_and_equivalents.value),
        report.cash_and_equivalents.period,
        "XBRL",
    )
    table.add_row("CIK", report.cik, "—", "—")

    console.print(table)


@app.command("analyze")
def analyze_cmd(
    ticker: str = typer.Argument(..., help="US stock ticker (e.g. AAPL)"),
) -> None:
    """Run full due diligence analysis pipeline for a US ticker."""
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    from due_diligence.graph.graph import build_graph

    # Crear directorio data/ si no existe — ahí vive la base de datos SQLite
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # SqliteSaver persiste el estado del grafo entre runs.
    # check_same_thread=False: permite que LangGraph use el conn desde hilos distintos.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    graph = build_graph(checkpointer=checkpointer)

    console.print(f"\n[bold cyan]Due Diligence Analysis — {ticker.upper()}[/bold cyan]")
    console.print("[dim]Phase 1a: Supervisor + Financials specialist[/dim]\n")

    with console.status("[bold green]Running analysis…"):
        # thread_id identifica este run en el checkpointer.
        # "<TICKER>-latest" permite re-invocar y reanudar el mismo análisis.
        config = {"configurable": {"thread_id": f"{ticker.upper()}-latest"}}
        result = graph.invoke({"ticker": ticker.upper()}, config=config)

    console.print(f"[green]OK[/green] Run ID:      {result.get('run_id', 'n/a')}")
    console.print(f"[green]OK[/green] CIK:         {result.get('cik', 'n/a')}")
    console.print(f"[green]OK[/green] As of date:  {result.get('as_of_date', 'n/a')}")

    if result.get("financials"):
        report = result["financials"]
        table = Table(title=f"Financials — {report.source_filing}", show_header=True)
        table.add_column("Metric", style="bold")
        table.add_column("Value (USD B)", justify="right")
        table.add_column("Period")
        table.add_column("Source")

        def _b(v: float) -> str:
            return f"{v / 1e9:.1f}"

        rows = [
            ("Revenue",          report.revenue),
            ("Gross Profit",     report.gross_profit),
            ("Operating Income", report.operating_income),
            ("Net Income",       report.net_income),
            ("Free Cash Flow",   report.free_cash_flow),
        ]
        for label, metrics in rows:
            if metrics:
                m = metrics[0]
                table.add_row(label, _b(m.value), m.period, m.source)

        table.add_row("Total Debt", _b(report.total_debt.value), report.total_debt.period, "XBRL")
        table.add_row(
            "Cash + STI",
            _b(report.cash_and_equivalents.value),
            report.cash_and_equivalents.period,
            "XBRL",
        )
        console.print(table)

    console.print("\n[dim]Specialists pending (Phase 1b): mgmt, filings, market, competitive[/dim]")
