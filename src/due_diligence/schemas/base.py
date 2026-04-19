"""
Schemas base — modelos Pydantic para datos fundamentales del sistema.

Qué hace este módulo:
    Define los modelos Pydantic base que se usan en todo el proyecto:
    - Citation: cita validada con substring-match en fuentes
    - Claim: afirmación que puede estar verificada o no
    - MetricValue: valor numérico con metadata (periodo, fuente, unidad)
    - FinancialsReport: reporte financiero completo de EDGAR

Por qué Pydantic:
    - Validación automática de tipos
    - Serialización/deserialización JSON
    - Type hints para mypy
    - Mensajes de error claros

ADR-005 (Zero hallucination numérica):
    Los valores financieros en FinancialsReport vienen 100% de EDGAR XBRL.
    El LLM nunca genera estos números directamente — solo los interpreta.
"""

from datetime import date
from typing import Optional
from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Cita validada con substring-match en una fuente."""
    claim: str  # Texto del claim que se cita
    source_url: str  # URL de la fuente (10-K, 10-Q, etc.)
    excerpt: str  # Fragmento exacto de la fuente que contiene el claim
    confidence: float = Field(..., ge=0.0, le=1.0)  # 0.0-1.0: confianza del match
    gaap_vs_nongaap_flag: Optional[str] = None  # "GAAP", "Non-GAAP", or None


class Claim(BaseModel):
    """Afirmación que puede estar verificada o no."""
    text: str  # Texto del claim
    citation: Optional[Citation] = None  # Si está verificado, la cita
    verified: bool = False  # True si se encontró substring-match


class MetricValue(BaseModel):
    """Valor numérico con metadata (periodo, fuente, unidad)."""
    value: float  # Valor numérico
    unit: str = "USD"  # Unidad (USD, shares, etc.)
    period: str  # Periodo (FY2025, Q1 2025, etc.)
    source: str  # Fuente del dato (XBRL, calculated, 10-K, etc.)
    currency_code: str = "USD"  # Código de moneda
    gaap_vs_nongaap: str = "GAAP"  # GAAP o Non-GAAP


class FinancialsReport(BaseModel):
    """Reporte financiero completo de EDGAR XBRL (100% determinista)."""
    ticker: str  # Ticker de la empresa
    cik: str  # CIK de la SEC (10 dígitos con ceros)
    as_of_date: date  # Fecha de corte del reporte

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
    source_filing: str  # Filing de origen (ej: "10-K FY2025")
    citations: list[Citation] = Field(default_factory=list)
