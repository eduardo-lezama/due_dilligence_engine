"""
Tests para archiver_node.

Qué se testea:
    - archiver_node(): escribe markdown y fila SQLite correctamente.
    - _render_markdown(): contiene las secciones esperadas del report.
    - Degraded mode: corre con estado mínimo sin fallar.
    - SQLite: la fila insertada tiene los campos correctos.
    - El nodo devuelve {} (no modifica el estado).

Estrategia de aislamiento:
    Todos los paths de I/O se redirigen a tmp_path de pytest — zero escrituras
    a data/ real durante los tests.
"""

import sqlite3
import pytest
from pathlib import Path

from due_diligence.agents.archiver import archiver_node, _render_markdown, _render_citations_section, _ensure_db
from due_diligence.schemas.reports import (
    ValueExpertReport, CriticReport, ValuationReport, CompetitiveReport,
)
from due_diligence.schemas.base import MetricValue


def _make_full_state(tmp_path: Path) -> dict:
    """Estado completo con todos los reports para tests exhaustivos."""
    return {
        "ticker": "AAPL",
        "run_id": "test-run-abc123",
        "as_of_date": "2025-09-27",
        "synthesis": ValueExpertReport(
            ticker="AAPL", stub=False,
            business_understanding="Apple designs consumer electronics.",
            moat_analysis="Wide moat via brand and switching costs.",
            management_assessment="Tim Cook, 13 years, disciplined capital allocation.",
            financial_quality="FCF conversion >95%, net cash position.",
            growth_reinvestment="Services growing 13% CAGR.",
            valuation_section="DCF shows 20% upside at 9% WACC.",
            risks="App Store regulation, China supply chain.",
            psychological_lens="Priced as tech growth, behaves like consumer staples.",
            decision_frame="5-8% position for quality compounder.",
            verdict="BUY",
            verdict_confidence=0.72,
            suggested_sizing_pct=7.0,
        ),
        "critique": CriticReport(
            ticker="AAPL", stub=False,
            weaknesses=["iPhone revenue flat for 3 years.", "Regulatory risk underestimated."],
            contra_thesis="App Store margin depends on regulatory outcome.",
            pre_mortem_scenario="EU DMA enforcement reduces commissions, Services re-rates.",
        ),
        "valuation": ValuationReport(
            ticker="AAPL", stub=False,
            dcf_intrinsic_value=MetricValue(value=3_200e9, unit="USD", period="FY2025", source="calculated"),
            dcf_upside_pct=14.3,
            reverse_dcf_implied_growth=0.15,
            ev_ebit_multiple=MetricValue(value=22.1, unit="x", period="2026-04-21", source="calculated"),
            ev_fcf_multiple=MetricValue(value=28.3, unit="x", period="2026-04-21", source="calculated"),
            pe_multiple=MetricValue(value=25.0, unit="x", period="2026-04-21", source="calculated"),
            earnings_yield_pct=4.5,
            sensitivity_table=[
                {"near_term_growth_pct": 15.0, "values": {"wacc_8.0pct": 4100.0, "wacc_9.0pct": 3200.0, "wacc_10.0pct": 2600.0}},
                {"near_term_growth_pct": 10.0, "values": {"wacc_8.0pct": 3400.0, "wacc_9.0pct": 2700.0, "wacc_10.0pct": 2200.0}},
                {"near_term_growth_pct": 5.0,  "values": {"wacc_8.0pct": 2800.0, "wacc_9.0pct": 2300.0, "wacc_10.0pct": 1900.0}},
            ],
        ),
        "competitive": CompetitiveReport(ticker="AAPL", stub=False, moat_classification="wide"),
    }


# ---------------------------------------------------------------------------
# Tests: nodo completo
# ---------------------------------------------------------------------------

def test_archiver_returns_empty_dict(mocker, tmp_path):
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", str(tmp_path / "test.db"))
    result = archiver_node({"ticker": "AAPL"})
    assert result == {}


def test_archiver_writes_markdown_file(mocker, tmp_path):
    reports_dir = tmp_path / "reports"
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(reports_dir))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", str(tmp_path / "test.db"))

    state = _make_full_state(tmp_path)
    archiver_node(state)

    # Nuevo formato: <DATE>-<TICKER>-<VERDICT>.md
    md_files = list(reports_dir.glob("*-AAPL-*.md"))
    assert len(md_files) == 1
    # Verify the verdict appears in the filename (full state should produce BUY)
    assert "BUY" in md_files[0].name


def test_archiver_markdown_contains_verdict(mocker, tmp_path):
    reports_dir = tmp_path / "reports"
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(reports_dir))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", str(tmp_path / "test.db"))

    archiver_node(_make_full_state(tmp_path))

    md_file = list(reports_dir.glob("*-AAPL-*.md"))[0]
    content = md_file.read_text(encoding="utf-8")
    assert "BUY" in content
    assert "72%" in content


def test_archiver_inserts_sqlite_row(mocker, tmp_path):
    db_path = str(tmp_path / "test.db")
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", db_path)

    archiver_node(_make_full_state(tmp_path))

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM analyses WHERE ticker='AAPL'").fetchall()
    assert len(rows) == 1


def test_archiver_sqlite_row_has_correct_fields(mocker, tmp_path):
    db_path = str(tmp_path / "test.db")
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", db_path)

    archiver_node(_make_full_state(tmp_path))

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analyses WHERE ticker='AAPL'").fetchone()

    assert row["verdict"] == "BUY"
    assert abs(row["verdict_confidence"] - 0.72) < 0.01
    assert row["moat_classification"] == "wide"
    assert row["run_id"] == "test-run-abc123"


def test_archiver_degraded_minimal_state(mocker, tmp_path):
    """Con solo ticker, el archiver persiste un report parcial sin fallar."""
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", str(tmp_path / "test.db"))

    result = archiver_node({"ticker": "MSFT"})
    assert result == {}

    # Stub state sin VERDICT → filename usa "INCOMPLETE" (antes PENDING; ADR-032)
    md_files = list((tmp_path / "reports").glob("*-MSFT-*.md"))
    assert len(md_files) == 1
    assert "INCOMPLETE" in md_files[0].name


def test_archiver_multiple_runs_insert_multiple_rows(mocker, tmp_path):
    """Cada llamada inserta una fila nueva — permite historial de análisis."""
    db_path = str(tmp_path / "test.db")
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", db_path)

    archiver_node({"ticker": "AAPL"})
    archiver_node({"ticker": "AAPL"})

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM analyses WHERE ticker='AAPL'").fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# Tests: _render_markdown
# ---------------------------------------------------------------------------

def test_render_markdown_contains_ticker():
    md = _render_markdown({"ticker": "KO"})
    assert "KO" in md


def test_render_markdown_contains_all_expert_sections(tmp_path):
    # Bloque B (B2): §7 y §9 van en la primera página ("Key risks", "What would make this wrong");
    # el resto del análisis del Expert, en el apéndice. Ninguna sección se pierde ni se repite.
    state = _make_full_state(tmp_path)
    md = _render_markdown(state)
    front, appendix = md.split("# Appendix — detail")
    for section in ["§1", "§2", "§3", "§4", "§5", "§6", "§8"]:
        assert section in appendix
    syn = state["synthesis"]
    assert "## Key risks" in front and syn.risks in front and syn.risks not in appendix
    assert "## What would make this wrong" in front and syn.decision_frame in front
    assert syn.decision_frame not in appendix


def test_render_markdown_contains_critic_weaknesses(tmp_path):
    md = _render_markdown(_make_full_state(tmp_path))
    assert "iPhone revenue flat" in md
    assert "Regulatory risk" in md


def test_render_markdown_contains_sensitivity_table(tmp_path):
    md = _render_markdown(_make_full_state(tmp_path))
    assert "DCF Sensitivity" in md
    assert "wacc_9.0pct" in md


def test_render_markdown_handles_missing_sections():
    """Estado mínimo: el markdown se genera sin secciones opcionales."""
    md = _render_markdown({"ticker": "XYZ"})
    assert "XYZ" in md
    # Sin synthesis, no hay secciones del expert
    assert "§1" not in md


# ---------------------------------------------------------------------------
# Tests: _ensure_db
# ---------------------------------------------------------------------------

def test_ensure_db_creates_table(tmp_path):
    db_path = str(tmp_path / "sub" / "test.db")
    _ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analyses'"
        ).fetchall()
    assert len(tables) == 1


def test_ensure_db_is_idempotent(tmp_path):
    """Llamar dos veces no falla ni duplica la tabla."""
    db_path = str(tmp_path / "test.db")
    _ensure_db(db_path)
    _ensure_db(db_path)  # segunda llamada — no debe lanzar excepción


# ---------------------------------------------------------------------------
# Tests: citations section (ADR-014)
# ---------------------------------------------------------------------------

from due_diligence.schemas.reports import ManagementReport, MarketReport
from due_diligence.schemas.base import Citation


def test_render_markdown_includes_citations_section():
    """La sección '## Sources & Citations' aparece cuando hay citas en los reportes."""
    state = {
        "ticker": "AAPL",
        "as_of_date": "2025-09-28",
        "run_id": "abc123",
        "mgmt": ManagementReport(
            ticker="AAPL",
            stub=False,
            insider_ownership_pct=0.03,
            citations=[
                Citation(
                    claim="Tim Cook owns 3.3M shares",
                    source_url="https://sec.gov/Archives/edgar/data/320193/DEF14A.htm",
                    excerpt="Timothy D. Cook beneficially owns 3,279,321 shares",
                    confidence=0.95,
                    verified=True,   # T3c: "Sources & Citations" solo lista verificadas
                ),
            ],
        ),
    }
    md = _render_markdown(state)
    assert "## Sources & Citations" in md
    assert "Timothy D. Cook beneficially owns 3,279,321 shares" in md
    assert "https://sec.gov/Archives/edgar/data/320193/DEF14A.htm" in md
    assert "Management" in md


def test_render_markdown_no_citations_section_when_empty():
    """Sin citas, la sección '## Sources & Citations' no aparece."""
    md = _render_markdown({"ticker": "XYZ"})
    assert "## Sources & Citations" not in md


def test_render_markdown_includes_market_news_section():
    """La sección '## Market & News' se renderiza con la URL inline."""
    state = {
        "ticker": "AAPL",
        "market": MarketReport(
            ticker="AAPL",
            stub=False,
            recent_news_summary=(
                "Recent news (last 30 days, 1 articles):\n"
                "[1] Cook steps down, Ternus takes over.\n"
                "    Source: Reuters | URL: https://reuters.com/aapl-cook\n"
                "    Apple announced today..."
            ),
        ),
    }
    md = _render_markdown(state)
    assert "## Market & News" in md
    assert "https://reuters.com/aapl-cook" in md


# ---------------------------------------------------------------------------
# Tests: Peer Relative Valuation (MCE-18)
# ---------------------------------------------------------------------------

def _make_peer_valuations_for_archiver():
    """Two PeerValuation objects with all multiples set for archiver tests."""
    from due_diligence.schemas.reports import PeerValuation
    return [
        PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5, fcf_yield_pct=3.2, mf_rank=2),
        PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1, fcf_yield_pct=4.9, mf_rank=1),
    ]


def test_archiver_renders_peer_relative_valuation_table():
    """New ## Peer Comparison - Relative Valuation section appears when peer_valuations present."""
    from due_diligence.agents.archiver import _render_markdown

    state = {
        "ticker": "AAPL",
        "run_id": "test-run",
        "as_of_date": "2025-09-27",
        "peer_valuations": _make_peer_valuations_for_archiver(),
    }

    markdown = _render_markdown(state)

    assert "## Peer Comparison — Relative Valuation" in markdown
    assert "EV/EBIT" in markdown
    assert "FCF Yield" in markdown
    assert "MF Rank" in markdown
    assert "MSFT" in markdown
    assert "GOOGL" in markdown
    assert "Magic Formula rank = combined rank" in markdown


def test_archiver_relative_valuation_section_absent_when_no_peers():
    """Relative Valuation section absent when peer_valuations not in state."""
    from due_diligence.agents.archiver import _render_markdown

    state = {"ticker": "AAPL", "run_id": "test-run", "as_of_date": "2025-09-27"}

    markdown = _render_markdown(state)

    assert "## Peer Comparison — Relative Valuation" not in markdown


def test_archiver_shows_no_market_data_note_for_excluded_peer():
    """Peer with no_market_data=True shows exclusion note in the table."""
    from due_diligence.agents.archiver import _render_markdown
    from due_diligence.schemas.reports import PeerValuation

    state = {
        "ticker": "AAPL",
        "run_id": "test-run",
        "as_of_date": "2025-09-27",
        "peer_valuations": [
            PeerValuation(ticker="VALID", roic=0.20, period="FY2025", ev_ebit=15.0, fcf_yield_pct=4.0, mf_rank=1),
            PeerValuation(ticker="NODATA", roic=0.25, period="FY2025", no_market_data=True),
        ],
    }

    markdown = _render_markdown(state)

    assert "NODATA" in markdown
    assert "no market data" in markdown.lower()


def test_archiver_shows_mf_rank_in_aggregate_metrics():
    """Magic Formula rank row appears in the Valuation aggregate metrics table."""
    from due_diligence.agents.archiver import _render_markdown
    from due_diligence.schemas.reports import ValuationReport
    from due_diligence.schemas.base import MetricValue

    state = {
        "ticker": "AAPL",
        "run_id": "test-run",
        "as_of_date": "2025-09-27",
        "peer_valuations": _make_peer_valuations_for_archiver(),
        "valuation": ValuationReport(
            ticker="AAPL",
            stub=False,
            earnings_yield_pct=8.1,
            magic_formula_rank=3,
            ev_ebit_multiple=MetricValue(value=12.3, unit="x", period="FY2025", source="calculated"),
        ),
    }

    markdown = _render_markdown(state)

    assert "Magic Formula rank" in markdown
    assert "#3 of 3" in markdown  # 2 peers with mf_rank set + 1 subject = 3 total


# ---------------------------------------------------------------------------
# Tests: near_term_growth en el footnote y reliability note (EC-54)
# ---------------------------------------------------------------------------

def test_archiver_dcf_footnote_includes_near_term_growth():
    """El footnote de DCF assumptions debe incluir near_term_growth y su source."""
    from due_diligence.agents.archiver import _render_markdown
    from due_diligence.schemas.reports import ValuationReport, ValuationInput

    state = {
        "ticker": "TEST",
        "valuation": ValuationReport(
            ticker="TEST", stub=False,
            near_term_growth_used_pct=0.123,
            near_term_growth_source="fcf_cagr_3y",
            assumptions=ValuationInput(checkpoint_id="c2", wacc_pct=9.0,
                                       terminal_growth_pct=2.5, projection_years=10),
        ),
    }
    md = _render_markdown(state)
    assert "12.3%" in md
    assert "fcf_cagr_3y" in md


def test_archiver_reliability_note_always_shown_not_just_contradiction():
    """La reliability note se muestra siempre, no solo cuando hay contradicción."""
    from due_diligence.agents.archiver import _render_markdown
    from due_diligence.schemas.reports import ValuationReport

    state = {
        "ticker": "TEST",
        "valuation": ValuationReport(
            ticker="TEST", stub=False,
            dcf_reliability_note="Near-term FCF growth: 7.0% (generic fallback).",
        ),
    }
    md = _render_markdown(state)
    assert "Near-term FCF growth: 7.0%" in md


def test_archiver_reading_guide_shown_for_spread_note():
    """Reading guide aparece cuando la reliability note contiene 'spread note'."""
    from due_diligence.agents.archiver import _render_markdown
    from due_diligence.schemas.reports import ValuationReport

    state = {
        "ticker": "TEST",
        "valuation": ValuationReport(
            ticker="TEST", stub=False,
            dcf_intrinsic_value=MetricValue(value=500e9, unit="USD", period="FY2025", source="calculated"),
            dcf_upside_pct=45.0,
            reverse_dcf_implied_growth=0.05,
            dcf_reliability_note="Near-term FCF growth: 12.0% (fcf_cagr_2y). ⚠️ DCF spread note: ...",
        ),
    }
    md = _render_markdown(state)
    assert "Reading guide" in md


# ---------------------------------------------------------------------------
# Tests: verificadas vs no verificadas en el report (H1-T3c, EC-69)
# ---------------------------------------------------------------------------

def _mgmt_with_cits():
    ok = Citation(claim="c", source_url="Form 4", excerpt="CEO bought 5,000 shares at $10.00",
                  confidence=0.9, verified=True)
    bad = Citation(claim="c", source_url="Tavily", excerpt="CEO is selling everything", confidence=0.0,
                   verified=False, verification_note="excerpt not found in source (fuzzy=40)")
    return ManagementReport(ticker="TST", stub=False, form4_transactions_12m=3, citations=[ok, bad])


def test_archiver_lists_unverified_separately():
    md = "\n".join(_render_citations_section({"ticker": "TST", "mgmt": _mgmt_with_cits()}))
    assert "### Management" in md and "CEO bought 5,000 shares" in md
    assert "## Unverified citations (excluded from synthesis)" in md
    assert "selling everything" in md and "fuzzy=40" in md
    # Las no verificadas NO aparecen en la sección principal
    main = md.split("## Unverified citations")[0]
    assert "selling everything" not in main


def test_archiver_renders_governance_section():
    """EC-70: los red flags de governance van en la primera página, dentro de "Key risks" (bloque B)."""
    from due_diligence.schemas.reports import FilingsRAGReport
    rf = "Active SEC investigation disclosed in Item 1A; material weakness in ICFR."
    md = _render_markdown({"ticker": "TST", "filings": FilingsRAGReport(ticker="TST", stub=False,
                                                                       governance_red_flags=rf)})
    front = md.split("# Appendix — detail")[0]
    assert "## Key risks" in front and "Governance red flags (10-K)" in front and rf in front


# ---------------------------------------------------------------------------
# H2-1 Task 5: seguimiento prospectivo — cada análisis guarda precio y valores por acción para
# medir a 6/12 meses si los veredictos aciertan (MCE-36). Sin esto no hay forma de saberlo.
# ---------------------------------------------------------------------------

def test_sqlite_row_stores_prospective_tracking_fields(mocker, tmp_path):
    db_path = str(tmp_path / "test.db")
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", db_path)
    state = _make_full_state(tmp_path)
    mv = lambda v, u="USD/share": MetricValue(value=v, unit=u, period="2026-10-10", source="t")  # noqa: E731
    state["valuation"] = state["valuation"].model_copy(update={
        "dcf_intrinsic_per_share": mv(210.5), "epv_per_share": mv(150.25)})
    state["market"] = MarketReport(ticker="AAPL", stub=False, price_current=mv(180.0, "USD"))
    archiver_node(state)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analyses WHERE ticker='AAPL'").fetchone()
    assert row["price_at_analysis"] == 180.0
    assert row["dcf_per_share"] == 210.5 and row["epv_per_share"] == 150.25
    assert row["reverse_dcf_growth"] == pytest.approx(0.15)


def test_prospective_fields_null_when_missing(mocker, tmp_path):
    db_path = str(tmp_path / "test.db")
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", db_path)
    state = _make_full_state(tmp_path)
    state.pop("valuation")
    archiver_node(state)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analyses WHERE ticker='AAPL'").fetchone()
    assert row["price_at_analysis"] is None and row["dcf_per_share"] is None


def test_migration_adds_prospective_columns_idempotently(tmp_path):
    db = str(tmp_path / "old.db")
    with sqlite3.connect(db) as conn:      # base de datos pre-H2 (sin las columnas nuevas)
        conn.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, run_id TEXT, "
                     "as_of_date TEXT, analysis_date TEXT NOT NULL, verdict TEXT)")
    _ensure_db(db)
    _ensure_db(db)                          # segunda vez: no falla
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(analyses)")}
    assert {"price_at_analysis", "dcf_per_share", "epv_per_share", "reverse_dcf_growth"} <= cols


# ---------------------------------------------------------------------------
# H2-2 (ADR-041): taxonomía BUY/WATCHLIST/PASS/SELL y decisión numérica visible
# ---------------------------------------------------------------------------
from due_diligence.schemas.reports import VerdictDecision, ValueExpertReport as _VER


def _h2_synthesis(verdict="WATCHLIST", raw="BUY"):
    d = VerdictDecision(verdict=verdict, verdict_raw=raw, business_class_llm="quality_compounder",
                        business_class="average", hurdle_rate=0.10, implied_return=0.072, implied_return_bear=0.031,
                        entry_price=22.8, entry_basis="base case returns the 10.0% hurdle", price=25.53,
                        confidence=0.6, sizing_pct=0.0,
                        confidence_components={"base": 0.5, "methods_agreement": 0.0, "llm_adjustment": 0.1},
                        caveats=["⚠️ BUY downgraded to WATCHLIST: price $25.53 is above the entry price $22.80.",
                                 "⚠️ Cyclicality not assessed (3 years of operating margin): a cyclical peak "
                                 "would not be normalized."])
    return _VER(ticker="AAPL", stub=False, verdict=verdict, verdict_confidence=0.6, suggested_sizing_pct=0.0,
                verdict_rationale="Wait for the entry price.", decision=d, taxonomy="H2")


def test_new_labels_name_the_report_file():
    from due_diligence.agents.archiver import report_filename
    assert report_filename("2026-10-10", "BBW", _h2_synthesis()) == "2026-10-10-BBW-WATCHLIST.md"
    assert report_filename("2026-10-10", "BBW", _h2_synthesis("PASS", "PASS")) == "2026-10-10-BBW-PASS.md"
    old = _VER(ticker="X", stub=False, verdict="HOLD")            # informes H1 siguen nombrándose igual
    assert report_filename("2026-10-10", "X", old) == "2026-10-10-X-HOLD.md"


def test_markdown_shows_entry_price_margin_and_downgrade(tmp_path):
    state = _make_full_state(tmp_path)
    state["synthesis"] = _h2_synthesis()
    md = _render_markdown(state)
    assert "**WATCHLIST**" in md and "| Entry price | $22.80 (base case returns the 10.0% hurdle) |" in md
    assert "| Expected return vs hurdle | 7.2% vs 10.0% |" in md
    assert "Model verdict BUY" in md and "downgraded" in md
    assert "quality_compounder → average" in md
    assert "Cyclicality not assessed" in md            # los avisos de la decisión se ven en el informe
    assert md.count("downgraded to WATCHLIST") == 1    # la rebaja se dice una vez, sin repetir el caveat


def test_sqlite_row_stores_verdict_decision(mocker, tmp_path):
    db_path = str(tmp_path / "test.db")
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path / "reports"))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", db_path)
    state = _make_full_state(tmp_path)
    state["synthesis"] = _h2_synthesis()
    archiver_node(state)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analyses WHERE ticker='AAPL'").fetchone()
    assert row["verdict"] == "WATCHLIST" and row["status"] == "complete" and row["verdict_raw"] == "BUY"
    assert row["entry_price"] == 22.8 and row["business_class"] == "average"
    assert row["hurdle_pct"] == 10.0 and row["implied_return_pct"] == 7.2 and row["bear_return_pct"] == 3.1
    assert row["methodology_version"] == "H2b"


# ---------------------------------------------------------------------------
# Bloque B (B2): primera página con las secciones de report_facts
# ---------------------------------------------------------------------------

def _decision_state(implied=0.072, cagr=0.077, price=88.05):
    from due_diligence.schemas.base import MetricValue
    from due_diligence.schemas.reports import DcfScenario, MarketReport, ValuationInput, ValuationReport
    return {
        "ticker": "KO", "historical_cagr_5y": cagr, "historical_cagr_years": 5,
        "market": MarketReport(ticker="KO", stub=False, price_current=MetricValue(
            value=price, unit="USD", period="2026-10-10", source="Polygon")),
        "valuation": ValuationReport(
            ticker="KO", stub=False, reverse_dcf_implied_growth=0.22, reverse_dcf_10y_equivalent=0.12,
            stage1_years_used=5, fade_years_used=5, hurdle_rate=0.101, risk_free_rate=0.051,
            hurdle_source="max(10.0% investor hurdle, Rf 5.10% + ERP 5.0%) = 10.1%",
            implied_return=implied, entry_price=26.58, entry_basis="base case returns the 10.1% hurdle",
            dcf_scenarios=[
                DcfScenario(name="bear", fcff_margin=0.12, near_term_growth=0.015, per_share=11.46, upside_pct=-88.0,
                            implied_return=0.021),
                DcfScenario(name="base", fcff_margin=0.219, near_term_growth=0.032, per_share=26.68, upside_pct=-72.0,
                            implied_return=implied),
                DcfScenario(name="bull", fcff_margin=0.222, near_term_growth=0.078, per_share=34.67, upside_pct=-63.0,
                            implied_return=0.051)],
            assumptions=ValuationInput(checkpoint_id="c2", wacc_pct=9.5, terminal_growth_pct=2.5, projection_years=10)),
    }


def test_front_page_leads_with_expected_return_vs_hurdle_and_entry_price():
    md = _render_markdown(_decision_state())
    front = md.split("# Appendix — detail")[0]
    assert "## Decision inputs (expected return vs hurdle)" in front
    assert "| Expected annual return at today's price — base case | 7.2% |" in front
    assert "| Expected annual return — bear case | 2.1% |" in front
    assert "| Entry price | $26.58 (base case returns the 10.1% hurdle) |" in front
    assert "231% above the entry price" in front
    assert front.index("## Decision inputs") < front.index("## What the price implies") < front.index("## Value range")


def test_price_implies_puts_expected_return_first_and_flags_implied_growth_as_secondary():
    md = _render_markdown(_decision_state())
    assert "| Expected return vs hurdle | 7.2% vs 10.1% (2.9 pp below) |" in md
    assert "| Market-implied FCFF growth | 22.0% for 5y, fading (~12.0% a year over 10y) |" in md
    assert "| Revenue growth, last 5 years | 7.7% |" in md
    assert "secondary to the expected return" in md


def test_value_range_shows_the_three_scenarios_with_their_returns():
    md = _render_markdown(_decision_state())
    assert "| Bear — FCFF margin 12.0%, growth 1.5% | $11.46 per share (-88% vs price); expected return 2.1% |" in md
    assert "| Bull — FCFF margin 22.2%, growth 7.8% | $34.67 per share (-63% vs price); expected return 5.1% |" in md


def test_quality_section_carries_the_accounting_scores():
    from due_diligence.agents.report_facts import quality_section
    from due_diligence.schemas.base import AccountingScores, ScoreResult
    s = AccountingScores(
        piotroski=ScoreResult(name="Piotroski F-score", value=8, period="FY2025 vs FY2024", reading="8/9 — strong (8–9)"),
        altman_z2=ScoreResult(name="Altman Z''", period="FY2025", reading="n/m — book equity ≤ 0"),
        beneish_m=ScoreResult(name="Beneish M-score", period="FY2025 vs FY2024", reading="n/a — missing: sga FY2025"),
        sloan_accruals=ScoreResult(name="Sloan accruals", value=-0.019, period="FY2025", reading="-1.9% of assets"))
    fin = type("F", (), {"accounting_scores": s, "roic_series": [], "incremental_roic": None, "free_cash_flow": [],
                         "net_income": [], "diluted_shares": []})()
    rows = dict(quality_section(fin).rows)
    assert rows["Piotroski F-score"] == "8/9 — strong (8–9) (FY2025 vs FY2024)"
    assert rows["Beneish M-score"].startswith("n/a — missing: sga FY2025")
    assert "n/m — book equity ≤ 0" in rows["Altman Z''"]


def test_changes_since_last_analysis_only_when_the_database_was_queried():
    state = _decision_state()
    assert "Changes since the last analysis" not in _render_markdown(state)
    first = _render_markdown(state, previous=None)
    assert "## Changes since the last analysis" in first and "First analysis of this ticker" in first
    prev = {"analysis_date": "2026-10-03", "verdict": "HOLD", "price_at_analysis": 80.0, "entry_price": None,
            "implied_return_pct": None, "dcf_per_share": 30.0, "run_id": "r1"}
    md = _render_markdown(state, previous=prev)
    assert "| Previous analysis | 2026-10-03 — HOLD |" in md and "$80.00 → $88.05 (+10%)" in md
    assert "older methodology" in md


def test_previous_analysis_reads_the_last_complete_row(tmp_path):
    from due_diligence.agents.report_facts import previous_analysis
    db = str(tmp_path / "a.db")
    _ensure_db(db)
    with sqlite3.connect(db) as conn:
        for rid, verdict, price in (("r1", "HOLD", 70.0), ("r2", None, 75.0), ("r3", "WATCHLIST", 80.0)):
            conn.execute("INSERT INTO analyses (ticker, run_id, analysis_date, verdict, price_at_analysis) "
                         "VALUES ('KO', ?, '2026-10-01', ?, ?)", (rid, verdict, price))
    assert previous_analysis(db, "KO")["run_id"] == "r3"
    assert previous_analysis(db, "KO", before_run_id="r3")["run_id"] == "r1"   # r2 sin veredicto: incompleto
    assert previous_analysis(db, "MSFT") is None
    assert previous_analysis(str(tmp_path / "missing.db"), "KO") is None


def test_citation_links_only_for_urls_and_gaap_flag_shown():
    from due_diligence.agents.archiver import _source_link
    assert _source_link("https://sec.gov/x") == "[https://sec.gov/x](https://sec.gov/x)"
    assert _source_link("10-K Item 1") == "10-K Item 1"


def test_previous_analysis_keeps_rows_without_run_id_and_reads_the_methodology(tmp_path):
    from due_diligence.agents.report_facts import changes_section, previous_analysis
    db = str(tmp_path / "a.db")
    _ensure_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO analyses (ticker, run_id, analysis_date, verdict, methodology_version) "
                     "VALUES ('KO', NULL, '2026-05-01', 'HOLD', NULL)")
        conn.execute("INSERT INTO analyses (ticker, run_id, analysis_date, verdict, methodology_version) "
                     "VALUES ('KO', 'r9', '2026-10-17', 'WATCHLIST', 'H2b')")
    old = previous_analysis(db, "KO", before_run_id="r9")
    assert old is not None and old["analysis_date"] == "2026-05-01"          # run_id NULL no se pierde
    notes = changes_section(old, verdict="WATCHLIST", price=90.0, entry=None, implied_return=None).notes
    assert any("older methodology (pre-H1)" in n for n in notes)
    new = previous_analysis(db, "KO")
    # Una fila H2b sin precio de entrada calculable NO es "metodología antigua" (revisión #4).
    assert not changes_section(new, verdict="WATCHLIST", price=90.0, entry=None, implied_return=None).notes
