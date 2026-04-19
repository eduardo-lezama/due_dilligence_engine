import json
from pathlib import Path


def test_smoke_financials_pipeline_from_fixture():
    """Full pipeline: fixture dict -> EdgarAdapter.parse -> FinancialsReport shape check."""
    fixture_path = Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json"
    raw = json.loads(fixture_path.read_text())

    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.schemas.base import FinancialsReport, MetricValue

    report = EdgarAdapter.parse_financials(raw)

    assert isinstance(report, FinancialsReport)
    assert report.ticker == "AAPL"
    assert report.cik == "0000320193"
    assert len(report.revenue) >= 1
    assert all(isinstance(m, MetricValue) for m in report.revenue)
    assert report.free_cash_flow[0].source == "calculated"
    assert report.total_debt.value > 0
    assert report.shares_outstanding.unit == "shares"


def test_smoke_llm_client_importable():
    import importlib
    import due_diligence.llm.client as mod
    importlib.reload(mod)

    from due_diligence.llm.client import LLMClient
    client = mod.get_client()
    assert isinstance(client, LLMClient)
