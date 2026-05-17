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
    insider_ownership_pct: Optional[float] = None  # % total de acciones owned por insiders (directivos + board)
    founder_ceo: Optional[bool] = None             # True si el CEO fundó la empresa
    ceo_tenure_years: Optional[float] = None       # Años del CEO en el cargo
    form4_transactions_12m: Optional[int] = None   # Número de transacciones Form 4 en 12m
    insider_transaction_pattern: Optional[str] = None  # Patrón: quién compra/vende, volumen neto, señal
    compensation_structure_notes: Optional[str] = None  # Notas sobre estructura de compensación
    capital_allocation_summary: Optional[str] = None  # Resumen de allocation de capital
    citations: list[Citation] = Field(default_factory=list)


class FilingsRAGReport(BaseModel):
    """Reporte de análisis de filings SEC (10-K, 10-Q) usando RAG."""
    ticker: str
    stub: bool = True
    business_description: Optional[str] = None      # Descripción del negocio
    risk_factors_summary: Optional[str] = None      # Resumen de factores de riesgo
    mda_highlights: Optional[str] = None            # Highlights de Management Discussion & Analysis
    # --- Campos operativos clave (Phase 2 enhancement) ---
    backlog_usd: Optional[float] = None             # Backlog contratado en USD (e.g. 1.27e9)
    revenue_guidance: Optional[str] = None          # Guidance de management para próximo periodo
    geographic_revenue_breakdown: Optional[str] = None  # "China: 85%, US: 10%, Other: 5%"
    key_regulatory_events: Optional[str] = None     # Eventos regulatorios específicos con fecha y entidad
    citations: list[Citation] = Field(default_factory=list)


class MarketReport(BaseModel):
    """Reporte de datos de mercado (precio, market cap, noticias, eventos 8-K)."""
    ticker: str
    stub: bool = True
    price_current: Optional[MetricValue] = None  # Precio actual
    price_52w_high: Optional[MetricValue] = None  # Máximo 52 semanas
    price_52w_low: Optional[MetricValue] = None  # Mínimo 52 semanas
    market_cap: Optional[MetricValue] = None  # Capitalización de mercado
    recent_news_summary: Optional[str] = None  # Resumen de noticias recientes
    material_events: list[dict] = Field(default_factory=list)  # Eventos 8-K materiales (ADR-017)
    quarterly_earnings_text: Optional[str] = None  # Press release earnings más reciente (8-K 2.02, EC-44)
    quarterly_earnings_date: Optional[str] = None  # Fecha del filing del press release


class PeerROIC(BaseModel):
    """ROIC de un peer ticker para comparación tabular en el reporte. ADR-015."""
    ticker: str          # Ej: "MSFT"
    roic: float          # ROIC más reciente, ratio, ej: 0.22 (22%)
    period: str          # Periodo más reciente, ej: "FY2025"
    roic_trail: list[dict] = Field(default_factory=list)
    # Trail histórico (hasta 3 periodos, más reciente primero).
    # Cada entry: {"period": "FY2025", "roic": 0.221}
    # Permite detectar si el ROIC es estable, mejorando o cíclico.
    # Campo backward-compatible: código que solo lee roic/period sigue funcionando.


class PeerValuation(BaseModel):
    """
    Peer ticker with valuation multiples for relative comparison. MCE-18.

    Built in peers_node via PolygonAdapter (market_cap) + XBRL (EBIT, FCF, debt, cash).
    mf_rank is None until valuation_node computes it from the full subject+peer set.
    """
    ticker: str
    roic: float                              # Most recent ROIC ratio (e.g. 0.341 = 34.1%)
    period: str                              # Fiscal year label (e.g. "FY2025")
    ev_ebit: Optional[float] = None          # EV / EBIT (None if no market data or EBIT <= 0)
    fcf_yield_pct: Optional[float] = None    # FCF / market_cap * 100 (None if FCF <= 0 or no market data)
    mf_rank: Optional[int] = None            # Magic Formula rank (1 = best); set by valuation_node
    no_market_data: bool = False             # True if Polygon returned no market_cap for this peer


class QuarterlySnapshot(BaseModel):
    """
    Snapshot de métricas clave del 10-Q más reciente (EC-44/MCE-24).

    Se genera cuando el 10-K tiene más de 90 días de antigüedad — en ese caso,
    los datos anuales ya no son la fuente más actualizada. Viaja por el grafo
    como datos deterministas (XBRL de la SEC) para que el Value Expert pueda
    integrar cifras trimestrales recientes en §4, §5 y §7.

    Por qué solo 3 métricas:
        Revenue, EBIT y gross profit son suficientes para detectar deterioro o mejora
        de tendencia sin añadir complejidad de parsing. El objetivo es un snapshot
        rápido, no un estado financiero completo.
    """
    period: str                              # Ej: "Q1 2026"
    revenue: float                           # Revenue del quarter (siempre presente si el modelo existe)
    operating_income: Optional[float] = None  # EBIT trimestral (puede faltar en small caps con XBRL atípico)
    gross_profit: Optional[float] = None     # Gross profit trimestral
    filing_date: Optional[str] = None        # ISO date del 10-Q filing (si disponible)


class CompetitiveReport(BaseModel):
    """Reporte de análisis competitivo y moat."""
    ticker: str
    stub: bool = True
    moat_classification: Optional[str] = None  # "wide", "narrow", "none"
    moat_sources: list[str] = Field(default_factory=list)  # Fuentes del moat
    peer_roic_comparison: list[PeerROIC] = Field(default_factory=list)  # ROIC de peers (ADR-015)
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
    projection_years: int = 10  # Años de proyección DCF (stage1 + fade combinados)
    # 3-stage DCF: stage1_years a g1 constante, luego fade lineal a terminal (ADR-029).
    # None → auto-split 50/50 (e.g. 10y → 5+5, 5y → 2+3). Override en Checkpoint 2.
    stage1_years: Optional[int] = None   # None = projection_years // 2
    fade_years: Optional[int] = None     # None = projection_years - stage1_years
    revenue_growth_yr1_pct: Optional[float] = None  # Override de g1 (user sabe lo que hace)
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
    dcf_reliability_note: Optional[str] = None          # Warning cuando el DCF puede ser poco fiable
    near_term_growth_used_pct: Optional[float] = None   # Tasa de crecimiento FCF efectivamente usada en el DCF (e.g. 0.123 = 12.3%)
    near_term_growth_source: Optional[str] = None       # Procedencia: "user_override" | "fcf_cagr_Ny" | "fcf_cagr_Ny_rev_capped" | "revenue_cagr_fallback" | "default_7pct"
    # --- 3-stage DCF (ADR-029) ---
    stage1_years_used: Optional[int] = None             # Años con g1 constante
    fade_years_used: Optional[int] = None               # Años de fade lineal g1→terminal
    # --- EPV — Earnings Power Value (Greenwald) ---
    epv_enterprise: Optional[MetricValue] = None        # NOPAT_mediana / WACC (enterprise value sin deuda)
    epv_per_share: Optional[MetricValue] = None         # (EPV_enterprise - net_debt) / shares
    epv_upside_pct: Optional[float] = None              # (EPV_enterprise - market_cap) / market_cap × 100
    epv_source: Optional[str] = None                   # "ebit_median_5y" | "ebit_median_3y" | "ebit_latest"
    # --- WACC advisory (ADR-029) ---
    suggested_wacc_pct: Optional[float] = None          # CAPM simplificado (β=1, Rf hardcoded)
    suggested_wacc_breakdown: Optional[str] = None      # "Rf=4.2% + β=1.0×ERP=5.0% + size_premium=0.5%"
    # --- Convergence signaling (ADR-030) ---
    valuation_methods_summary: list[dict] = Field(default_factory=list)  # 4 métodos con clasificación cheap/fair/expensive


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
