"""
Schemas base — modelos Pydantic para datos fundamentales del sistema.

Qué hace este módulo:
    Define los modelos Pydantic base que se usan en todo el proyecto:
    - Citation: cita con excerpt de la fuente; verified=True solo tras validation/citations.py
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
    """Cita de un claim cualitativo con su excerpt de la fuente (invariante 2, ADR-005).

    Nace con verified=False. Solo validation/citations.py la marca verified=True cuando
    el excerpt aparece en el texto fuente que vio el LLM (substring normalizado o
    rapidfuzz.partial_ratio ≥ 90) y todos sus números están en la ventana alineada.
    Las no verificadas llevan confidence=0 y verification_note, y no entran en la síntesis.
    """
    claim: str  # Texto del claim que se cita
    source_url: str  # URL de la fuente (10-K, 10-Q, etc.)
    excerpt: str  # Fragmento exacto de la fuente que contiene el claim
    confidence: float = Field(..., ge=0.0, le=1.0)  # 0.0-1.0: confianza del match
    gaap_vs_nongaap_flag: Optional[str] = None  # "GAAP", "Non-GAAP", or None
    # H1 (ADR-005 implementado, ADR-032): una cita nace verified=False. Solo
    # validation/citations.py la marca verified=True tras encontrar el excerpt en el
    # texto fuente real. Las no verificadas llevan confidence=0 y el motivo aquí.
    verified: bool = False                   # True solo tras validation/citations.py
    verification_note: Optional[str] = None  # Motivo de rechazo cuando verified=False


class Claim(BaseModel):
    """Afirmación que puede estar verificada o no."""
    text: str  # Texto del claim
    citation: Optional[Citation] = None  # Si está verificado, la cita
    verified: bool = False  # True si se encontró substring-match


class MetricValue(BaseModel):
    """Valor numérico con metadata (periodo, fuente, unidad).

    Invariante: si existe un MetricValue, su `value` es siempre un float válido.
    Para representar "dato no disponible", el campo padre debe ser Optional[MetricValue]
    y se setea a None — NO se construye un MetricValue con value=None.
    Esto preserva el contrato del schema: cualquier consumer puede operar sobre
    `metric.value` sin null-checks.
    """
    value: float  # Valor numérico — siempre presente cuando existe el MetricValue
    unit: str = "USD"  # Unidad (USD, shares, etc.)
    period: str  # Periodo (FY2025, Q1 2025, etc.)
    source: str  # Fuente del dato (XBRL, calculated, 10-K, etc.)
    currency_code: str = "USD"  # Código de moneda
    gaap_vs_nongaap: str = "GAAP"  # GAAP o Non-GAAP


class ScoreResult(BaseModel):
    """Un score contable determinista (A2, ADR-043). value=None → no calculable; `reading` dice por qué."""
    name: str                     # "Piotroski F-score", "Altman Z''", ...
    value: Optional[float] = None
    period: str                   # "FY2025" o "FY2025 vs FY2024"
    reading: str                  # interpretación con el umbral publicado, o "n/a — missing: ..."
    components: dict[str, Optional[float]] = Field(default_factory=dict)  # señales / ratios usados


class AccountingScores(BaseModel):
    """Calidad contable y riesgo (Piotroski, Altman Z'', Beneish, Sloan). Calculado en código, sin LLM."""
    piotroski: ScoreResult
    altman_z2: ScoreResult
    beneish_m: ScoreResult
    sloan_accruals: ScoreResult


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

    # Capital returns (ADR-023) — central para value investing.
    # Las empresas que devuelven capital al accionista (BBW: ~100% del FCF, AAPL: ~80%)
    # son cualitativamente distintas a las que retienen para reinversión. El sistema
    # debe surfacearlo explícitamente en el contexto del Value Expert.
    buybacks: list[MetricValue] = Field(default_factory=list)  # PaymentsForRepurchaseOfCommonStock
    dividends: list[MetricValue] = Field(default_factory=list)  # PaymentsOfDividendsCommonStock

    # Balance sheet (point-in-time, most recent)
    total_assets: MetricValue
    total_debt: MetricValue  # LT + ST + leasing financiero, sin doble conteo (EC-80)
    cash_and_equivalents: MetricValue  # SOLO caja (desde H1); STI va aparte (EC-81)
    short_term_investments: Optional[MetricValue] = None  # NO incluido en cash_and_equivalents (desde H1)
    long_term_investments: Optional[MetricValue] = None   # activo no operativo — el bridge EV→equity lo suma
    equity_method_investments: Optional[MetricValue] = None  # participaciones: se suman en el bridge salvo flag (T8)
    data_quality_flags: list[str] = Field(default_factory=list)  # identidades contables rotas, fallbacks

    # Share data
    shares_outstanding: Optional[MetricValue] = None  # recuento puntual de portada (dei)

    # --- FCFF inputs (Bloque C) — series anuales, index 0 = latest, mismas etiquetas FY que revenue ---
    interest_expense: list[MetricValue] = Field(default_factory=list)  # magnitud (signo varía por filer)
    interest_income: list[MetricValue] = Field(default_factory=list)   # restado neto de impuestos del FCFF
    sbc: list[MetricValue] = Field(default_factory=list)                # ShareBasedCompensation (magnitud)
    depreciation_amortization: list[MetricValue] = Field(default_factory=list)  # magnitud
    diluted_shares: list[MetricValue] = Field(default_factory=list)     # MEDIA PONDERADA diluida, unit="shares"
    basic_shares_weighted: Optional[MetricValue] = None                 # media ponderada básica (latest)
    # --- Balance memo (point-in-time, latest) ---
    operating_lease_liabilities: Optional[MetricValue] = None  # MEMO: no en total_debt (ASC 842)
    operating_rou_asset: Optional[MetricValue] = None          # ROU operativo: se excluye del IC operativo (T9)
    minority_interest: Optional[MetricValue] = None
    preferred_stock: Optional[MetricValue] = None
    # --- Tipo impositivo canónico (revisión adversarial): expuestos para effective_tax_rate_multi ---
    income_tax: list[MetricValue] = Field(default_factory=list)
    pretax_income: list[MetricValue] = Field(default_factory=list)
    finance_lease_principal_payments: list[MetricValue] = Field(default_factory=list)  # capex equivalente (FCFF)

    # ROIC (Return on Invested Capital) — calculado de XBRL, no generado por LLM.
    # ROIC_t = NOPAT_t / media(IC_t, IC_{t-1}) con la tasa efectiva del año t (T9, EC-85).
    # source: "calculated-avg-ic" / "calculated-end-ic"; fixtures legacy sin balance_history →
    # "calculated-latest-ic" (IC del último año para todos, aproximación previa).
    roic_series: list[MetricValue] = Field(default_factory=list)  # ROIC como ratio (ej: 0.87 = 87%)
    incremental_roic: Optional[MetricValue] = None  # ΔNOPAT/ΔIC ≤3 años, period "FY2022→FY2025"; None = n/m
    # Series de balance por año (mismas etiquetas FY, index 0 = latest). Las consume suggest_wacc (T11).
    total_debt_series: list[MetricValue] = Field(default_factory=list)
    cash_series: list[MetricValue] = Field(default_factory=list)   # caja + STI de cada año
    equity_series: list[MetricValue] = Field(default_factory=list)
    invested_capital: Optional[MetricValue] = None   # IC más reciente en USD
    # Scores contables deterministas (A2, ADR-043). None en fixtures/reports anteriores al bloque A.
    accounting_scores: Optional[AccountingScores] = None
    stockholders_equity: Optional[MetricValue] = None  # Book equity más reciente en USD

    # Meta
    source_filing: str  # Filing de origen (ej: "10-K FY2025")
    citations: list[Citation] = Field(default_factory=list)
