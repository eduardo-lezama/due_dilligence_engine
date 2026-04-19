from typing import Optional
from pydantic import BaseModel, Field
from due_diligence.schemas.base import Citation, MetricValue


class ManagementReport(BaseModel):
    ticker: str
    stub: bool = True
    insider_ownership_pct: Optional[float] = None
    ceo_tenure_years: Optional[float] = None
    form4_transactions_12m: Optional[int] = None
    compensation_structure_notes: Optional[str] = None
    capital_allocation_summary: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


class FilingsRAGReport(BaseModel):
    ticker: str
    stub: bool = True
    business_description: Optional[str] = None
    risk_factors_summary: Optional[str] = None
    mda_highlights: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


class MarketReport(BaseModel):
    ticker: str
    stub: bool = True
    price_current: Optional[MetricValue] = None
    price_52w_high: Optional[MetricValue] = None
    price_52w_low: Optional[MetricValue] = None
    market_cap: Optional[MetricValue] = None
    recent_news_summary: Optional[str] = None


class CompetitiveReport(BaseModel):
    ticker: str
    stub: bool = True
    moat_classification: Optional[str] = None  # "wide", "narrow", "none"
    moat_sources: list[str] = Field(default_factory=list)
    peer_roic_comparison: list[MetricValue] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class CheckpointInput(BaseModel):
    """User input at a Socratic HITL checkpoint. Serializable for CLI and future webapp."""
    checkpoint_id: str
    user_notes: str = ""
    proceed: bool = True


class ValuationInput(CheckpointInput):
    """Extended input for Checkpoint 2 — valuation assumptions the user can override."""
    wacc_pct: float = 9.0
    terminal_growth_pct: float = 2.5
    projection_years: int = 10
    revenue_growth_yr1_pct: Optional[float] = None
    margin_assumption_pct: Optional[float] = None


class ValuationReport(BaseModel):
    ticker: str
    stub: bool = True
    dcf_intrinsic_value: Optional[MetricValue] = None
    reverse_dcf_implied_growth: Optional[float] = None
    ev_ebitda_multiple: Optional[MetricValue] = None
    pe_multiple: Optional[MetricValue] = None
    magic_formula_rank: Optional[int] = None
    sensitivity_table: list[dict] = Field(default_factory=list)
    assumptions: Optional[ValuationInput] = None


class ValueExpertReport(BaseModel):
    ticker: str
    stub: bool = True
    business_understanding: Optional[str] = None
    moat_analysis: Optional[str] = None
    management_assessment: Optional[str] = None
    financial_quality: Optional[str] = None
    growth_reinvestment: Optional[str] = None
    valuation_section: Optional[str] = None
    risks: Optional[str] = None
    psychological_lens: Optional[str] = None
    decision_frame: Optional[str] = None
    verdict: Optional[str] = None           # ADR-009: must always be present when not stub
    verdict_confidence: Optional[float] = None
    suggested_sizing_pct: Optional[float] = None


class CriticReport(BaseModel):
    ticker: str
    stub: bool = True
    weaknesses: list[str] = Field(default_factory=list)
    contra_thesis: Optional[str] = None
    pre_mortem_scenario: Optional[str] = None
