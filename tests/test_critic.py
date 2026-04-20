"""
Tests para critic_agent.

Qué se testea:
    - critic_agent(): nodo LangGraph completo con LLM mockeado.
    - _parse_response(): parsing JSON, weaknesses como lista, fallback en error.
    - _build_critic_prompt(): incluye VERDICT del expert y datos de riesgo.
    - Degraded mode: corre con estado mínimo sin fallar.
    - Modelo: usa openai_expert_model igual que el value_expert.
"""

import json
import pytest

from due_diligence.agents.critic import critic_agent, _parse_response, _build_critic_prompt
from due_diligence.schemas.reports import CriticReport


_VALID_LLM_RESPONSE = json.dumps({
    "weaknesses": [
        "Apple's iPhone revenue (50% of total) has shown flat growth for 3 consecutive years, making the bull case dependent on Services continuing to grow at 13%+ — which requires the installed base to keep expanding.",
        "Regulatory risk is more acute than the bull case acknowledges: EU DMA forces sideloading by 2025 and US DOJ antitrust case targets App Store commissions directly — both threaten the high-margin Services segment.",
        "Reverse DCF implies 15% embedded growth. Apple's 5-year FCF CAGR is 8%. The current price requires either Services acceleration or a new product category — neither is certain.",
    ],
    "contra_thesis": "Apple is a mature hardware company being valued as a software compounder. The Services moat is regulatory, not structural — one adverse ruling removes the 30% App Store cut that drives most of the margin expansion. The stock is priced for perfection in a scenario where regulatory outcomes are binary.",
    "pre_mortem_scenario": "In 2027, the EU DMA enforcement reduced App Store commissions from 30% to 10%. Services gross margin contracted from 74% to 58%. The FCF multiple re-rated from 28x to 20x and the stock fell 35% despite stable iPhone volumes.",
})


def _mock_critic(mocker, response=_VALID_LLM_RESPONSE):
    mock_client = mocker.MagicMock()
    mock_client.complete.return_value = response
    mocker.patch("due_diligence.agents.critic.get_client", return_value=mock_client)
    return mock_client


# ---------------------------------------------------------------------------
# Tests: nodo LangGraph completo
# ---------------------------------------------------------------------------

def test_critic_returns_critique_key(mocker):
    _mock_critic(mocker)
    result = critic_agent({"ticker": "AAPL"})
    assert "critique" in result


def test_critic_stub_false(mocker):
    _mock_critic(mocker)
    result = critic_agent({"ticker": "AAPL"})
    assert result["critique"].stub is False


def test_critic_weaknesses_populated(mocker):
    _mock_critic(mocker)
    report: CriticReport = critic_agent({"ticker": "AAPL"})["critique"]
    assert len(report.weaknesses) == 3
    assert all(isinstance(w, str) and len(w) > 0 for w in report.weaknesses)


def test_critic_contra_thesis_populated(mocker):
    _mock_critic(mocker)
    report = critic_agent({"ticker": "AAPL"})["critique"]
    assert report.contra_thesis is not None
    assert len(report.contra_thesis) > 10


def test_critic_pre_mortem_populated(mocker):
    _mock_critic(mocker)
    report = critic_agent({"ticker": "AAPL"})["critique"]
    assert report.pre_mortem_scenario is not None


def test_critic_uses_expert_model(mocker):
    """El critic usa openai_expert_model — mismo que el value_expert."""
    from due_diligence.config import settings
    mock_client = _mock_critic(mocker)
    critic_agent({"ticker": "AAPL"})
    call_kwargs = mock_client.complete.call_args
    assert call_kwargs.kwargs.get("model") == settings.openai_expert_model


def test_critic_includes_verdict_in_prompt(mocker):
    """El prompt incluye el VERDICT del value_expert para que el critic lo conozca."""
    from due_diligence.schemas.reports import ValueExpertReport
    mock_client = _mock_critic(mocker)

    state = {
        "ticker": "AAPL",
        "synthesis": ValueExpertReport(
            ticker="AAPL", stub=False, verdict="BUY",
            verdict_confidence=0.72, suggested_sizing_pct=7.0,
        ),
    }
    critic_agent(state)

    call_kwargs = mock_client.complete.call_args
    user_prompt = call_kwargs.kwargs["messages"][0]["content"]
    assert "BUY" in user_prompt


def test_critic_degraded_minimal_state(mocker):
    """Con solo ticker, el agente produce un CriticReport sin fallar."""
    _mock_critic(mocker)
    result = critic_agent({"ticker": "AAPL"})
    assert isinstance(result["critique"], CriticReport)
    assert result["critique"].stub is False


# ---------------------------------------------------------------------------
# Tests: _parse_response
# ---------------------------------------------------------------------------

def test_parse_valid_response():
    report = _parse_response(_VALID_LLM_RESPONSE, "AAPL")
    assert report.stub is False
    assert len(report.weaknesses) == 3
    assert report.contra_thesis is not None
    assert report.pre_mortem_scenario is not None


def test_parse_weaknesses_always_list():
    """weaknesses debe ser siempre una lista, incluso si el LLM devuelve otra cosa."""
    raw = json.dumps({"weaknesses": "not a list", "contra_thesis": "test"})
    report = _parse_response(raw, "TEST")
    assert isinstance(report.weaknesses, list)
    assert report.weaknesses == []


def test_parse_json_with_surrounding_text():
    raw = "Analysis:\n" + _VALID_LLM_RESPONSE + "\nDone."
    report = _parse_response(raw, "AAPL")
    assert len(report.weaknesses) == 3


def test_parse_invalid_json_returns_fallback():
    report = _parse_response("I cannot critique this.", "ERR")
    assert isinstance(report, CriticReport)
    assert report.stub is False
    assert report.weaknesses == []
    assert report.contra_thesis is not None  # contiene el texto crudo


def test_parse_empty_weaknesses_allowed():
    """Lista vacía es válida — el critic puede no encontrar debilidades significativas."""
    raw = json.dumps({"weaknesses": [], "contra_thesis": "No major weaknesses found."})
    report = _parse_response(raw, "TEST")
    assert report.weaknesses == []
    assert report.contra_thesis == "No major weaknesses found."


# ---------------------------------------------------------------------------
# Tests: _build_critic_prompt
# ---------------------------------------------------------------------------

def test_build_prompt_includes_ticker():
    prompt = _build_critic_prompt({"ticker": "MSFT"})
    assert "MSFT" in prompt


def test_build_prompt_includes_risk_factors_when_present():
    from due_diligence.schemas.reports import FilingsRAGReport
    state = {
        "ticker": "AAPL",
        "filings": FilingsRAGReport(
            ticker="AAPL", stub=False,
            risk_factors_summary="App Store regulation and supply chain concentration.",
        ),
    }
    prompt = _build_critic_prompt(state)
    assert "App Store" in prompt


def test_build_prompt_includes_implied_growth():
    """El growth implícito del reverse DCF debe aparecer en el prompt."""
    from due_diligence.schemas.reports import ValuationReport
    from due_diligence.schemas.base import MetricValue
    state = {
        "ticker": "AAPL",
        "valuation": ValuationReport(
            ticker="AAPL", stub=False,
            reverse_dcf_implied_growth=0.15,
            dcf_upside_pct=12.0,
        ),
    }
    prompt = _build_critic_prompt(state)
    assert "15.0%" in prompt or "15%" in prompt
