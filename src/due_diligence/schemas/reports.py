"""
Schemas de reportes — modelos Pydantic para outputs de agentes especializados.

Qué hace este módulo:
    Define los modelos Pydantic para los reportes que generan los agentes LLM:
    - ManagementReport: insider ownership, Form 4, compensación
    - FilingsRAGReport: análisis de 10-K (business, riesgos, MDA)
    - MarketReport: precio actual, market cap, noticias
    - CompetitiveReport: moat, peer ROIC comparison
    - ValuationReport: DCF, múltiplos, sensitivity analysis
    - ValueExpertReport: análisis integrado + VERDICT (ADR-009)
    - CriticReport: contra-tesis, pre-mortem

Stub pattern:
    Todos los reportes tienen stub=True por defecto. Un stub es un placeholder
    que indica que el agente aún no ha generado el contenido real. Cuando el
    agente corre, stub=False y los campos se llenan con contenido real.

CheckpointInput:
    Modelo para input del usuario en checkpoints HITL (Human-in-the-Loop).
    Serializable para CLI y futura webapp.

ADR-009 (Verdict):
    ValueExpertReport.verdict debe estar presente cuando no es stub.
    Es el output final del sistema: BUY/SELL/HOLD con justificación.
"""

from typing import Optional
from pydantic import BaseModel, Field
from due_diligence.schemas.base import Citation, MetricValue


class ManagementReport(BaseModel):
    """Reporte de gestión e insider ownership."""
    ticker: str
    stub: bool = True  # True = placeholder, False = contenido real generado
    insider_ownership_pct: Optional[float] = None  # % de acciones owned por insiders
    ceo_tenure_years: Optional[float] = None  # Años del CEO en el cargo
    form4_transactions_12m: Optional[int] = None  # Número de transacciones Form 4 en 12m
    compensation_structure_notes: Optional[str] = None  # Notas sobre estructura de compensación
    capital_allocation_summary: Optional[str] = None  # Resumen de allocation de capital
    citations: list[Citation] = Field(default_factory=list)


class FilingsRAGReport(BaseModel):
    """Reporte de análisis de filings SEC (10-K, 10-Q) usando RAG."""
    ticker: str
    stub: bool = True
    business_description: Optional[str] = None  # Descripción del negocio
    risk_factors_summary: Optional[str] = None  # Resumen de factores de riesgo
    mda_highlights: Optional[str] = None  # Highlights de Management Discussion & Analysis
    citations: list[Citation] = Field(default_factory=list)


class MarketReport(BaseModel):
    """Reporte de datos de mercado (precio, market cap, noticias)."""
    ticker: str
    stub: bool = True
    price_current: Optional[MetricValue] = None  # Precio actual
    price_52w_high: Optional[MetricValue] = None  # Máximo 52 semanas
    price_52w_low: Optional[MetricValue] = None  # Mínimo 52 semanas
    market_cap: Optional[MetricValue] = None  # Capitalización de mercado
    recent_news_summary: Optional[str] = None  # Resumen de noticias recientes


class CompetitiveReport(BaseModel):
    """Reporte de análisis competitivo y moat."""
    ticker: str
    stub: bool = True
    moat_classification: Optional[str] = None  # "wide", "narrow", "none"
    moat_sources: list[str] = Field(default_factory=list)  # Fuentes del moat
    peer_roic_comparison: list[MetricValue] = Field(default_factory=list)  # ROIC vs peers
    citations: list[Citation] = Field(default_factory=list)


class CheckpointInput(BaseModel):
    """Input del usuario en checkpoint HITL. Serializable para CLI y webapp."""
    checkpoint_id: str  # ID del checkpoint (checkpoint1, checkpoint2, etc.)
    user_notes: str = ""  # Notas del usuario
    proceed: bool = True  # Si el usuario quiere continuar


class ValuationInput(CheckpointInput):
    """Input extendido para Checkpoint 2 — assumptions de valuation."""
    wacc_pct: float = 9.0  # Weighted Average Cost of Capital
    terminal_growth_pct: float = 2.5  # Growth rate terminal
    projection_years: int = 10  # Años de proyección DCF
    revenue_growth_yr1_pct: Optional[float] = None  # Growth revenue año 1
    margin_assumption_pct: Optional[float] = None  # Margen operativo asumido


class ValuationReport(BaseModel):
    """Reporte de valuation (DCF, múltiplos, sensitivity)."""
    ticker: str
    stub: bool = True
    dcf_intrinsic_value: Optional[MetricValue] = None  # Valor intrínseco DCF (equity value total)
    dcf_intrinsic_per_share: Optional[MetricValue] = None  # Valor intrínseco por acción
    dcf_upside_pct: Optional[float] = None             # (intrinsic - market_cap) / market_cap
    price_current: Optional[MetricValue] = None        # Precio de mercado actual (copia de MarketReport)
    reverse_dcf_implied_growth: Optional[float] = None  # Growth implícito en precio actual
    ev_ebit_multiple: Optional[MetricValue] = None      # EV/EBIT (Greenblatt — EBITDA no disponible en XBRL básico)
    ev_fcf_multiple: Optional[MetricValue] = None       # EV/FCF (Owner earnings proxy)
    pe_multiple: Optional[MetricValue] = None           # P/E (market_cap / net_income)
    earnings_yield_pct: Optional[float] = None          # EBIT/EV × 100 — componente Magic Formula
    magic_formula_rank: Optional[int] = None            # Rank según Magic Formula (requiere peer list — None en Phase 1)
    sensitivity_table: list[dict] = Field(default_factory=list)  # Tabla de sensibilidad WACC × growth
    assumptions: Optional[ValuationInput] = None        # Assumptions usadas en el DCF


class ValueExpertReport(BaseModel):
    """
    Reporte del Value Expert — análisis integrado + VERDICT.

    ADR-009: verdict debe estar presente cuando stub=False.
    Es el output final del sistema: BUY/SELL/HOLD con justificación.
    """
    ticker: str
    stub: bool = True
    business_understanding: Optional[str] = None  # Entendimiento del negocio
    moat_analysis: Optional[str] = None  # Análisis del moat
    management_assessment: Optional[str] = None  # Evaluación del management
    financial_quality: Optional[str] = None  # Calidad financiera
    growth_reinvestment: Optional[str] = None  # Growth y reinversión
    valuation_section: Optional[str] = None  # Sección de valuation
    risks: Optional[str] = None  # Riesgos
    psychological_lens: Optional[str] = None  # Lente psicológico
    decision_frame: Optional[str] = None  # Frame de decisión
    verdict: Optional[str] = None  # ADR-009: BUY/SELL/HOLD (requerido cuando no stub)
    verdict_confidence: Optional[float] = None  # Confianza en el verdict (0.0-1.0)
    suggested_sizing_pct: Optional[float] = None  # % de portfolio sugerido
    verdict_rationale: Optional[str] = None  # 2-3 frases: por qué este verdict


class CriticReport(BaseModel):
    """Reporte del Critic — contra-tesis y pre-mortem."""
    ticker: str
    stub: bool = True
    weaknesses: list[str] = Field(default_factory=list)  # Debilidades identificadas
    contra_thesis: Optional[str] = None  # Contra-tesis
    pre_mortem_scenario: Optional[str] = None  # Escenario pre-mortem
