"""
Tests para performance tracking (ADR-021).

Qué se testea:
    - RunStats.add() acumula tokens y calcula coste correctamente.
    - LLMClient.reset_run_stats() limpia el acumulador.
    - LLMClient.get_run_stats() devuelve el acumulador actual.
    - update_performance_metrics() actualiza la fila SQLite correctamente.
    - Precios conocidos aplicados correctamente.
"""

import sqlite3


# ---------------------------------------------------------------------------
# Tests: RunStats
# ---------------------------------------------------------------------------

def test_runstats_add_accumulates_correctly():
    """add() acumula tokens y calcula coste para gpt-4o-mini."""
    from due_diligence.llm.client import RunStats
    stats = RunStats()
    stats.add("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=0)
    # 1M input tokens @ $0.15/M = $0.15
    assert abs(stats.cost_usd - 0.15) < 0.0001
    assert stats.tokens_in == 1_000_000
    assert stats.tokens_out == 0
    assert stats.calls == 1


def test_runstats_add_output_tokens():
    """Coste correcto para output tokens de gpt-5.4-mini."""
    from due_diligence.llm.client import RunStats
    stats = RunStats()
    stats.add("gpt-5.4-mini", prompt_tokens=0, completion_tokens=1_000_000)
    # 1M output @ $4.50/M = $4.50
    assert abs(stats.cost_usd - 4.50) < 0.001
    assert stats.tokens_out == 1_000_000
    assert stats.calls == 1


def test_runstats_accumulates_multiple_calls():
    """Múltiples add() se acumulan correctamente."""
    from due_diligence.llm.client import RunStats
    stats = RunStats()
    stats.add("gpt-4o-mini", 10_000, 2_000)
    stats.add("gpt-5.4-mini", 5_000, 1_000)
    assert stats.calls == 2
    assert stats.tokens_in == 15_000
    assert stats.tokens_out == 3_000
    # gpt-4o-mini: (10k*0.15 + 2k*0.60)/1M = (1500+1200)/1M = 0.0027
    # gpt-5.4-mini: (5k*0.75 + 1k*4.50)/1M = (3750+4500)/1M = 0.00825
    expected = 0.0027 + 0.00825
    assert abs(stats.cost_usd - expected) < 0.00001


def test_runstats_unknown_model_uses_fallback():
    """Modelo desconocido usa precios de fallback sin crash."""
    from due_diligence.llm.client import RunStats
    stats = RunStats()
    stats.add("gpt-unknown-9999", 1_000, 1_000)
    assert stats.calls == 1
    assert stats.cost_usd > 0  # fallback produce algún coste


# ---------------------------------------------------------------------------
# Tests: LLMClient acumulador
# ---------------------------------------------------------------------------

def test_llmclient_reset_clears_stats():
    """reset_run_stats() limpia el acumulador a cero."""
    from due_diligence.llm.client import LLMClient
    client = LLMClient()
    client._run_stats.add("gpt-4o-mini", 1000, 500)
    assert client._run_stats.calls == 1
    client.reset_run_stats()
    assert client._run_stats.calls == 0
    assert client._run_stats.cost_usd == 0.0
    assert client._run_stats.tokens_in == 0


def test_llmclient_get_run_stats_returns_accumulator():
    """get_run_stats() devuelve el mismo objeto acumulador."""
    from due_diligence.llm.client import LLMClient
    client = LLMClient()
    client.reset_run_stats()
    client._run_stats.add("gpt-4o-mini", 100, 50)
    stats = client.get_run_stats()
    assert stats.calls == 1
    assert stats.tokens_in == 100


# ---------------------------------------------------------------------------
# Tests: update_performance_metrics
# ---------------------------------------------------------------------------

def test_update_performance_metrics_writes_to_sqlite(tmp_path):
    """update_performance_metrics() actualiza la fila correctamente."""
    from due_diligence.agents.archiver import _ensure_db, _insert_row, update_performance_metrics
    from datetime import date

    db_path = str(tmp_path / "test.db")
    _ensure_db(db_path)
    _insert_row(db_path, {
        "ticker": "PRDO",
        "run_id": "test-run-001",
        "as_of_date": "2025-12-31",
        "analysis_date": date.today().isoformat(),
        "verdict": "HOLD",
        "verdict_confidence": 0.67,
        "suggested_sizing_pct": 4.0,
        "dcf_upside_pct": None,
        "moat_classification": "narrow",
        "report_path": "/tmp/PRDO.md",
    })

    update_performance_metrics(db_path, "test-run-001", 45_000.0, 0.0312, 85_000, 12_000)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM analyses WHERE run_id='test-run-001'").fetchone()

    assert abs(row["latency_ms"] - 45_000.0) < 1
    assert abs(row["cost_eur"] - 0.0312) < 0.00001
    assert row["tokens_in"] == 85_000
    assert row["tokens_out"] == 12_000
