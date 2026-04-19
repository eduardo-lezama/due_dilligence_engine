import pytest
from pydantic import ValidationError


def test_citation_valid():
    from due_diligence.schemas.base import Citation
    c = Citation(
        claim="Revenue grew 8% YoY",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
        excerpt="Net sales were $391.0 billion, an increase of 2 percent compared to the prior year.",
        confidence=0.95,
    )
    assert c.confidence == 0.95
    assert c.gaap_vs_nongaap_flag is None


def test_citation_unverified_has_zero_confidence():
    from due_diligence.schemas.base import Citation
    c = Citation(
        claim="Some claim",
        source_url="https://example.com",
        excerpt="",
        confidence=0.0,
    )
    assert c.confidence == 0.0


def test_citation_confidence_out_of_range_raises():
    from due_diligence.schemas.base import Citation
    with pytest.raises(ValidationError):
        Citation(
            claim="x",
            source_url="https://example.com",
            excerpt="x",
            confidence=1.5,
        )


def test_metric_value_valid():
    from due_diligence.schemas.base import MetricValue
    m = MetricValue(
        value=391_035_000_000.0,
        unit="USD",
        period="FY2024",
        source="XBRL",
        currency_code="USD",
        gaap_vs_nongaap="GAAP",
    )
    assert m.value == 391_035_000_000.0


def test_financials_report_stub():
    from due_diligence.schemas.base import FinancialsReport, MetricValue
    from datetime import date

    def _mv(value: float, period: str) -> MetricValue:
        return MetricValue(value=value, unit="USD", period=period, source="XBRL")

    report = FinancialsReport(
        ticker="AAPL",
        cik="0000320193",
        as_of_date=date(2024, 9, 28),
        revenue=[_mv(391_035e6, "FY2024"), _mv(383_285e6, "FY2023")],
        gross_profit=[_mv(180_683e6, "FY2024")],
        operating_income=[_mv(123_216e6, "FY2024")],
        net_income=[_mv(93_736e6, "FY2024")],
        operating_cash_flow=[_mv(118_254e6, "FY2024")],
        capex=[_mv(9_447e6, "FY2024")],
        free_cash_flow=[_mv(108_807e6, "FY2024")],
        total_assets=_mv(364_980e6, "FY2024"),
        total_debt=_mv(101_304e6, "FY2024"),
        cash_and_equivalents=_mv(65_171e6, "FY2024"),
        shares_outstanding=MetricValue(
            value=15_204_137_000.0, unit="shares", period="FY2024", source="XBRL"
        ),
        source_filing="10-K FY2024",
    )
    assert report.ticker == "AAPL"
    assert len(report.revenue) == 2
    assert report.free_cash_flow[0].value == pytest.approx(108_807e6)
