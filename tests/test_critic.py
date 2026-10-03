"""
Tests para critic_agent.

Qué se testea:
    - critic_agent(): nodo LangGraph completo con LLM mockeado.
    - structured() + fail loud (ADR-032): LLMError → stub=True + error; sin síntesis → skip.
    - _build_critic_prompt(): incluye VERDICT del expert y datos de riesgo.
    - Degraded mode: corre con estado mínimo sin fallar.
    - Modelo: usa openai_expert_model igual que el value_expert.
"""

import pytest

from due_diligence.agents.critic import critic_agent, _build_critic_prompt
from due_diligence.llm.errors import LLMProviderError
from due_diligence.schemas.llm_outputs import CriticLLMOutput
from due_diligence.schemas.reports import CriticReport, ValueExpertReport


_VALID_LLM_RESPONSE = CriticLLMOutput(**{
    "weaknesses": [
        "Apple's iPhone revenue (50% of total) has shown flat growth for 3 consecutive years, making the bull case dependent on Services continuing to grow at 13%+ — which requires the installed base to keep expanding.",
        "Regulatory risk is more acute than the bull case acknowledges: EU DMA forces sideloading by 2025 and US DOJ antitrust case targets App Store commissions directly — both threaten the high-margin Services segment.",
        "Reverse DCF implies 15% embedded growth. Apple's 5-year FCF CAGR is 8%. The current price requires either Services acceleration or a new product category — neither is certain.",
    ],
    "contra_thesis": "Apple is a mature hardware company being valued as a software compounder. The Services moat is regulatory, not structural — one adverse ruling removes the 30% App Store cut that drives most of the margin expansion. The stock is priced for perfection in a scenario where regulatory outcomes are binary.",
    "pre_mortem_scenario": "In 2027, the EU DMA enforcement reduced App Store commissions from 30% to 10%. Services gross margin contracted from 74% to 58%. The FCF multiple re-rated from 28x to 20x and the stock fell 35% despite stable iPhone volumes.",
})


def _critic_state(**extra) -> dict:
    """Estado mínimo con síntesis válida: sin ella el Critic se salta (ADR-032)."""
    st = {"ticker": "AAPL",
          "synthesis": ValueExpertReport(ticker="AAPL", stub=False, verdict="BUY",
                                         verdict_confidence=0.7, suggested_sizing_pct=5.0)}
    st.update(extra)
    return st


@pytest.fixture
def critic_state():
    return _critic_state()


def _mock_critic(mocker, response=_VALID_LLM_RESPONSE):
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = response
    mocker.patch("due_diligence.agents.critic.get_client", return_value=mock_client)
    return mock_client


# ---------------------------------------------------------------------------
# Tests: nodo LangGraph completo
# ---------------------------------------------------------------------------

def test_critic_returns_critique_key(mocker):
    _mock_critic(mocker)
    result = critic_agent(_critic_state())
    assert "critique" in result


def test_critic_stub_false(mocker):
    _mock_critic(mocker)
    result = critic_agent(_critic_state())
    assert result["critique"].stub is False


def test_critic_weaknesses_populated(mocker):
    _mock_critic(mocker)
    report: CriticReport = critic_agent(_critic_state())["critique"]
    assert len(report.weaknesses) == 3
    assert all(isinstance(w, str) and len(w) > 0 for w in report.weaknesses)


def test_critic_contra_thesis_populated(mocker):
    _mock_critic(mocker)
    report = critic_agent(_critic_state())["critique"]
    assert report.contra_thesis is not None
    assert len(report.contra_thesis) > 10


def test_critic_pre_mortem_populated(mocker):
    _mock_critic(mocker)
    report = critic_agent(_critic_state())["critique"]
    assert report.pre_mortem_scenario is not None


def test_critic_uses_expert_model(mocker):
    """El critic usa openai_expert_model — mismo que el value_expert."""
    from due_diligence.config import settings
    mock_client = _mock_critic(mocker)
    critic_agent(_critic_state())
    call_kwargs = mock_client.structured.call_args
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

    call_kwargs = mock_client.structured.call_args
    user_prompt = call_kwargs.kwargs["messages"][0]["content"]
    assert "BUY" in user_prompt


def test_critic_degraded_minimal_state(mocker):
    """Con solo ticker + síntesis (sin financials/valuation), produce un CriticReport sin fallar."""
    _mock_critic(mocker)
    result = critic_agent(_critic_state())
    assert isinstance(result["critique"], CriticReport)
    assert result["critique"].stub is False


# ---------------------------------------------------------------------------
# Tests: structured() + fail loud (H1-T2c, ADR-032, EC-68)
# ---------------------------------------------------------------------------

def test_critic_structured_ok(mocker, critic_state):
    client = mocker.MagicMock()
    client.structured.return_value = CriticLLMOutput(weaknesses=["w1"], contra_thesis="ct",
                                                     pre_mortem_scenario="pm")
    mocker.patch("due_diligence.agents.critic.get_client", return_value=client)
    rep = critic_agent(critic_state)["critique"]
    assert rep.stub is False and rep.weaknesses == ["w1"] and rep.error is None
    assert client.structured.call_args.args[0] is CriticLLMOutput


def test_critic_fails_loud(mocker, critic_state):
    client = mocker.MagicMock()
    client.structured.side_effect = LLMProviderError("down", provider="openai", fallback_eligible=True)
    mocker.patch("due_diligence.agents.critic.get_client", return_value=client)
    rep = critic_agent(critic_state)["critique"]
    assert rep.stub is True and rep.error.startswith("LLMProviderError") and rep.contra_thesis is None


def test_critic_skips_when_synthesis_is_stub(mocker):
    client = _mock_critic(mocker)
    st = {"ticker": "AAPL", "synthesis": ValueExpertReport(ticker="AAPL", stub=True, error="LLMTruncatedError: x")}
    rep = critic_agent(st)["critique"]
    assert rep.stub is True and rep.error == "skipped: synthesis unavailable"
    client.structured.assert_not_called()


def test_critic_drops_blank_weaknesses(mocker, critic_state):
    _mock_critic(mocker, CriticLLMOutput(weaknesses=["real", " ", ""], contra_thesis=None,
                                         pre_mortem_scenario=None))
    assert critic_agent(critic_state)["critique"].weaknesses == ["real"]


# ---------------------------------------------------------------------------
# Tests: _detect_red_flags (MCE-23)
# ---------------------------------------------------------------------------

def test_detect_red_flags_dcf_spread():
    """Si valuation tiene flag de spread DCF, _detect_red_flags lo emite."""
    from due_diligence.agents.critic import _detect_red_flags
    from due_diligence.schemas.reports import ValuationReport

    state = {
        "ticker": "FAKE",
        "valuation": ValuationReport(
            ticker="FAKE", stub=False,
            dcf_reliability_note="⚠️ DCF spread note: forward DCF uses 25% but market prices -2%.",
        ),
    }
    flags = _detect_red_flags(state)
    assert any("spread" in f.lower() for f in flags)


def test_detect_red_flags_high_capital_returns():
    """Capital returns >80% del FCF dispara flag de empresa madura sin reinversión."""
    from due_diligence.agents.critic import _detect_red_flags
    from due_diligence.schemas.base import FinancialsReport, MetricValue
    from datetime import date

    state = {
        "ticker": "MATURE",
        "financials": FinancialsReport(
            ticker="MATURE", cik="0000001", as_of_date=date(2026, 1, 31),
            revenue=[MetricValue(value=500_000_000, unit="USD", period="FY2026", source="XBRL")],
            gross_profit=[],
            operating_income=[],
            net_income=[],
            operating_cash_flow=[],
            capex=[],
            free_cash_flow=[MetricValue(value=40_000_000, unit="USD", period="FY2026", source="calculated")],
            buybacks=[MetricValue(value=28_000_000, unit="USD", period="FY2026", source="XBRL")],
            dividends=[MetricValue(value=11_000_000, unit="USD", period="FY2026", source="XBRL")],
            total_assets=MetricValue(value=200_000_000, unit="USD", period="FY2026", source="XBRL"),
            total_debt=MetricValue(value=0, unit="USD", period="FY2026", source="XBRL"),
            cash_and_equivalents=MetricValue(value=27_000_000, unit="USD", period="FY2026", source="XBRL"),
            shares_outstanding=MetricValue(value=12_000_000, unit="shares", period="FY2026", source="XBRL"),
            source_filing="10-K FY2026",
        ),
    }
    flags = _detect_red_flags(state)
    # 39M / 40M = 97.5% del FCF → flag se activa
    assert any("HIGH CAPITAL RETURN" in f for f in flags)


def test_detect_red_flags_margin_compression_3y():
    """3+ años de compresión de gross margin → flag estructural."""
    from due_diligence.agents.critic import _detect_red_flags
    from due_diligence.schemas.base import FinancialsReport, MetricValue
    from datetime import date

    # Margins: FY2026 18.4%, FY2025 19.8%, FY2024 21.2% (descendente desde más antiguo)
    state = {
        "ticker": "DECLINE",
        "financials": FinancialsReport(
            ticker="DECLINE", cik="0000001", as_of_date=date(2026, 1, 31),
            revenue=[
                MetricValue(value=1_100_000_000, unit="USD", period="FY2026", source="XBRL"),
                MetricValue(value=1_050_000_000, unit="USD", period="FY2025", source="XBRL"),
                MetricValue(value=1_000_000_000, unit="USD", period="FY2024", source="XBRL"),
            ],
            gross_profit=[
                MetricValue(value=202_000_000, unit="USD", period="FY2026", source="XBRL"),  # 18.4%
                MetricValue(value=208_000_000, unit="USD", period="FY2025", source="XBRL"),  # 19.8%
                MetricValue(value=212_000_000, unit="USD", period="FY2024", source="XBRL"),  # 21.2%
            ],
            operating_income=[],
            net_income=[],
            operating_cash_flow=[],
            capex=[],
            free_cash_flow=[],
            total_assets=MetricValue(value=500_000_000, unit="USD", period="FY2026", source="XBRL"),
            total_debt=MetricValue(value=0, unit="USD", period="FY2026", source="XBRL"),
            cash_and_equivalents=MetricValue(value=50_000_000, unit="USD", period="FY2026", source="XBRL"),
            shares_outstanding=MetricValue(value=12_000_000, unit="shares", period="FY2026", source="XBRL"),
            source_filing="10-K FY2026",
        ),
    }
    flags = _detect_red_flags(state)
    assert any("MARGIN COMPRESSION" in f for f in flags)


def test_detect_red_flags_no_flags_when_clean():
    """Empresa sana sin flags → lista vacía."""
    from due_diligence.agents.critic import _detect_red_flags
    flags = _detect_red_flags({"ticker": "CLEAN"})
    assert flags == []


def test_critic_prompt_includes_detected_flags(mocker):
    """El prompt del critic incluye la sección DETECTED FLAGS cuando hay flags."""
    from due_diligence.schemas.reports import ValuationReport
    mock_client = _mock_critic(mocker)
    state = _critic_state(
        ticker="FAKE",
        valuation=ValuationReport(
            ticker="FAKE", stub=False,
            dcf_reliability_note="⚠️ DCF spread note: forward vs reverse diverge.",
        ),
    )
    critic_agent(state)
    user_prompt = mock_client.structured.call_args.kwargs["messages"][0]["content"]
    assert "DETECTED FLAGS" in user_prompt
    assert "spread" in user_prompt.lower()


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
