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
    """
    sizing_pct se clampea por dos vías:
    1. Techo absoluto 20%
    2. Techo por confidence (Fix D): max sizing ≤ confidence × 10

    Con confidence=0.8, max sizing = 8% (más restrictivo que el techo absoluto).
    """
    raw = json.dumps({"verdict": "BUY", "verdict_confidence": 0.8, "suggested_sizing_pct": 50.0})
    report = _parse_response(raw, "TEST")
    # 50 → clamp a 20 → re-clamp por confidence (0.8 × 10 = 8.0)
    assert report.suggested_sizing_pct == pytest.approx(8.0)


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
    """Con historical_cagr_5y + historical_cagr_years, _format_context usa el span real."""
    from due_diligence.agents.value_expert import _format_context
    # Con 4 años reales (FSLR-like)
    context = _format_context({"ticker": "FSLR", "historical_cagr_5y": 0.258, "historical_cagr_years": 3})
    assert "25.8%" in context
    assert "3y" in context  # debe decir "3y", no "5y"
    assert "CAGR" in context


def test_format_context_omits_cagr_block_when_absent():
    """Sin historical_cagr_5y en el state, el bloque CAGR no aparece."""
    from due_diligence.agents.value_expert import _format_context
    context = _format_context({"ticker": "AAPL"})
    assert "CAGR" not in context or "Revenue" not in context  # no bloque historical context


def test_format_context_always_surfaces_margin_trend():
    """
    EC-22 / Fix B: gross margin trend debe aparecer SIEMPRE que haya ≥2 años de datos,
    no solo en compresión. Antes del fix, BBW (margen mejorando 52.5%→55.8%) no veía
    el trend en context — el §4 decía 'margin trend not provided'.
    """
    import json
    from pathlib import Path
    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.agents.value_expert import _format_context

    fixture = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())
    financials = EdgarAdapter.parse_financials(fixture)
    context = _format_context({"ticker": "AAPL", "financials": financials})
    # AAPL margins improving slightly — trend debe aparecer aunque NO haya compresión
    assert "Gross margin trend" in context


def test_format_context_surfaces_mgmt_citations_for_section_3():
    """
    EC-23 / Fix C: las citations de mgmt deben surfacearse al contexto del Expert
    para que la regla §3 MUST (reconcile insider sale price vs current price) tenga
    los datos. Antes del fix, las citations solo iban al appendix del markdown.
    """
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import ManagementReport
    from due_diligence.schemas.base import Citation

    state = {
        "ticker": "BBW",
        "mgmt": ManagementReport(
            ticker="BBW", stub=False,
            insider_transaction_pattern="CEO net seller",
            citations=[
                Citation(
                    claim="CEO sold shares in September 2025",
                    excerpt="Sharon Price John (CEO) | SELL | 15,288 shares @ $73.21 | 2025-09-16",
                    source_url="form4.html",
                    confidence=1.0,
                )
            ],
        ),
    }
    context = _format_context(state)
    # El precio $73.21 debe aparecer en el contexto, no solo en el appendix de citations
    assert "$73.21" in context
    assert "Insider transaction evidence" in context


def test_parse_response_clamps_sizing_to_confidence_ceiling():
    """
    EC-24 / Fix D: el sizing se clampea a max(confidence × 10) para evitar el
    bug de "confidence baja, sizing alto" detectado en BBW (64% confidence, 3% sizing).
    """
    import json
    from due_diligence.agents.value_expert import _parse_response

    # LLM emite sizing=8% con confidence=0.5 → ceiling debería ser 5%
    raw = json.dumps({
        "verdict": "HOLD",
        "verdict_confidence": 0.5,
        "suggested_sizing_pct": 8.0,
    })
    report = _parse_response(raw, "FAKE")
    # 0.5 × 10 = 5.0 → sizing clamped to 5.0
    assert report.suggested_sizing_pct == pytest.approx(5.0)


def test_parse_response_does_not_clamp_when_consistent():
    """Sizing dentro del techo (confidence × 10) no se clampea."""
    import json
    from due_diligence.agents.value_expert import _parse_response

    raw = json.dumps({
        "verdict": "BUY",
        "verdict_confidence": 0.85,
        "suggested_sizing_pct": 7.0,  # 7 < 0.85 × 10 = 8.5 → no clamp
    })
    report = _parse_response(raw, "FAKE")
    assert report.suggested_sizing_pct == pytest.approx(7.0)


def test_format_context_includes_reinvestment_rate(aapl_raw_path=None):
    """MCE-22: reinvestment rate (capex/OCF) precomputado en contexto del Expert."""
    import json
    from pathlib import Path
    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.agents.value_expert import _format_context

    fixture = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())
    financials = EdgarAdapter.parse_financials(fixture)
    context = _format_context({"ticker": "AAPL", "financials": financials})
    # AAPL tiene OCF y capex disponibles; debe aparecer "Reinvestment rate"
    assert "Reinvestment rate" in context
    # AAPL es una empresa madura — capex/OCF típicamente <20% → "harvest mode"
    assert any(mode in context for mode in ["harvest mode", "balanced", "invest mode"])


def test_system_prompt_mce22_requires_insider_price_reconciliation():
    """MCE-22: §3 debe forzar reconciliación de precios insider vs precio actual."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "insider sale price" in _SYSTEM_PROMPT.lower()
    assert "current" in _SYSTEM_PROMPT.lower()


def test_system_prompt_mce22_requires_reinvestment_rate():
    """MCE-22: §5 debe forzar reportar reinvestment rate."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "reinvestment rate" in _SYSTEM_PROMPT.lower()


def test_system_prompt_mce22_requires_value_trap_pattern_check():
    """MCE-22: §7 debe forzar cross-check de news + insider + margins."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "value trap" in _SYSTEM_PROMPT.lower()


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


# ---------------------------------------------------------------------------
# Tests: _thesis_break_self_check (EC-45)
# ---------------------------------------------------------------------------

def test_format_context_cagr_short_span_qualifier():
    """CAGR con <4 años emite qualifier de SHORT SPAN."""
    from due_diligence.agents.value_expert import _format_context
    context = _format_context({"ticker": "FSLR", "historical_cagr_5y": 0.25, "historical_cagr_years": 2})
    assert "SHORT SPAN" in context
    assert "2 years" in context


def test_format_context_cagr_extreme_growth_qualifier():
    """CAGR >40% emite qualifier de EXTREME GROWTH."""
    from due_diligence.agents.value_expert import _format_context
    context = _format_context({"ticker": "NVDA", "historical_cagr_5y": 0.55, "historical_cagr_years": 5})
    assert "EXTREME GROWTH" in context
    assert "SHORT SPAN" not in context  # 5 años es suficiente


def test_format_context_cagr_negative_qualifier():
    """CAGR negativo emite qualifier de NEGATIVE CAGR."""
    from due_diligence.agents.value_expert import _format_context
    context = _format_context({"ticker": "INTC", "historical_cagr_5y": -0.05, "historical_cagr_years": 5})
    assert "NEGATIVE CAGR" in context


def test_format_context_includes_quarterly_snapshot():
    """Con quarterly_snapshot en state, aparece como QUARTERLY XBRL en contexto."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import QuarterlySnapshot

    state = {
        "ticker": "MRCY",
        "quarterly_snapshot": QuarterlySnapshot(
            period="Q1 2026",
            revenue=250_000_000,
            operating_income=-15_000_000,
            gross_profit=80_000_000,
        ),
    }
    context = _format_context(state)
    assert "QUARTERLY XBRL" in context
    assert "Q1 2026" in context
    assert "$250" in context


def test_thesis_break_detects_margin_decline_contradiction():
    """Si decision_frame dice 'thesis breaks if margin decline' y §4 ya dice margin decline → warning."""
    from due_diligence.agents.value_expert import _thesis_break_self_check

    report = ValueExpertReport(
        ticker="MRCY", stub=False,
        decision_frame="Thesis breaks if gross margin decline continues below 30% for two quarters.",
        financial_quality="Gross margin has been in decline from 45% to 32%, compressing rapidly.",
        risks="Operating margin decline is accelerating.",
        verdict="HOLD", verdict_confidence=0.5,
    )
    checked = _thesis_break_self_check(report, "")
    assert "THESIS-BREAK SELF-CHECK WARNING" in (checked.decision_frame or "")
    assert "margin decline" in (checked.decision_frame or "").lower()


def test_thesis_break_no_false_positive_on_forward_looking():
    """Si el thesis-breaker es forward-looking y no ya presente → no caveat."""
    from due_diligence.agents.value_expert import _thesis_break_self_check

    report = ValueExpertReport(
        ticker="AAPL", stub=False,
        decision_frame="Thesis breaks if iPhone unit growth turns negative for 3 consecutive quarters.",
        financial_quality="Strong FCF conversion, margins expanding. Revenue growing 8% YoY.",
        risks="App Store regulatory risk in EU.",
        verdict="BUY", verdict_confidence=0.75,
    )
    checked = _thesis_break_self_check(report, "")
    assert "THESIS-BREAK SELF-CHECK WARNING" not in (checked.decision_frame or "")


def test_thesis_break_no_crash_on_empty_sections():
    """Con secciones vacías no falla."""
    from due_diligence.agents.value_expert import _thesis_break_self_check

    report = ValueExpertReport(
        ticker="TEST", stub=False,
        decision_frame="Thesis breaks if revenue decline exceeds 20%.",
        verdict="HOLD", verdict_confidence=0.5,
    )
    checked = _thesis_break_self_check(report, "")
    # No own_text → no puede detectar contradicción → no caveat
    assert "THESIS-BREAK SELF-CHECK WARNING" not in (checked.decision_frame or "")


def test_thesis_break_detects_guidance_cut():
    """Guidance cut en thesis-breaker ya descrito en risks → warning."""
    from due_diligence.agents.value_expert import _thesis_break_self_check

    report = ValueExpertReport(
        ticker="XYZ", stub=False,
        decision_frame="Thesis breaks if management issues a guidance cut for FY2027.",
        risks="Management already issued a guidance cut of 15% for FY2026, withdrawing full-year outlook.",
        financial_quality="Revenue declining 8% QoQ.",
        verdict="HOLD", verdict_confidence=0.4,
    )
    checked = _thesis_break_self_check(report, "")
    assert "THESIS-BREAK SELF-CHECK WARNING" in (checked.decision_frame or "")


# ---------------------------------------------------------------------------
# Tests: MCE-18 — peer relative valuation context in _format_context
# ---------------------------------------------------------------------------

def test_value_expert_context_includes_peer_relative_valuation_block():
    """_format_context includes PEER RELATIVE VALUATION block when peer_valuations present."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import PeerValuation, ValuationReport
    from due_diligence.schemas.base import MetricValue

    state = {
        "ticker": "AAPL",
        "peer_valuations": [
            PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5, fcf_yield_pct=3.2, mf_rank=2),
            PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1, fcf_yield_pct=4.9, mf_rank=1),
        ],
        "valuation": ValuationReport(
            ticker="AAPL",
            stub=False,
            ev_ebit_multiple=MetricValue(value=12.3, unit="x", period="FY2025", source="calculated"),
            earnings_yield_pct=8.1,
            magic_formula_rank=3,
        ),
    }

    context = _format_context(state)

    assert "PEER RELATIVE VALUATION" in context
    assert "§6" in context
    assert "EV/EBIT" in context
    assert "Peer median" in context
    assert "MF rank" in context
    assert "discount" in context or "premium" in context


def test_value_expert_context_peer_block_absent_when_no_peer_valuations():
    """PEER RELATIVE VALUATION block absent when no peer_valuations in state."""
    from due_diligence.agents.value_expert import _format_context

    context = _format_context({"ticker": "AAPL"})

    assert "PEER RELATIVE VALUATION" not in context


def test_value_expert_context_warns_about_excluded_peers():
    """Context includes WARNING for peers with no_market_data=True."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import PeerValuation

    state = {
        "ticker": "AAPL",
        "peer_valuations": [
            PeerValuation(ticker="VALID", roic=0.20, period="FY2025", ev_ebit=15.0, mf_rank=1),
            PeerValuation(ticker="NODATA", roic=0.25, period="FY2025", no_market_data=True),
        ],
    }

    context = _format_context(state)

    assert "NODATA" in context
    assert "excluded" in context.lower()
    assert "[WARNING]" in context


# ---------------------------------------------------------------------------
# Tests: DCF GROWTH ASSUMPTION block en _format_context (EC-54)
# ---------------------------------------------------------------------------

def test_format_context_includes_dcf_growth_assumption_block():
    """_format_context emite bloque DCF GROWTH ASSUMPTION cuando near_term_growth disponible."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import ValuationReport

    state = {
        "ticker": "BBW",
        "valuation": ValuationReport(
            ticker="BBW", stub=False,
            near_term_growth_used_pct=0.08,
            near_term_growth_source="revenue_cagr_fallback",
            dcf_upside_pct=15.0,
        ),
    }
    context = _format_context(state)
    assert "DCF GROWTH ASSUMPTION" in context
    assert "8.0%" in context or "8%" in context
    assert "revenue" in context.lower()


def test_format_context_dcf_growth_default_has_caution_warning():
    """Para default_7pct, el bloque debe incluir CAUTION."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import ValuationReport

    state = {
        "ticker": "NOHIST",
        "valuation": ValuationReport(
            ticker="NOHIST", stub=False,
            near_term_growth_used_pct=0.07,
            near_term_growth_source="default_7pct",
        ),
    }
    context = _format_context(state)
    assert "CAUTION" in context or "caution" in context.lower()
    assert "7" in context


def test_format_context_dcf_growth_absent_when_near_term_growth_none():
    """Sin near_term_growth_used_pct, no se emite el bloque DCF GROWTH ASSUMPTION."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import ValuationReport

    state = {
        "ticker": "TEST",
        "valuation": ValuationReport(ticker="TEST", stub=False),
    }
    context = _format_context(state)
    assert "DCF GROWTH ASSUMPTION" not in context


def test_system_prompt_requires_dcf_growth_reflexion():
    """El system prompt debe exigir reflexión sobre el growth assumption en §6."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "DCF GROWTH ASSUMPTION" in _SYSTEM_PROMPT or "growth assumption" in _SYSTEM_PROMPT.lower()
