"""
Tests de visibilidad del fallo de punta a punta (H1-T2e, ADR-032, EC-68).

Qué se testea:
    - archiver.report_filename: sin VERDICT → -INCOMPLETE.md (antes -PENDING.md).
    - archiver._render_markdown: sección "## Analysis incomplete" con cada agente fallido.
    - archiver: pie "Models used" desde RunStats.calls_by_model; columnas SQLite nuevas.
    - cli._show_failures: panel rojo "ANALYSIS INCOMPLETE".
    - value_expert: bloque DATA GAPS y tope de confianza 0.6 sin filings/mgmt.
Sin red: todos los LLM mockeados.
"""
import sqlite3

import pytest
from rich.console import Console

from due_diligence import cli
from due_diligence.agents.archiver import (
    _render_markdown,
    archiver_node,
    collect_failures,
    report_filename,
)
from due_diligence.llm.client import RunStats
from due_diligence.schemas.reports import (
    CriticReport,
    FilingsRAGReport,
    ManagementReport,
    ValueExpertReport,
)


def test_report_filename_incomplete_when_synthesis_failed():
    s = ValueExpertReport(ticker="TST", stub=True, error="LLMTruncatedError: x")
    assert report_filename("2026-10-03", "TST", s) == "2026-10-03-TST-INCOMPLETE.md"
    assert report_filename("2026-10-03", "TST", None) == "2026-10-03-TST-INCOMPLETE.md"
    ok = ValueExpertReport(ticker="TST", stub=False, verdict="BUY")
    assert report_filename("2026-10-03", "TST", ok) == "2026-10-03-TST-BUY.md"


def test_collect_failures_lists_agents_with_error_only():
    state = {"filings": FilingsRAGReport(ticker="T", stub=True, error="LLMSchemaError: y"),
             "mgmt": ManagementReport(ticker="T", stub=False),
             "critique": CriticReport(ticker="T", stub=True, error="skipped: synthesis unavailable")}
    assert collect_failures(state) == [("Filings", "LLMSchemaError: y"),
                                       ("Critic", "skipped: synthesis unavailable")]


def test_markdown_lists_agent_failures():
    state = {"ticker": "TST",
             "synthesis": ValueExpertReport(ticker="TST", stub=True, error="LLMTruncatedError: x"),
             "filings": FilingsRAGReport(ticker="TST", stub=True, error="LLMSchemaError: y")}
    md = _render_markdown(state)
    assert "**INCOMPLETE**" in md and "## Analysis incomplete" in md
    assert "LLMTruncatedError" in md and "LLMSchemaError" in md


def test_archiver_renders_models_used_footer():
    state = {"ticker": "TST",
             "synthesis": ValueExpertReport(ticker="TST", stub=False, verdict="BUY", model_used="gpt-5.4-mini")}
    md = _render_markdown(state, run_stats=RunStats(calls_by_model={"gpt-4.1": 3, "gpt-5.4-mini": 2}))
    assert "Models used: gpt-4.1: 3, gpt-5.4-mini: 2" in md


def test_archiver_node_writes_incomplete_file_and_status_columns(mocker, tmp_path):
    db_path = str(tmp_path / "t.db")
    mocker.patch("due_diligence.agents.archiver.settings.reports_dir", str(tmp_path))
    mocker.patch("due_diligence.agents.archiver.settings.db_path", db_path)
    client = mocker.MagicMock()
    client.get_run_stats.return_value = RunStats(calls_by_model={"gpt-4.1": 4})
    mocker.patch("due_diligence.agents.archiver.get_client", return_value=client)
    state = {"ticker": "TST", "run_id": "r1",
             "synthesis": ValueExpertReport(ticker="TST", stub=True, error="LLMTruncatedError: x")}
    archiver_node(state)
    assert [p.name.endswith("-TST-INCOMPLETE.md") for p in tmp_path.glob("*.md")] == [True]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analyses").fetchone()
    assert row["status"] == "incomplete" and row["verdict"] is None
    assert row["models_used"] == "gpt-4.1: 4"
    assert row["n_verified_citations"] == 0 and row["methodology_version"] == "H1"


def test_ensure_db_migrates_old_table_idempotently(tmp_path):
    """Una DB pre-H1 gana las columnas nuevas sin perder filas; dos llamadas no fallan."""
    from due_diligence.agents.archiver import _ensure_db
    db_path = str(tmp_path / "old.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, "
                     "analysis_date TEXT NOT NULL)")
        conn.execute("INSERT INTO analyses (ticker, analysis_date) VALUES ('OLD', '2026-01-01')")
    _ensure_db(db_path)
    _ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(analyses)")}
        old = conn.execute("SELECT methodology_version FROM analyses WHERE ticker='OLD'").fetchone()
    assert {"status", "models_used", "n_verified_citations", "methodology_version"} <= cols
    assert old == (None,)   # filas antiguas: NULL = pre-H1


def test_cli_shows_red_failure_panel(mocker):
    mocker.patch.object(cli, "console", Console(record=True, width=120))
    cli._show_failures({"synthesis": ValueExpertReport(ticker="T", stub=True, error="LLMTruncatedError: x")})
    text = cli.console.export_text()
    assert "ANALYSIS INCOMPLETE" in text and "LLMTruncatedError" in text
    assert "No VERDICT was produced" in text


def test_cli_no_panel_without_failures(mocker):
    mocker.patch.object(cli, "console", Console(record=True, width=120))
    cli._show_failures({"synthesis": ValueExpertReport(ticker="T", stub=False, verdict="BUY")})
    assert "ANALYSIS INCOMPLETE" not in cli.console.export_text()


def test_cli_models_line_formats_calls_by_model():
    assert cli._models_line(RunStats(calls_by_model={"gpt-4.1": 3, "gpt-5.4-mini": 2})) == \
        "Models: gpt-4.1 ×3, gpt-5.4-mini ×2"
    assert cli._models_line(RunStats()) == ""


# ── Value Expert: DATA GAPS + tope de confianza (revisión funcional H1) ──

def _ve_client(mocker, confidence=0.9, sizing=9.0):
    from due_diligence.schemas.llm_outputs import ValueExpertLLMOutput
    out = ValueExpertLLMOutput(**{k: "x" for k in (
        "business_understanding", "moat_analysis", "management_assessment", "financial_quality",
        "growth_reinvestment", "valuation_section", "risks", "psychological_lens", "decision_frame")},
        verdict="BUY", verdict_confidence=confidence, suggested_sizing_pct=sizing, verdict_rationale="r")
    client = mocker.MagicMock()
    client.structured.return_value = out
    mocker.patch("due_diligence.agents.value_expert.get_client", return_value=client)
    return client


def test_context_lists_data_gaps():
    from due_diligence.agents.value_expert import _format_context
    ctx = _format_context({"ticker": "TST",
                           "filings": FilingsRAGReport(ticker="TST", stub=True, error="LLMSchemaError: y"),
                           "mgmt": ManagementReport(ticker="TST", stub=False)})
    assert "## DATA GAPS" in ctx and "Filings" in ctx and "LLMSchemaError: y" in ctx
    assert ctx.index("## DATA GAPS") < 200   # al principio: el Expert sabe qué le falta antes de leer


def test_confidence_capped_when_specialist_missing(mocker):
    from due_diligence.agents.value_expert import value_expert_agent
    _ve_client(mocker, confidence=0.9, sizing=9.0)
    s = value_expert_agent({"ticker": "TST",
                            "filings": FilingsRAGReport(ticker="TST", stub=True, error="LLMSchemaError: y"),
                            "mgmt": ManagementReport(ticker="TST", stub=False)})["synthesis"]
    assert s.verdict_confidence == pytest.approx(0.6)
    assert s.suggested_sizing_pct == pytest.approx(6.0)   # el clamp sizing ≤ conf×10 se re-aplica


def test_confidence_not_capped_when_specialists_present(mocker):
    from due_diligence.agents.value_expert import value_expert_agent
    _ve_client(mocker, confidence=0.9, sizing=9.0)
    from due_diligence.schemas.base import Citation
    # Presentes CON evidencia verificada: sin ella también cuentan como hueco (EC-94).
    ev = [Citation(claim="c", source_url="10-K", excerpt="x" * 25, confidence=0.9, verified=True)]
    s = value_expert_agent({"ticker": "TST",
                            "filings": FilingsRAGReport(ticker="TST", stub=False, citations=ev),
                            "mgmt": ManagementReport(ticker="TST", stub=False, citations=ev)})["synthesis"]
    assert s.verdict_confidence == pytest.approx(0.9)
