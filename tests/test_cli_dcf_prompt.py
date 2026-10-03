"""
Tests del prompt de assumptions DCF en la CLI (T11, ADR-033).

Qué se testea:
    - `_ask_float` re-pregunta ante entrada no numérica (antes caía al default en silencio).
    - `_ask_wacc_and_terminal` re-pregunta hasta que validate_dcf_rates acepta (EC-76).
    - `_ask_dcf_assumptions` usa el default del PAYLOAD del interrupt de checkpoint 2
      (WACC real sugerido), no un 9.0 fijo ni el Ke etiquetado como "WACC".
    - `_pending_interrupt_payload` lee el payload tal como lo expone LangGraph 1.1.8:
      clave "__interrupt__" del resultado de invoke() o `get_state(config).interrupts`.
Zero red: typer.prompt mockeado; grafo y estado falsos.
"""
from types import SimpleNamespace

from due_diligence.cli import (
    _ask_dcf_assumptions, _ask_float, _ask_wacc_and_terminal, _pending_interrupt_payload,
)


def test_ask_float_reasks_on_invalid_input(mocker):
    mocker.patch("due_diligence.cli.typer.prompt", side_effect=["abc", "7.5"])
    assert _ask_float("WACC (%)", 9.0) == 7.5


def test_cli_reasks_when_wacc_not_above_terminal(mocker):
    # 1er intento WACC 2.0 / g 2.5 → inválido; 2º intento 9.0 / 2.5 → válido
    mocker.patch("due_diligence.cli.typer.prompt", side_effect=["2.0", "2.5", "9.0", "2.5"])
    assert _ask_wacc_and_terminal(default_wacc=8.5) == (9.0, 2.5)


def test_ask_dcf_assumptions_uses_payload_default(mocker):
    seen: list[tuple[str, str]] = []

    def fake_prompt(label, default=None, **_):
        seen.append((label, default))
        return default   # el usuario pulsa Enter en todo

    mocker.patch("due_diligence.cli.typer.prompt", side_effect=fake_prompt)
    payload = {
        "phase": "checkpoint2",
        "context": {"wacc_breakdown": "Ke 9.2% × 75% + Kd 5.0% × (1−25%) × 25% = 7.8%"},
        "defaults": {"wacc_pct": 8.5, "terminal_growth_pct": 2.5, "projection_years": 10,
                     "mid_year_convention": False},
    }
    out = _ask_dcf_assumptions(payload, {"financials": None, "market": None})
    wacc_prompt = next(d for label, d in seen if "WACC" in label)
    assert wacc_prompt == "8.5"
    assert out["wacc_pct"] == 8.5 and out["terminal_growth_pct"] == 2.5
    assert out["mid_year_convention"] is False


def test_pending_interrupt_payload_from_invoke_result():
    result = {"ticker": "X", "__interrupt__": [SimpleNamespace(value={"phase": "checkpoint2"}, id="i1")]}
    assert _pending_interrupt_payload(result) == {"phase": "checkpoint2"}


def test_pending_interrupt_payload_from_state_snapshot_fallback():
    graph = SimpleNamespace(get_state=lambda cfg: SimpleNamespace(
        interrupts=(SimpleNamespace(value={"phase": "checkpoint2", "error": "bad"}, id="i2"),)))
    assert _pending_interrupt_payload({"ticker": "X"}, graph, {"configurable": {}})["error"] == "bad"


def test_pending_interrupt_payload_none_when_finished():
    graph = SimpleNamespace(get_state=lambda cfg: SimpleNamespace(interrupts=()))
    assert _pending_interrupt_payload({"ticker": "X"}, graph, {}) is None
    assert _pending_interrupt_payload(None) is None
