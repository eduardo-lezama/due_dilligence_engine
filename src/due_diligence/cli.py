from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

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
