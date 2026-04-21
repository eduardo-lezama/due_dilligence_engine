"""
Tests para value_expert_agent.

Qué se testea:
    - value_expert_agent(): nodo LangGraph completo con LLM mockeado.
    - _parse_response(): parsing JSON, validación de VERDICT, fallback en error.
    - _format_context(): que incluye datos de todos los reports disponibles.
    - Degraded mode: agente corre con estado mínimo (solo ticker).
    - Uso del modelo expert: el agente llama a get_client().complete() con
      el modelo correcto (openai_expert_model).

Todos los tests mockean el LLM — zero llamadas reales a la API.
"""

import json
import pytest

from due_diligence.agents.value_expert import (
    value_expert_agent,
    _parse_response,
    _format_context,
)
from due_diligence.schemas.reports import ValueExpertReport


_VALID_LLM_RESPONSE = json.dumps({
    "business_understanding": "Apple designs and sells consumer electronics. Revenue comes from iPhone (~50%), Services (~20%), and other hardware. The business is simple to understand.",
    "moat_analysis": "Wide moat via brand and switching costs. ROIC of 87% sustained over multiple years confirms durable competitive advantage. Ecosystem lock-in deepens the moat.",
    "management_assessment": "Tim Cook has led Apple for 13 years with disciplined capital allocation — $600B+ returned to shareholders. Equity-based compensation aligns incentives.",
    "financial_quality": "FCF conversion >95% of net income. Net cash position after adjusting for gross debt. Gross margins expanding to 47% on services mix shift.",
    "growth_reinvestment": "Services growing at 13% CAGR provides reinvestment runway. Incremental ROIC remains high as services require minimal capital.",
    "valuation_section": "DCF at 9% WACC shows moderate upside vs current market cap. Reverse DCF implies ~15% growth embedded in current price — achievable but not certain.",
    "risks": "App Store regulatory risk in EU and US. Supply chain concentration in China. iPhone revenue plateau risk if upgrade cycles lengthen.",
    "psychological_lens": "Market prices Apple as a tech growth company; it increasingly resembles a consumer staples compounder. This framing mismatch may create mispricing.",
    "decision_frame": "Fits a concentrated value portfolio as a quality compounder at fair value. 5-8% position appropriate given valuation is not deep-value but quality is exceptional.",
    "verdict": "BUY",
    "verdict_confidence": 0.72,
    "suggested_sizing_pct": 7.0,
})


def _mock_expert(mocker, response=_VALID_LLM_RESPONSE):
    mock_client = mocker.MagicMock()
    mock_client.complete.return_value = response
    mocker.patch("due_diligence.agents.value_expert.get_client", return_value=mock_client)
    return mock_client


# ---------------------------------------------------------------------------
# Tests: nodo LangGraph completo
# ---------------------------------------------------------------------------

def test_value_expert_returns_synthesis_key(mocker):
    _mock_expert(mocker)
    result = value_expert_agent({"ticker": "AAPL"})
    assert "synthesis" in result


def test_value_expert_stub_false(mocker):
    _mock_expert(mocker)
    result = value_expert_agent({"ticker": "AAPL"})
    assert result["synthesis"].stub is False


def test_value_expert_verdict_populated(mocker):
    _mock_expert(mocker)
    report: ValueExpertReport = value_expert_agent({"ticker": "AAPL"})["synthesis"]
    assert report.verdict == "BUY"
    assert report.verdict_confidence == pytest.approx(0.72)
    assert report.suggested_sizing_pct == pytest.approx(7.0)


def test_value_expert_all_sections_populated(mocker):
    _mock_expert(mocker)
    report = value_expert_agent({"ticker": "AAPL"})["synthesis"]
    assert report.business_understanding is not None
    assert report.moat_analysis is not None
    assert report.management_assessment is not None
    assert report.financial_quality is not None
    assert report.growth_reinvestment is not None
    assert report.valuation_section is not None
    assert report.risks is not None
    assert report.psychological_lens is not None
    assert report.decision_frame is not None


def test_value_expert_uses_expert_model(mocker):
    """El agente debe llamar al LLM con openai_expert_model, no el fallback general."""
    from due_diligence.config import settings
    mock_client = _mock_expert(mocker)
    value_expert_agent({"ticker": "AAPL"})
    call_kwargs = mock_client.complete.call_args
    assert call_kwargs.kwargs.get("model") == settings.openai_expert_model


def test_value_expert_degraded_minimal_state(mocker):
    """Con solo ticker, el agente produce un report sin fallar."""
    _mock_expert(mocker)
    result = value_expert_agent({"ticker": "AAPL"})
    assert isinstance(result["synthesis"], ValueExpertReport)
    assert result["synthesis"].stub is False


# ---------------------------------------------------------------------------
# Tests: _parse_response
# ---------------------------------------------------------------------------

def test_parse_valid_buy():
    report = _parse_response(_VALID_LLM_RESPONSE, "AAPL")
    assert report.verdict == "BUY"
    assert report.verdict_confidence == pytest.approx(0.72)
    assert report.suggested_sizing_pct == pytest.approx(7.0)


def test_parse_valid_hold():
    raw = json.dumps({
        "business_understanding": "B", "moat_analysis": "M",
        "management_assessment": "Mg", "financial_quality": "F",
        "growth_reinvestment": "G", "valuation_section": "V",
        "risks": "R", "psychological_lens": "P", "decision_frame": "D",
        "verdict": "HOLD", "verdict_confidence": 0.5, "suggested_sizing_pct": 3.0,
    })
    report = _parse_response(raw, "KO")
    assert report.verdict == "HOLD"


def test_parse_valid_sell():
    raw = json.dumps({
        "verdict": "SELL", "verdict_confidence": 0.8, "suggested_sizing_pct": 0.0,
    })
    report = _parse_response(raw, "XYZ")
    assert report.verdict == "SELL"


def test_parse_invalid_verdict_defaults_to_hold():
    """Un VERDICT no reconocido se convierte a HOLD sin lanzar excepción."""
    raw = json.dumps({
        "verdict": "STRONG_BUY",
        "verdict_confidence": 0.9,
        "suggested_sizing_pct": 15.0,
    })
    report = _parse_response(raw, "TEST")
    assert report.verdict == "HOLD"


def test_parse_confidence_clamped_to_range():
    """Confidence fuera de [0,1] se clampea."""
    raw = json.dumps({"verdict": "BUY", "verdict_confidence": 1.5, "suggested_sizing_pct": 5.0})
    report = _parse_response(raw, "TEST")
    assert report.verdict_confidence == pytest.approx(1.0)


def test_parse_sizing_clamped_to_max():
    """sizing_pct > 20 se clampea a 20."""
    raw = json.dumps({"verdict": "BUY", "verdict_confidence": 0.8, "suggested_sizing_pct": 50.0})
    report = _parse_response(raw, "TEST")
    assert report.suggested_sizing_pct == pytest.approx(20.0)


def test_parse_invalid_json_returns_fallback():
    """JSON inválido devuelve un ValueExpertReport parcial con HOLD."""
    report = _parse_response("I cannot analyze this company.", "ERR")
    assert isinstance(report, ValueExpertReport)
    assert report.stub is False
    assert report.verdict == "HOLD"
    assert report.verdict_confidence == pytest.approx(0.0)


def test_parse_json_with_surrounding_text():
    """El parser extrae el JSON aunque el LLM añada texto antes y después."""
    raw = "Here is my analysis:\n" + _VALID_LLM_RESPONSE + "\nEnd of analysis."
    report = _parse_response(raw, "AAPL")
    assert report.verdict == "BUY"


# ---------------------------------------------------------------------------
# Tests: _format_context
# ---------------------------------------------------------------------------

def test_format_context_includes_ticker():
    context = _format_context({"ticker": "MSFT"})
    assert "MSFT" in context


def test_format_context_includes_financials_when_present():
    import json
    from pathlib import Path
    from due_diligence.tools.edgar import EdgarAdapter
    fixture = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())
    financials = EdgarAdapter.parse_financials(fixture)
    context = _format_context({"ticker": "AAPL", "financials": financials})
    assert "Revenue" in context
    assert "ROIC" in context


def test_format_context_handles_missing_reports():
    """Con solo ticker no falla — devuelve contexto mínimo."""
    context = _format_context({"ticker": "AAPL"})
    assert "AAPL" in context


# ---------------------------------------------------------------------------
# Tests: Value Expert prompt v2 (ADR-019)
# ---------------------------------------------------------------------------

def test_system_prompt_v2_declares_integration_intent():
    """El prompt v2 debe declarar explícitamente que la tarea es INTEGRAR, no resumir."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "integrate" in _SYSTEM_PROMPT.lower() or "adjudic" in _SYSTEM_PROMPT.lower()


def test_system_prompt_v2_requires_conflicting_signals():
    """El prompt v2 debe exigir nombrar señales conflictivas."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "conflicting" in _SYSTEM_PROMPT.lower() or "conflict" in _SYSTEM_PROMPT.lower()


def test_system_prompt_v2_requires_cagr_contrast():
    """§6 Valuation debe contrastar implied growth vs CAGR histórico."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "cagr" in _SYSTEM_PROMPT.lower() or "historical" in _SYSTEM_PROMPT.lower()


def test_system_prompt_v2_requires_thesis_breaker():
    """§9 Decision Frame debe incluir thesis-breaker concreto y observable."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "thesis" in _SYSTEM_PROMPT.lower() and "break" in _SYSTEM_PROMPT.lower()


def test_format_context_injects_historical_cagr():
    """Con historical_cagr_5y en el state, _format_context lo incluye en el contexto."""
    from due_diligence.agents.value_expert import _format_context
    context = _format_context({"ticker": "AAPL", "historical_cagr_5y": 0.074})
    assert "7.4%" in context or "0.074" in context
    assert "CAGR" in context or "Historical" in context or "historical" in context


def test_format_context_omits_cagr_block_when_absent():
    """Sin historical_cagr_5y en el state, el bloque CAGR no aparece."""
    from due_diligence.agents.value_expert import _format_context
    context = _format_context({"ticker": "AAPL"})
    assert "CAGR" not in context or "Revenue" not in context  # no bloque historical context


def test_format_context_includes_material_events():
    """Con eventos 8-K en market report, aparecen en el contexto del Expert."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import MarketReport
    state = {
        "ticker": "AAPL",
        "market": MarketReport(
            ticker="AAPL",
            stub=False,
            material_events=[{
                "filing_date": "2025-10-15",
                "item_numbers": ["4.02"],
                "summary_text": "Restatement of Q2 results.",
                "filing_url": "https://sec.gov/8k",
            }],
        ),
    }
    context = _format_context(state)
    assert "4.02" in context or "Restatement" in context or "material" in context.lower()
