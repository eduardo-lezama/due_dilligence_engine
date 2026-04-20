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

from due_diligence.agents.archiver import archiver_node, _render_markdown, _ensure_db
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

    md_files = list(reports_dir.glob("AAPL-*.md"))
    assert len(md_files) == 1


def test_archiver_markdown_contains_verdict(mocker, tmp_path):
    reports_dir = tmp_path / "reports"
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(reports_dir))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", str(tmp_path / "test.db"))

    archiver_node(_make_full_state(tmp_path))

    md_file = list(reports_dir.glob("AAPL-*.md"))[0]
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

    md_files = list((tmp_path / "reports").glob("MSFT-*.md"))
    assert len(md_files) == 1


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
    md = _render_markdown(_make_full_state(tmp_path))
    for section in ["§1", "§2", "§3", "§4", "§5", "§6", "§7", "§8", "§9"]:
        assert section in md


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
