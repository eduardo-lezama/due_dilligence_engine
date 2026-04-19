def test_stub_schemas_instantiable():
    from due_diligence.schemas.reports import (
        ManagementReport, FilingsRAGReport, MarketReport,
        CompetitiveReport, ValuationReport, ValueExpertReport, CriticReport,
    )
    assert ManagementReport(ticker="AAPL").stub is True
    assert FilingsRAGReport(ticker="AAPL").stub is True
    assert MarketReport(ticker="AAPL").stub is True
    assert CompetitiveReport(ticker="AAPL").stub is True
    assert ValuationReport(ticker="AAPL").stub is True
    assert ValueExpertReport(ticker="AAPL").stub is True
    assert CriticReport(ticker="AAPL").stub is True
