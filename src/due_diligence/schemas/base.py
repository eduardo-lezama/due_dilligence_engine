from datetime import date
from typing import Optional
from pydantic import BaseModel, Field


class Citation(BaseModel):
    claim: str
    source_url: str
    excerpt: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    gaap_vs_nongaap_flag: Optional[str] = None  # "GAAP", "Non-GAAP", or None


class Claim(BaseModel):
    text: str
    citation: Optional[Citation] = None
    verified: bool = False


class MetricValue(BaseModel):
    value: float
    unit: str = "USD"
    period: str
    source: str  # "XBRL", "calculated", "10-K", etc.
    currency_code: str = "USD"
    gaap_vs_nongaap: str = "GAAP"


class FinancialsReport(BaseModel):
    ticker: str
    cik: str
    as_of_date: date

    # Income statement (list = multi-year, index 0 = most recent)
    revenue: list[MetricValue]
    gross_profit: list[MetricValue]
    operating_income: list[MetricValue]
    net_income: list[MetricValue]

    # Cash flow
    operating_cash_flow: list[MetricValue]
    capex: list[MetricValue]
    free_cash_flow: list[MetricValue]

    # Balance sheet (point-in-time, most recent)
    total_assets: MetricValue
    total_debt: MetricValue
    cash_and_equivalents: MetricValue

    # Share data
    shares_outstanding: MetricValue

    # Meta
    source_filing: str
    citations: list[Citation] = Field(default_factory=list)
