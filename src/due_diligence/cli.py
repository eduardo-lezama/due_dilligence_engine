"""
CLI entry points para el Due Diligence system.

Qué hace este módulo:
    Define los comandos del CLI usando Typer. Es una capa thin — delega
    toda la lógica a los adapters y al grafo LangGraph. Solo formatea
    output con Rich.

Comandos disponibles:
    get-financials  — obtiene datos EDGAR directamente (Phase 0, debug)
    analyze         — corre el grafo completo con checkpoints interactivos

Flujo del comando analyze (Phase 1 + Phase 2):
    1. Corre hasta checkpoint1 → muestra especialistas + preguntas Socratic
    2. Pide peers para ROIC comparison (skip con Enter) → peers_node fetcha XBRL
    3. Corre hasta checkpoint2 → muestra CAGR histórico + pide assumptions DCF
    4. Corre hasta END → muestra VERDICT + report completo con citations + 8-K events
    5. Informa ruta del markdown guardado

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

import structlog
import typer

log = structlog.get_logger()
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from due_diligence.config import settings
from due_diligence.utils.formatting import fmt_insider_ownership, fmt_usd as _fmt_usd

app = typer.Typer(help="Due Diligence — value investing analysis system")
console = Console()


# ---------------------------------------------------------------------------
# Helpers de display
# ---------------------------------------------------------------------------


def _show_financials_table(financials) -> None:
    table = Table(title=f"Financials — {financials.source_filing}", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
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
            table.add_row(label, _fmt_usd(m.value), m.period, m.source)

    if financials.total_debt:
        table.add_row("Total Debt", _fmt_usd(financials.total_debt.value), financials.total_debt.period, "XBRL")
    if financials.cash_and_equivalents:
        # Desde H1 (EC-81) cash_and_equivalents es SOLO caja; las STI tienen su propia fila.
        table.add_row("Cash (ex-STI)", _fmt_usd(financials.cash_and_equivalents.value),
                      financials.cash_and_equivalents.period, "XBRL")
    if financials.short_term_investments:
        table.add_row("Short-term investments", _fmt_usd(financials.short_term_investments.value),
                      financials.short_term_investments.period, "XBRL")
    # Capital returns (ADR-023): buybacks + dividends del cash flow statement
    if financials.buybacks:
        b = financials.buybacks[0]
        table.add_row("Buybacks", _fmt_usd(b.value), b.period, "XBRL")
    if financials.dividends:
        d = financials.dividends[0]
        table.add_row("Dividends paid", _fmt_usd(d.value), d.period, "XBRL")
    if financials.roic_series:
        r = financials.roic_series[0]
        table.add_row("ROIC", f"{r.value:.1%}", r.period, "calculated")
    console.print(table)


def _show_failures(state: dict) -> None:
    """Panel rojo con los agentes que fallaron (fail loud también en la UI, ADR-032).

    Usa archiver.collect_failures: un solo criterio de "fallo" para report y CLI.
    Se llama en checkpoint 1 (antes de "Proceed?") y antes del VERDICT final.
    """
    from due_diligence.agents.archiver import collect_failures
    failures = collect_failures(state)
    if not failures:
        return
    body = "\n".join(f"[bold]{label}[/bold]: {err}" for label, err in failures)
    if any(label == "Value Expert" for label, _ in failures):
        body += "\n\n[bold red]No VERDICT was produced. Do not act on this report.[/bold red]"
    console.print(Panel(body, title="ANALYSIS INCOMPLETE", border_style="red"))


def _models_line(run_stats) -> str:
    """'Models: gpt-4.1 ×3, gpt-5.4-mini ×2' desde RunStats.calls_by_model ('' si vacío)."""
    calls = getattr(run_stats, "calls_by_model", None) or {}
    if not calls:
        return ""
    return "Models: " + ", ".join(f"{m} ×{n}" for m, n in sorted(calls.items()))


def _show_checkpoint1_summary(state: dict) -> None:
    """Muestra el resumen de los 4 especialistas en checkpoint1."""
    console.print("\n[bold yellow]-- Checkpoint 1: Specialist Results --[/bold yellow]\n")

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Specialist", style="bold")
    table.add_column("Key Result")
    table.add_column("Status")

    mgmt = state.get("mgmt")
    if mgmt and not mgmt.stub:
        ownership = fmt_insider_ownership(mgmt.insider_ownership_pct, mgmt.insider_ownership_shares,
                                          mgmt.insider_ownership_below_1pct,
                                          mgmt.insider_ownership_multi_class_row,
                                          as_of=mgmt.insider_ownership_as_of) or "N/A"
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
        mcap = _fmt_usd(market.market_cap.value) if market.market_cap else "N/A"
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


def _render_socratic_questions(state: dict) -> dict:
    """
    Muestra las preguntas Socratic generadas por _build_socratic_questions
    y recoge respuestas free-text del usuario (ADR-018).

    Las respuestas se devuelven como dict {qN: {question, answer}} para incluirlas
    en el resume value del checkpoint1 → llegan al state y al Value Expert.
    Skip permitido: respuesta vacía (Enter) = no se registra.
    """
    from due_diligence.agents.checkpoint import _build_socratic_questions

    questions = _build_socratic_questions(state)
    if not questions:
        return {}

    console.print("\n[bold yellow]-- Socratic Questions (ADR-018) --[/bold yellow]")
    console.print("[dim]Answer with your own knowledge — or press Enter to skip each.[/dim]\n")

    answers: dict = {}
    for i, q in enumerate(questions, 1):
        console.print(f"[bold]{i}.[/bold] {q}")
        ans = typer.prompt("   Your answer", default="")
        if ans.strip():
            answers[f"q{i}"] = {"question": q, "answer": ans.strip()}
        console.print()

    return answers


def _pending_interrupt_payload(result, graph=None, config=None) -> Optional[dict]:
    """
    Payload del interrupt() pendiente tras graph.invoke(), o None si el grafo terminó.

    LangGraph 1.1.8 (verificado en pregel/main.py, `invoke` con stream_mode="values"): si el
    run se pausa, el dict devuelto lleva la clave "__interrupt__" con una lista de
    `Interrupt(value=..., id=...)`. Fallback: `graph.get_state(config).interrupts` (StateSnapshot).
    Antes la CLI ignoraba este payload y recalculaba sus propios defaults (WACC 9,0 fijo).
    """
    ints = result.get("__interrupt__") if isinstance(result, dict) else None
    if not ints and graph is not None:
        ints = getattr(graph.get_state(config), "interrupts", None)
    if not ints:
        return None
    value = getattr(ints[0], "value", None)
    return value if isinstance(value, dict) else None


def _ask_float(label: str, default: float) -> float:
    """Pregunta un float; entrada inválida → vuelve a preguntar (antes caía al default en silencio)."""
    while True:
        raw = typer.prompt(label, default=f"{default:.1f}")
        try:
            return float(raw)
        except ValueError:
            console.print(f"[red]'{raw}' is not a number[/red]")


def _ask_wacc_and_terminal(default_wacc: float, default_g: float = 2.5) -> tuple[float, float]:
    """Pregunta WACC y g terminal hasta que validate_dcf_rates acepte (fail loud, EC-76)."""
    from due_diligence.agents.valuation_math import ValuationInputError, validate_dcf_rates

    while True:
        w = _ask_float("  WACC (%)", default_wacc)
        g = _ask_float("  Terminal growth (%)", default_g)
        try:
            validate_dcf_rates(w / 100, g / 100)
            return w, g
        except ValuationInputError as e:
            console.print(f"[red]{e}[/red]")


def _ask_dcf_assumptions(payload: dict, state: dict) -> dict:
    """
    Muestra el hint de valoración y recoge los assumptions DCF del usuario.

    `payload` es el valor del interrupt() de checkpoint 2 (`_build_checkpoint2_summary`):
    trae el WACC real sugerido y su desglose (ADR-033) y los defaults únicos
    (CHECKPOINT2_DEFAULTS). `state` se usa solo para el contexto visual y el growth auto.
    """
    from due_diligence.agents.checkpoint import CHECKPOINT2_DEFAULTS

    console.print("\n[bold yellow]-- Checkpoint 2: DCF Assumptions --[/bold yellow]\n")
    payload = payload or {}
    defaults = {**CHECKPOINT2_DEFAULTS, **(payload.get("defaults") or {})}
    context = payload.get("context") or {}
    if payload.get("error"):
        # El nodo rechazó el resume anterior (WACC − g < 1pp): mostrarlo en rojo y re-preguntar.
        console.print(f"[bold red]{payload['error']}[/bold red]")

    # Mostrar contexto disponible
    financials = state.get("financials")
    market = state.get("market")

    if financials and financials.free_cash_flow:
        fcf = financials.free_cash_flow[0]
        console.print(f"  Latest FCF:    [cyan]{_fmt_usd(fcf.value)}[/cyan] ({fcf.period})")
    if market and market.market_cap:
        console.print(f"  Market cap:    [cyan]{_fmt_usd(market.market_cap.value)}[/cyan]")
    if market and market.price_current:
        console.print(f"  Current price: [cyan]${market.price_current.value:.2f}[/cyan]")
    if financials and financials.roic_series:
        r = financials.roic_series[0]
        console.print(f"  Latest ROIC:   [cyan]{r.value:.1%}[/cyan] ({r.period})")
    cyc_hint = context.get("cyclicality") or ""
    if cyc_hint.startswith("Cyclical: yes"):
        # EC-92: visible ANTES de pedir g1 — el CAGR de una cíclica no es un ancla fiable.
        console.print(f"  [yellow]{cyc_hint}[/yellow]")
    console.print()

    def _ask_int(prompt: str, default: int) -> int:
        val = typer.prompt(prompt, default=str(default))
        try:
            return int(val)
        except ValueError:
            return default

    # WACC real sugerido (ADR-033) — viene del payload del checkpoint, no se recalcula aquí.
    from due_diligence.agents.valuation import _compute_near_term_growth, _resolve_stages
    default_wacc = float(defaults["wacc_pct"])
    console.print(
        f"\n[dim]  Suggested WACC (real, Ke + Kd): [bold]{default_wacc:.1f}%[/bold] "
        f"— {context.get('wacc_breakdown', 'breakdown unavailable')}[/dim]"
    )
    console.print(
        "[dim]  Note: β=1.0 is a simplification — override manually if you have a better estimate.[/dim]"
    )

    wacc, terminal = _ask_wacc_and_terminal(default_wacc, float(defaults["terminal_growth_pct"]))
    years = _ask_int("  Projection years (e.g. 10)", int(defaults["projection_years"]))

    # 3-stage split: mostrar el split automático y permitir override (ADR-029)
    auto_s1, auto_fd = _resolve_stages(years, None, None)
    console.print(
        f"\n[dim]  3-stage DCF split (auto): [bold]{auto_s1}y[/bold] constant growth "
        f"+ [bold]{auto_fd}y[/bold] fade to terminal.[/dim]"
    )
    console.print(
        "[dim]  To override, enter 'S1,FADE' (e.g. '3,4'). Press Enter to accept auto split.[/dim]"
    )
    stage_input = typer.prompt("  Stage split (Enter for auto)", default="auto")
    stage1_override: Optional[int] = None
    fade_override: Optional[int] = None
    if stage_input.strip().lower() != "auto":
        try:
            parts = stage_input.split(",")
            stage1_override = int(parts[0].strip())
            fade_override = int(parts[1].strip()) if len(parts) > 1 else years - stage1_override
        except (ValueError, IndexError):
            console.print("[yellow]  ⚠ Invalid split format — using auto split.[/yellow]")

    # EC-54: pre-computar el valor auto y mostrarlo ANTES del prompt de override.
    # El usuario debe saber qué está overrideando antes de decidir si lo hace.
    auto_growth, auto_source = _compute_near_term_growth(
        financials=state.get("financials"),
        revenue_cagr=state.get("historical_cagr_5y"),
        user_override_pct=None,
    )
    _SOURCE_LABELS = {
        "default_7pct": "generic fallback (no historical data)",
        "revenue_cagr_fallback": "revenue CAGR proxy (FCF unavailable)",
        "cyclical_terminal": "cyclical business: terminal growth (EC-92) — override only for structural growth",
    }
    source_desc = _SOURCE_LABELS.get(auto_source, auto_source.replace("_", " "))
    console.print(
        f"\n[dim]  Auto near-term growth (g1): [bold]{auto_growth:.1%}[/bold] ({source_desc})[/dim]"
    )
    console.print(
        f"[dim]  Applied for {auto_s1 if stage1_override is None else stage1_override}y, "
        f"then fades linearly to terminal growth.[/dim]"
    )
    if "rev_capped" in auto_source:
        console.print(
            "[yellow]  ⚠ FCF CAGR was capped by revenue CAGR — "
            "FCF cannot sustainably grow faster than revenue.[/yellow]"
        )
    if auto_source == "default_7pct":
        console.print(
            "[red]  ⚠ No historical data — 7% is a generic estimate. "
            "Consider providing your own estimate.[/red]"
        )

    growth_input = typer.prompt(
        f"  Near-term FCF growth % override (Enter to accept {auto_growth:.1%})",
        default="auto",
    )
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
        # Default único (False en H1, ADR-033 §7); se envía explícito para que el report lo refleje.
        "mid_year_convention": bool(defaults["mid_year_convention"]),
    }
    if stage1_override is not None:
        result["stage1_years"] = stage1_override
    if fade_override is not None:
        result["fade_years"] = fade_override
    if growth is not None:
        result["revenue_growth_yr1_pct"] = growth

    return result


def _show_valuation(state: dict) -> None:
    valuation = state.get("valuation")
    if valuation is not None and valuation.stub and valuation.error:
        # ValuationInputError capturado en el nodo (ADR-033 §6): visible, no un hueco mudo.
        console.print(Panel(valuation.error, title="VALUATION NOT COMPUTED", border_style="red"))
        return
    if not valuation or valuation.stub:
        return

    table = Table(title="Valuation", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    # Plano enterprise (ADR-033): EV del DCF → net claims → equity → por acción.
    if valuation.enterprise_value_dcf:
        table.add_row("Enterprise value (DCF)", _fmt_usd(valuation.enterprise_value_dcf.value))
    if valuation.equity_bridge is not None:
        table.add_row("Net claims (debt + NCI + pref − cash − STI − LTI)",
                      _fmt_usd(valuation.equity_bridge.net_claims))
    if valuation.dcf_intrinsic_value:
        table.add_row("DCF equity value (after bridge)", _fmt_usd(valuation.dcf_intrinsic_value.value))
    if valuation.dcf_intrinsic_per_share:
        table.add_row("Per share", f"${valuation.dcf_intrinsic_per_share.value:.2f}")
    if valuation.dcf_upside_pct is not None:
        color = "green" if valuation.dcf_upside_pct > 0 else "red"
        table.add_row("DCF upside vs price", f"[{color}]{valuation.dcf_upside_pct:+.1f}%[/{color}]")
    if valuation.reverse_dcf_implied_growth is not None:
        table.add_row("Implied growth (reverse DCF)", f"{valuation.reverse_dcf_implied_growth:.1%}")
    if valuation.ev_ebit_multiple:
        table.add_row("EV/EBIT", f"{valuation.ev_ebit_multiple.value:.1f}x")
    if valuation.ev_fcf_multiple:
        table.add_row("EV/FCFF", f"{valuation.ev_fcf_multiple.value:.1f}x")
    if valuation.pe_multiple:
        table.add_row("P/E", f"{valuation.pe_multiple.value:.1f}x")
    if valuation.earnings_yield_pct is not None:
        table.add_row("Earnings yield (EBIT/EV)", f"{valuation.earnings_yield_pct:.1f}%")
    if valuation.epv_upside_pct is not None:
        epv_color = "green" if valuation.epv_upside_pct > 0 else "red"
        table.add_row(
            "EPV upside (zero-growth floor)",
            f"[{epv_color}]{valuation.epv_upside_pct:+.1f}%[/{epv_color}]",
        )
    console.print(table)

    if valuation.dcf_reliability_note:
        console.print(f"\n[bold yellow]{valuation.dcf_reliability_note}[/bold yellow]")

    if valuation.assumptions:
        a = valuation.assumptions
        stage_str = ""
        if valuation.stage1_years_used is not None and valuation.fade_years_used is not None:
            stage_str = f" · {valuation.stage1_years_used}y+{valuation.fade_years_used}y fade"
        console.print(
            f"[dim]  DCF: WACC={a.wacc_pct}%, terminal={a.terminal_growth_pct}%, {a.projection_years}y{stage_str}[/dim]"
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
            else:
                ps_table.add_row(
                    "DCF intrinsic (per share)",
                    "[dim]N/A[/dim]",
                    "[dim]FCF negativo — DCF no computable[/dim]",
                )
            # EPV per share — piso de valor sin crecimiento (Greenwald, ADR-029)
            if valuation.epv_per_share is not None:
                epv_delta = ""
                if price_mv is not None and price_mv.value > 0:
                    epv_pct = (valuation.epv_per_share.value - price_mv.value) / price_mv.value * 100
                    epv_color = "green" if epv_pct > 0 else "red"
                    epv_delta = f"[{epv_color}]{epv_pct:+.1f}% vs market[/{epv_color}]"
                src = valuation.epv_source or "EBIT median"
                ps_table.add_row(
                    f"EPV *(zero-growth, {src})*",
                    f"${valuation.epv_per_share.value:.2f}",
                    epv_delta,
                )

            # Equity value total para contexto
            if valuation.dcf_intrinsic_value:
                mcap_str = ""
                if valuation.dcf_upside_pct is not None:
                    color = "green" if valuation.dcf_upside_pct > 0 else "red"
                    mcap_str = f"[{color}]{valuation.dcf_upside_pct:+.1f}% vs price[/{color}]"  # upside por acción (ADR-033)
                ps_table.add_row(
                    "DCF equity value (after bridge)",
                    _fmt_usd(valuation.dcf_intrinsic_value.value),
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
            mult_table.add_row("EV/FCFF", f"{valuation.ev_fcf_multiple.value:.1f}x")
        if valuation.pe_multiple:
            mult_table.add_row("P/E", f"{valuation.pe_multiple.value:.1f}x")
        if valuation.earnings_yield_pct is not None:
            mult_table.add_row("Earnings yield (EBIT/EV)", f"{valuation.earnings_yield_pct:.1f}%")
        if valuation.magic_formula_rank is not None:
            _pvs = state.get("peer_valuations") or []
            _total = len([pv for pv in _pvs if pv.mf_rank is not None]) + 1
            mult_table.add_row("Magic Formula rank", f"#{valuation.magic_formula_rank} of {_total}")
        if valuation.assumptions:
            a = valuation.assumptions
            _stage_str = ""
            if valuation.stage1_years_used is not None and valuation.fade_years_used is not None:
                _stage_str = f" · {valuation.stage1_years_used}y+{valuation.fade_years_used}y fade"
            mult_table.add_row(
                "[dim]DCF assumptions[/dim]",
                f"[dim]WACC {a.wacc_pct}% / g={a.terminal_growth_pct}% / {a.projection_years}y{_stage_str}[/dim]",
            )
            if valuation.suggested_wacc_pct is not None:
                mult_table.add_row(
                    "[dim]WACC advisory[/dim]",
                    f"[dim]{valuation.suggested_wacc_pct:.1f}% ({valuation.suggested_wacc_breakdown})[/dim]",
                )
        if mult_table.row_count:
            console.print(mult_table)
        # Convergence table (ADR-030)
        if valuation.valuation_methods_summary:
            conv_table = Table(title="Valuation Methods Convergence", box=box.SIMPLE_HEAVY)
            conv_table.add_column("Method", style="bold")
            conv_table.add_column("Signal", justify="right")
            conv_table.add_column("", justify="center")
            _cls_map = {
                "cheap": "[green]cheap[/green]",
                "expensive": "[red]expensive[/red]",
                "fair": "[yellow]fair[/yellow]",
                "n/a": "[dim]n/a[/dim]",
            }
            for _row in valuation.valuation_methods_summary:
                _cls = _cls_map.get(_row["classification"], _row["classification"])
                conv_table.add_row(_row["method"], _row.get("display_upside", "—"), _cls)
            console.print(conv_table)
            _cheap_n = sum(1 for r in valuation.valuation_methods_summary if r["classification"] == "cheap")
            _total_n = len(valuation.valuation_methods_summary)
            console.print(f"[dim]  Convergence: {_cheap_n}/{_total_n} cheap[/dim]")

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


def _show_peer_comparison(state: dict) -> None:
    """Muestra la tabla de peer ROIC comparison con tendencia histórica."""
    competitive = state.get("competitive")
    if not competitive or competitive.stub or not competitive.peer_roic_comparison:
        return

    financials = state.get("financials")
    ticker = state.get("ticker", "?")

    table = Table(title="Peer Comparison — ROIC", box=box.SIMPLE_HEAVY)
    table.add_column("Ticker", style="bold")
    table.add_column("Latest ROIC", justify="right")
    table.add_column("Trend (oldest→latest)", justify="left", style="dim")

    # Primera fila: empresa analizada (siempre presente — EC-20)
    if financials and financials.roic_series:
        own_series = financials.roic_series[:5]  # serie completa (EC-38)
        own_trend = " → ".join(f"{r.value:.1%}" for r in reversed(own_series))
        own_latest = own_series[0]
        table.add_row(
            f"[cyan]{ticker}[/cyan]",
            f"[bold cyan]{own_latest.value:.1%}[/bold cyan] ({own_latest.period})",
            own_trend,
        )
    else:
        # ROIC no calculable — fila explícita en lugar de subject ausente
        table.add_row(
            f"[cyan]{ticker}[/cyan]",
            "[yellow]N/A — IC indeterminate[/yellow]",
            "—",
        )

    for pr in competitive.peer_roic_comparison:
        if pr.roic_trail and len(pr.roic_trail) > 1:
            trend = " → ".join(f"{r['roic']:.1%}" for r in reversed(pr.roic_trail))
        else:
            trend = "—"
        table.add_row(pr.ticker, f"{pr.roic:.1%} ({pr.period})", trend)

    console.print(table)
    console.print()

    # Peer EV/EBIT multiples table (MCE-18) — segunda tabla si hay datos de valoración
    peer_valuations_list = state.get("peer_valuations") or []
    has_multiples = any(pv.ev_ebit is not None or pv.fcf_yield_pct is not None for pv in peer_valuations_list)
    if has_multiples:
        valuation_state = state.get("valuation")
        mult_t = Table(title="Peer Comparison — Multiples", box=box.SIMPLE_HEAVY)
        mult_t.add_column("Ticker", style="bold")
        mult_t.add_column("EV/EBIT", justify="right")
        mult_t.add_column("FCF Yield", justify="right")
        mult_t.add_column("MF Rank", justify="right")
        # Fila del subject
        subj_ev = (
            f"[bold cyan]{valuation_state.ev_ebit_multiple.value:.1f}x[/bold cyan]"
            if (valuation_state and valuation_state.ev_ebit_multiple) else "[dim]N/A[/dim]"
        )
        subj_mf = (
            f"[bold cyan]#{valuation_state.magic_formula_rank}[/bold cyan]"
            if (valuation_state and valuation_state.magic_formula_rank is not None) else "—"
        )
        mult_t.add_row(f"[cyan]{ticker}[/cyan]", subj_ev, "—", subj_mf)
        for pv in peer_valuations_list:
            if pv.no_market_data:
                mult_t.add_row(pv.ticker, "[dim]no data[/dim]", "[dim]no data[/dim]", "—")
            else:
                ev_s = f"{pv.ev_ebit:.1f}x" if pv.ev_ebit is not None else "[dim]N/A (EBIT≤0)[/dim]"
                fcf_s = f"{pv.fcf_yield_pct:.1f}%" if pv.fcf_yield_pct is not None else "[dim]N/A (FCF<0)[/dim]"
                rank_s = f"#{pv.mf_rank}" if pv.mf_rank is not None else "—"
                mult_t.add_row(pv.ticker, ev_s, fcf_s, rank_s)
        console.print(mult_t)
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
    import time
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command
    from due_diligence.graph.graph import build_graph
    from due_diligence.llm.client import get_client

    from due_diligence.tools.tavily import TavilyQuotaExceededError
    from due_diligence.tools.edgar import DataUnavailableError

    ticker = ticker.upper()
    # Iniciar timer y resetear acumulador de tokens/coste (ADR-021)
    t0 = time.time()
    llm_client = get_client()
    llm_client.reset_run_stats()

    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"{ticker}-latest"}}

    console.print(f"\n[bold cyan]Due Diligence Analysis — {ticker}[/bold cyan]\n")

    # ── Phase 1a + 1b: supervisor → financials → specialists ──────────────
    try:
        with console.status("[bold green]Running Phase 1a+1b (EDGAR + specialists)…"):
            graph.invoke({"ticker": ticker}, config=config)
    except TavilyQuotaExceededError as e:
        console.print("\n[bold red]Tavily quota exhausted — analysis aborted.[/bold red]")
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except DataUnavailableError as e:
        # Fail loud VISIBLE (T6): caja ausente, identidad contable rota, 10-K desalineado...
        # Antes salía como traceback de Python; ahora un panel rojo con el motivo.
        console.print(Panel(str(e), title="DATA UNAVAILABLE", border_style="red"))
        raise typer.Exit(1)

    state = graph.get_state(config).values

    # Mostrar financials
    if state.get("financials"):
        _show_financials_table(state["financials"])

    # Mostrar resumen de especialistas en checkpoint1
    _show_checkpoint1_summary(state)
    # "degraded" sin motivo no basta: el usuario ve qué agente falló y por qué
    # antes de decidir si continuar (ADR-032).
    _show_failures(state)

    # Preguntar si continuar
    if not typer.confirm("\nProceed to valuation?", default=True):
        console.print("[yellow]Analysis stopped at checkpoint 1.[/yellow]")
        return

    # ── Checkpoint 1 Socratic (ADR-018) ───────────────────────────────────
    # Renderizar preguntas ancladas en datos y recoger respuestas free-text.
    # Las respuestas enriquecen el contexto del Value Expert con conocimiento
    # tácito del usuario (e.g. "sé que AAPL tiene moat wider que 'narrow' porque X").
    socratic_answers = _render_socratic_questions(state)

    # Peer tickers (ADR-015) — la última pregunta Socratic ya lo cubre, pero
    # lo extraemos explícitamente para el resume value.
    peers_raw = typer.prompt(
        "\nPeer tickers (comma-separated, e.g. MSFT,GOOGL,AMZN)"
        "\n  — press Enter to skip (moat analysis will use 10-K only)",
        default="",
    )
    if peers_raw.strip():
        peers_display = [t.strip().upper() for t in peers_raw.split(",") if t.strip()]
        console.print(f"[dim]Peers: {', '.join(peers_display)}[/dim]")
    else:
        console.print("[dim]Peers skipped — moat analysis will use 10-K only.[/dim]")

    # ── Reanudar: checkpoint1 → peers_node → checkpoint2 ──────────────────
    # NOTE: Command(resume={}) no funciona en LangGraph — dict vacío es falsy
    # y LangGraph no procesa el resume. Usar un valor truthy explícito.
    with console.status("[bold green]Passing checkpoint 1 + fetching peer data…"):
        cp1_result = graph.invoke(
            Command(resume={"proceed": True, "peers": peers_raw, "answers": socratic_answers}),
            config=config,
        )

    state = graph.get_state(config).values
    # Payload del interrupt de checkpoint 2: WACC real sugerido + defaults únicos (ADR-033).
    cp2_payload = _pending_interrupt_payload(cp1_result, graph, config) or {}

    # ── Checkpoint 2 — CAGR anchor + assumptions DCF ──────────────────────
    # Mostrar CAGR histórico antes de pedir WACC/growth (ADR-018).
    cagr = state.get("historical_cagr_5y")
    if cagr is not None:
        console.print(f"\n[bold yellow]Historical 5y revenue CAGR (from XBRL): {cagr:.1%}[/bold yellow]")
        console.print(
            "Your proposed growth rate should be defensible against this anchor.\n"
        )
        growth_justification = typer.prompt(
            "Why do your DCF assumptions diverge (or align) with this historical rate?",
            default="",
        )
    else:
        growth_justification = ""

    # ── Reanudar: checkpoint2 → valuation → expert → critic → archiver ────
    # Bucle: si checkpoint2_node rechaza el resume (WACC − g < 1pp) vuelve a interrumpir con
    # "error" en el payload; la CLI ya valida antes, así que normalmente es una sola vuelta.
    while True:
        assumptions = _ask_dcf_assumptions(cp2_payload, state)
        if growth_justification:
            assumptions["growth_justification"] = growth_justification
        with console.status("[bold green]Running valuation + expert + critic + archiver…"):
            cp2_result = graph.invoke(Command(resume=assumptions), config=config)
        pending = _pending_interrupt_payload(cp2_result, graph, config)
        if not pending or pending.get("phase") != "checkpoint2":
            break
        cp2_payload = pending

    state = graph.get_state(config).values

    # Mostrar resultados finales
    console.print("\n[bold cyan]-- Analysis Complete --[/bold cyan]\n")
    _show_valuation(state)
    console.print()
    _show_failures(state)
    _show_verdict(state)
    _show_peer_comparison(state)
    _show_critic(state)

    # ── Performance metadata (ADR-021) ────────────────────────────────────
    latency_ms = (time.time() - t0) * 1000
    run_stats = llm_client.get_run_stats()
    cost_eur = run_stats.cost_usd * settings.usd_eur_rate
    run_id = state.get("run_id", "")

    # Mostrar resumen de performance en CLI
    console.print(
        f"\n[dim]⏱  {latency_ms/1000:.1f}s  |  "
        f"🪙  {cost_eur:.4f}€  "
        f"({run_stats.tokens_in:,} in + {run_stats.tokens_out:,} out tokens, "
        f"{run_stats.calls} LLM calls)[/dim]"
    )
    models_line = _models_line(run_stats)
    if models_line:
        console.print(f"[dim]{models_line}[/dim]")

    # Persistir en SQLite y appender al markdown
    from datetime import date
    from due_diligence.agents.archiver import report_filename
    # Mismo nombre que escribió el archiver (un solo sitio decide BUY/HOLD/SELL/INCOMPLETE).
    report_file = Path(settings.reports_dir).resolve() / report_filename(
        date.today().isoformat(), ticker, state.get("synthesis"))
    if run_id:
        try:
            from due_diligence.agents.archiver import _format_models_used, update_performance_metrics
            update_performance_metrics(
                settings.db_path, run_id, latency_ms, cost_eur,
                run_stats.tokens_in, run_stats.tokens_out,
                models_used=_format_models_used(run_stats) or None,
            )
        except Exception as e:
            log.warning("performance_update_failed", error=str(e))

    if report_file.exists():
        try:
            perf_block = (
                f"\n## Performance\n\n"
                f"| Metric | Value |\n|---|---|\n"
                f"| Latency | {latency_ms/1000:.1f}s |\n"
                f"| Cost | {cost_eur:.4f}€ (${run_stats.cost_usd:.4f} USD) |\n"
                f"| Tokens | {run_stats.tokens_in:,} in / {run_stats.tokens_out:,} out |\n"
                f"| LLM calls | {run_stats.calls} |\n"
                f"| Models | {_models_line(run_stats).removeprefix('Models: ') or 'n/a'} |\n"
            )
            with open(report_file, "a", encoding="utf-8") as f:
                f.write(perf_block)
            console.print(f"[bold green]Report saved:[/bold green] {report_file}")
        except Exception as e:
            log.warning("performance_append_failed", error=str(e))
            console.print(f"[bold green]Report saved:[/bold green] {report_file}")
