"""
report_facts — hechos deterministas compartidos por el informe, el contexto del Expert y la consola.

Por qué existe (bloque B, B2): la misma cifra se formateaba tres veces (value_expert._format_context,
archiver._render_markdown, cli._show_verdict) y un arreglo en un sitio no llegaba a los otros dos
(EC-14 → EC-17). Aquí se calcula y redacta cada hecho UNA vez; los tres consumidores solo eligen el
formato (tabla markdown, líneas para el LLM, tabla rich). Sin LLM, sin I/O salvo `previous_analysis`,
que lee SQLite en solo lectura.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from typing import Optional

from due_diligence.utils.formatting import fmt_usd
from due_diligence.utils.periods import fiscal_year

# Tolerancia de la etiqueta de tendencia del ROIC: un cambio menor que el 5 % del nivel de partida
# (o que 1 pp si el nivel es casi 0) es ESTABLE.
_ROIC_TREND_REL_TOL = 0.05
_ROIC_TREND_ABS_TOL = 0.01


@dataclass
class Section:
    """Bloque de hechos: título, filas (etiqueta, valor) y notas. Lo renderiza cada consumidor."""
    title: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_markdown(self, level: int = 3) -> list[str]:
        """Tabla markdown de dos columnas + notas en cursiva (informe)."""
        lines = [f"{'#' * level} {self.title}", ""]
        if self.rows:
            lines += ["| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in self.rows]
            lines.append("")
        lines += [f"*{n}*" for n in self.notes]
        return lines + ([""] if self.notes else [])

    def as_context(self) -> list[str]:
        """Líneas planas para el contexto del LLM (sin sintaxis de tabla)."""
        lines = [f"## {self.title.upper()} (deterministic)"]
        lines += [f"{k}: {v}" for k, v in self.rows]
        lines += [f"Note: {n}" for n in self.notes]
        return lines + [""]


def roic_trend(roic_series) -> tuple[Optional[str], str]:
    """Etiqueta (RISING / FALLING / STABLE) y descripción de la tendencia del ROIC.

    Compara el último ejercicio con el más antiguo dentro de los 4 años fiscales anteriores (T7),
    medido en AÑOS FISCALES. La serie se ordena por año antes de comparar (en informes anteriores a
    F0 el último año aparecía al final de la serie y BBW salía "RISING" con el ROIC bajando).
    La tolerancia es ABSOLUTA (5 % del nivel de partida, mínimo 1 pp): la antigua comparación
    multiplicativa `último > antiguo × 1,05` daba RISING con ROIC negativos que empeoraban
    (−28 % → −29 %). Devuelve (None, motivo) si no hay con qué comparar.
    """
    try:
        series = sorted(roic_series or [], key=lambda m: fiscal_year(m.period), reverse=True)
    except ValueError:            # etiquetas no FY: se respeta el orden recibido
        series = list(roic_series or [])
    if len(series) < 2:
        return None, "n/a (fewer than 2 years of ROIC)"
    latest = series[0]
    try:
        y0 = fiscal_year(latest.period)
        window = [r for r in series[1:] if 0 < y0 - fiscal_year(r.period) <= 4]
        oldest = window[-1] if window else None
        years = y0 - fiscal_year(oldest.period) if oldest else 0
    except ValueError:
        oldest = series[min(4, len(series) - 1)]
        years = series.index(oldest)
    if oldest is None:
        return None, "n/a (no ROIC data within the last 4 fiscal years to compare)"
    change = latest.value - oldest.value
    tol = max(_ROIC_TREND_ABS_TOL, _ROIC_TREND_REL_TOL * abs(oldest.value))
    per_year_pp = change / max(years, 1) * 100
    label = "RISING" if change > tol else "FALLING" if change < -tol else "STABLE"
    return label, (f"{label} ({oldest.period} {oldest.value:.1%} → {latest.period} {latest.value:.1%}, "
                   f"avg ΔROIC {per_year_pp:+.1f}pp/year over {max(years, 1)} years)")


def filings_coverage_gaps(filings) -> list[tuple[str, str]]:
    """Secciones del 10-K no encontradas o recortadas (EC-106): huecos parciales del Filings agent."""
    if filings is None or getattr(filings, "stub", True):
        return []
    out = []
    if filings.sections_missing:
        out.append(("Filings", f"10-K sections not found: {', '.join(filings.sections_missing)} — "
                               f"no evidence from them"))
    if filings.sections_truncated:
        out.append(("Filings", f"10-K sections truncated to their first part: "
                               f"{', '.join(filings.sections_truncated)}"))
    return out



# --------------------------------------------------------------------------- #
# Secciones del informe de una página (bloque B, B2)
# --------------------------------------------------------------------------- #

def _pct(v: Optional[float], digits: int = 1) -> str:
    return f"{v:.{digits}%}" if v is not None else "n/a"


def _usd(v: Optional[float]) -> str:
    return f"${v:,.2f}" if v is not None else "n/a"


def _scenario_return(sc) -> str:
    """Rentabilidad de un escenario, o el motivo de que no haya cifra (nunca un "n/a" mudo)."""
    if sc.implied_return is not None:
        return _pct(sc.implied_return)
    return f"n/a — {sc.implied_return_note}" if sc.implied_return_note else "n/a"


def _scenario(valuation, name: str):
    return next((sc for sc in (valuation.dcf_scenarios or []) if sc.name == name), None)


def decision_inputs_section(valuation, market) -> Section:
    """Rentabilidad frente al listón y precio de entrada (ADR-044): lo que decide BUY / WATCHLIST.

    La ven el Expert (antes de elegir la etiqueta), el informe y la consola, con la misma redacción.
    """
    sec = Section("Decision inputs (expected return vs hurdle)")
    if valuation is None or valuation.stub:
        sec.notes.append("No valuation available: no entry price can be computed, so BUY is not possible.")
        return sec
    price = market.price_current.value if market is not None and market.price_current is not None else None
    bear = _scenario(valuation, "bear")
    implied = (_pct(valuation.implied_return) if valuation.implied_return is not None
               else f"n/a — {valuation.implied_return_note or 'not computable'}")
    sec.rows += [
        ("Current price", _usd(price)),
        ("Hurdle (required annual return)", f"{_pct(valuation.hurdle_rate)} — {valuation.hurdle_source or ''}".rstrip(" —")),
        ("Expected annual return at today's price — base case", implied),
        ("Expected annual return — bear case", _scenario_return(bear) if bear is not None else
         "n/a (no bear scenario)"),
        ("Risk-free rate (floor for the bear case)", _pct(valuation.risk_free_rate, 2)),
        ("Entry price", f"{_usd(valuation.entry_price)} ({valuation.entry_basis})" if valuation.entry_price
         else f"n/a ({valuation.entry_basis or 'not computable'})"),
    ]
    if price is not None and valuation.entry_price:
        status = "at or below the entry price" if price <= valuation.entry_price else (
            f"{price / valuation.entry_price - 1:.0%} above the entry price")
        sec.rows.append(("Price vs entry", status))
    sec.notes.append("Expected return = the annual return a buyer at today's price earns if the scenario "
                     "plays out (same cash flows, bridge and share count as the DCF). Entry price = highest "
                     "price at which the base case still returns the hurdle and the bear case returns at "
                     "least the risk-free rate.")
    return sec


def price_implies_section(valuation, state) -> Section:
    """'What the price implies' (A4 → bloque B): rentabilidad implícita primero, crecimiento implícito después.

    El crecimiento implícito asume RONIC = WACC después del año 10 (el crecimiento posterior no vale
    nada), así que sobrestima lo que "exige" el precio de un negocio con ventaja duradera (revisión
    2026-10-17, S5). Por eso es dato secundario y lo dice.
    """
    sec = Section("What the price implies")
    if valuation is None or valuation.stub:
        return sec
    if valuation.implied_return is not None and valuation.hurdle_rate is not None:
        gap = valuation.implied_return - valuation.hurdle_rate
        verb = "above" if gap >= 0 else "below"
        sec.rows.append(("Expected return vs hurdle",
                         f"{_pct(valuation.implied_return)} vs {_pct(valuation.hurdle_rate)} "
                         f"({abs(gap) * 100:.1f} pp {verb})"))
    elif valuation.implied_return_note:
        sec.rows.append(("Expected return vs hurdle", f"n/a — {valuation.implied_return_note}"))
    g, g10 = valuation.reverse_dcf_implied_growth, valuation.reverse_dcf_10y_equivalent
    cagr, years = state.get("historical_cagr_5y"), state.get("historical_cagr_years") or 5
    if g is not None:
        implied = f"{_pct(g)} for {valuation.stage1_years_used}y, fading" + (
            f" (~{_pct(g10)} a year over 10y)" if g10 is not None else "")
        sec.rows.append(("Market-implied FCFF growth", implied))
    if cagr is not None:
        sec.rows.append((f"Revenue growth, last {years} years", _pct(cagr)))
    if g is not None:
        sec.notes.append("Implied growth assumes growth after year 10 creates no value (RONIC = WACC), so it "
                         "overstates what the price requires from a business with a durable advantage. "
                         "Read it as secondary to the expected return.")
    if valuation.growth_structural_break:
        sec.notes.append(valuation.growth_structural_break.removeprefix("⚠️ "))
    return sec


def value_range_section(valuation) -> Section:
    """Rango de valor: escenarios de la propia historia (A3) + EPV (suelo sin crecimiento) + DCF a WACC."""
    sec = Section("Value range (assumptions from the company's own history)")
    if valuation is None or valuation.stub:
        return sec
    for sc in valuation.dcf_scenarios or []:
        sec.rows.append((f"{sc.name.capitalize()} — FCFF margin {sc.fcff_margin:.1%}, growth {sc.near_term_growth:.1%}",
                         f"{_usd(sc.per_share)} per share"
                         + (f" ({sc.upside_pct:+.0f}% vs price)" if sc.upside_pct is not None else "")
                         + f"; expected return {_scenario_return(sc)}"))
    if not valuation.dcf_scenarios and valuation.dcf_intrinsic_per_share is not None:
        sec.rows.append(("DCF at the market cost of capital", f"{_usd(valuation.dcf_intrinsic_per_share.value)} per share"))
    if valuation.epv_per_share is not None:
        sec.rows.append((f"EPV — zero-growth floor ({valuation.epv_source or 'EBIT median'})",
                         f"{_usd(valuation.epv_per_share.value)} per share"
                         + (f" ({valuation.epv_upside_pct:+.0f}% vs price)" if valuation.epv_upside_pct is not None else "")))
    if valuation.dcf_scenarios:
        a = valuation.assumptions
        sec.notes.append(f"Values discounted at the market cost of capital (WACC {a.wacc_pct}%); the expected "
                         f"return is what a buyer at today's price earns in each scenario." if a else
                         "Values discounted at the market cost of capital.")
    return sec


def quality_section(financials) -> Section:
    """Calidad del negocio en cifras, sin prosa: ROIC, tendencia, ROIC incremental, conversión, scores, acciones."""
    sec = Section("Business quality")
    if financials is None:
        return sec
    roic = financials.roic_series or []
    if roic:
        med = statistics.median([r.value for r in roic[:5]])
        sec.rows.append(("ROIC (latest / 5y median)", f"{_pct(roic[0].value)} ({roic[0].period}) / {_pct(med)}"))
        label, text = roic_trend(roic)
        if label:
            sec.rows.append(("ROIC trend", text))
    if financials.incremental_roic is not None:
        sec.rows.append(("Incremental ROIC", f"{_pct(financials.incremental_roic.value)} ({financials.incremental_roic.period})"))
    fcf = {m.period: m.value for m in financials.free_cash_flow}
    conv = [fcf[n.period] / n.value for n in financials.net_income[:5] if n.period in fcf and n.value and n.value > 0]
    if len(conv) >= 3:
        sec.rows.append(("FCF / net income (5y median)", f"{statistics.median(conv):.2f}x"))
    shares = [m for m in financials.diluted_shares if m.value]
    if len(shares) >= 2:
        try:
            window = [m for m in shares[1:] if 0 < fiscal_year(shares[0].period) - fiscal_year(m.period) <= 5]
            old = window[-1] if window else None      # el más antiguo de la ventana de 5 años
        except ValueError:
            old = None
        if old is not None:
            n = fiscal_year(shares[0].period) - fiscal_year(old.period)
            change = (shares[0].value / old.value) ** (1 / n) - 1
            sec.rows.append(("Diluted share count, change per year", f"{change:+.1%} ({old.period} → {shares[0].period})"))
    scores = financials.accounting_scores
    if scores is not None:
        for r in (scores.piotroski, scores.altman_z2, scores.beneish_m, scores.sloan_accruals):
            sec.rows.append((r.name, f"{r.reading} ({r.period})"))
    return sec


def relative_section(state) -> Section:
    """Comparables: EV/EBIT y rendimiento FCF del sujeto frente a la mediana de los competidores."""
    sec = Section("Relative check (peers)")
    peers = state.get("peer_valuations") or []
    valuation = state.get("valuation")
    source = state.get("peers_source") or "user"
    if not peers:
        sec.notes.append("No peer multiples (no peers available or none with market data).")
        return sec
    names = ", ".join(pv.ticker for pv in peers)
    sec.rows.append(("Peers", f"{names} ({'suggested automatically (Finnhub, same industry)' if source == 'auto' else 'chosen by the investor'})"))
    ev_ebits = [pv.ev_ebit for pv in peers if pv.ev_ebit is not None]
    if ev_ebits and valuation is not None and not valuation.stub and valuation.ev_ebit_multiple is not None:
        med = statistics.median(ev_ebits)
        subj = valuation.ev_ebit_multiple.value
        sec.rows.append(("EV/EBIT vs peer median", f"{subj:.1f}x vs {med:.1f}x ({subj / med - 1:+.0%})"))
    yields = [pv.fcf_yield_pct for pv in peers if pv.fcf_yield_pct is not None]
    fin, market = state.get("financials"), state.get("market")
    if (yields and fin is not None and fin.free_cash_flow and market is not None and market.market_cap
            and market.market_cap.value and fin.free_cash_flow[0].value > 0):
        subj_y = fin.free_cash_flow[0].value / market.market_cap.value * 100
        sec.rows.append(("FCF yield vs peer median", f"{subj_y:.1f}% vs {statistics.median(yields):.1f}%"))
    if valuation is not None and not valuation.stub and valuation.magic_formula_rank is not None:
        ranked = [pv for pv in peers if pv.mf_rank is not None]
        sec.rows.append(("Magic Formula rank (earnings yield + ROIC, #1 = best)",
                         f"#{valuation.magic_formula_rank} of {len(ranked) + 1}"))
    excluded = [pv.ticker for pv in peers if pv.no_market_data or pv.ev_ebit is None]
    if excluded:
        sec.notes.append(f"Excluded from the medians (no market data or EBIT ≤ 0): {', '.join(excluded)}.")
    return sec


def previous_analysis(db_path: str, ticker: str, before_run_id: Optional[str] = None) -> Optional[dict]:
    """Último análisis COMPLETO anterior del mismo ticker en SQLite (solo lectura), o None.

    Columnas que no existan en una base antigua se devuelven como None (PRAGMA table_info).
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute("PRAGMA table_info(analyses)")}
            if not cols:
                return None
            wanted = ["analysis_date", "verdict", "price_at_analysis", "entry_price", "implied_return_pct",
                      "dcf_per_share", "run_id", "methodology_version"]
            select = ", ".join(c if c in cols else f"NULL AS {c}" for c in wanted)
            sql = (f"SELECT {select} FROM analyses WHERE ticker = ? AND verdict IS NOT NULL"
                   # "run_id != ?" solo es NULL con run_id NULL: las filas antiguas sin run_id se perderían.
                   + (" AND (run_id IS NULL OR run_id != ?)" if before_run_id else "") + " ORDER BY id DESC LIMIT 1")
            row = conn.execute(sql, (ticker, before_run_id) if before_run_id else (ticker,)).fetchone()
            return dict(row) if row else None
    except sqlite3.Error:
        return None


def changes_section(previous: Optional[dict], *, verdict: Optional[str], price: Optional[float],
                    entry: Optional[float], implied_return: Optional[float]) -> Section:
    """Qué ha cambiado desde el último análisis del mismo ticker (revisión 2026-10-17: CALM ×3 sin diff)."""
    sec = Section("Changes since the last analysis")
    if previous is None:
        sec.notes.append("First analysis of this ticker in the database.")
        return sec
    sec.rows.append(("Previous analysis", f"{previous['analysis_date']} — {previous['verdict']}"))
    if previous.get("price_at_analysis") and price:
        sec.rows.append(("Price", f"{_usd(previous['price_at_analysis'])} → {_usd(price)} "
                                  f"({price / previous['price_at_analysis'] - 1:+.0%})"))
    if previous.get("entry_price") and entry:
        sec.rows.append(("Entry price", f"{_usd(previous['entry_price'])} → {_usd(entry)}"))
    if previous.get("implied_return_pct") is not None and implied_return is not None:
        sec.rows.append(("Expected return", f"{previous['implied_return_pct']:.1f}% → {implied_return:.1%}"))
    if verdict and previous.get("verdict") and verdict != previous["verdict"]:
        sec.rows.append(("Verdict", f"{previous['verdict']} → {verdict}"))
    if previous.get("methodology_version") not in ("H2b",):
        sec.notes.append(f"The previous analysis used an older methodology "
                         f"({previous.get('methodology_version') or 'pre-H1'}): only price and verdict are comparable.")
    return sec


def market_snapshot_rows(valuation, market) -> list[tuple[str, str]]:
    """Market cap, EV y caja neta como % de la capitalización (la tesis de CALM no los mostraba)."""
    rows: list[tuple[str, str]] = []
    if market is not None and market.market_cap is not None:
        rows.append(("Market cap", fmt_usd(market.market_cap.value)))
    if valuation is not None and not valuation.stub and valuation.market_enterprise_value is not None:
        rows.append(("Enterprise value (market)", fmt_usd(valuation.market_enterprise_value.value)))
        if valuation.equity_bridge is not None and market is not None and market.market_cap is not None \
                and market.market_cap.value:
            claims = valuation.equity_bridge.net_claims
            share = abs(claims) / market.market_cap.value
            # net_claims > 0: deuda (+ minoritarios, preferentes) por encima de la caja e inversiones.
            rows.append(("Net debt and other claims (net of cash and investments) / market cap", f"{share:.0%}")
                        if claims > 0 else
                        ("Net cash and investments (net of debt and other claims) / market cap", f"{share:.0%}"))
    return rows
