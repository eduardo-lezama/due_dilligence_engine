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

from datetime import date
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
    # EC-66: señales de governance críticas extraídas explícitamente del 10-K Risk Factors / MD&A.
    # Separado de key_regulatory_events (que cubre acciones de gobierno externas como BIS/OFAC).
    # Cubre: SEC investigations activas, material weaknesses, class actions, restatements.
    governance_red_flags: Optional[str] = None      # SEC investigation, material weakness, class action, restatement — null si ninguno encontrado
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
    quarterly_eps_actuals: list[dict] = Field(default_factory=list)  # EPS actual/estimado/surprise por quarter (Finnhub)


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
    # ADR-033 §7: factor (1+WACC)^0.5 sobre el EV. Default False en H1 (comparabilidad con
    # los reports históricos); la fuente única del default es checkpoint.CHECKPOINT2_DEFAULTS.
    mid_year_convention: bool = False
    # Fecha a la que se valora: el EV se capitaliza (1+WACC)^(días/365) desde el cierre fiscal
    # (los flujos se descuentan al cierre, el precio es de hoy). None → hoy (lo fija el agente).
    valuation_date: Optional[date] = None


# --------------------------------------------------------------------------- #
# Plano enterprise de la valoración (ADR-033): FCFF → WACC → EV → bridge → por acción.
# Viven aquí (y no en valuation_math) para que ValuationReport los pueda referenciar
# y el archiver / Value Expert los rendericen sin importar código de cálculo.
# --------------------------------------------------------------------------- #

class FcffBreakdown(BaseModel):
    """Componentes del FCFF (ADR-033). Unlevered: flujo disponible para TODOS los proveedores de capital."""
    ocf: float
    after_tax_interest_addback: float      # + interest_expense × (1−t): OCF US GAAP ya lo restó
    after_tax_interest_income: float       # − interest_income × (1−t): evita contar dos veces la caja (bridge)
    capex: float
    sbc_charge: float                      # SBC tratado como coste en caja (Damodaran)
    finance_lease_principal: float = 0.0   # − principal de leasing financiero (capex equivalente, no está en capex)
    tax_rate: float
    fcff: float
    missing_inputs: list[str] = Field(default_factory=list)


class EquityBridge(BaseModel):
    """EV → equity común (ADR-033). Cada componente visible para auditoría en archiver/Value Expert."""
    enterprise_value: float
    total_debt: float
    minority_interest: float = 0.0
    preferred_stock: float = 0.0
    cash: float
    short_term_investments: float = 0.0
    long_term_investments: float = 0.0
    operating_lease_liabilities_memo: Optional[float] = None  # NO restado: ya está en OCF/EBIT (ASC 842)
    net_claims: float          # debt + minority + preferred − cash − STI − LTI
    equity_value: float        # enterprise_value − net_claims (puede ser negativo: se informa, no se recorta)
    components_missing: list[str] = Field(default_factory=list)


class WaccBreakdown(BaseModel):
    """WACC real = wE·Ke + wD·Kd·(1−t) (ADR-033). Fracciones, no %."""
    cost_of_equity: float
    cost_of_equity_source: str
    pre_tax_cost_of_debt: float
    cost_of_debt_source: str
    tax_rate: float
    after_tax_cost_of_debt: float
    equity_weight: float
    debt_weight: float
    wacc: float


class DcfComponents(BaseModel):
    """Desglose del DCF 3-stage: explícito vs terminal (ADR-033)."""
    pv_explicit: float
    pv_terminal: float
    enterprise_value: float
    terminal_value_share: Optional[float]   # pv_terminal / EV; None si EV == 0
    mid_year: bool
    # True si el flujo terminal se construyó desde NOPAT × (1 − g/RONIC) (Damodaran/Koller);
    # False = legacy FCFF_T × (1+g), que sobrevalora el TV cuando g > 0.
    terminal_reinvestment_modeled: bool = False


class ValuationReport(BaseModel):
    """Reporte de valuation (DCF, múltiplos, sensitivity)."""
    ticker: str
    stub: bool = True
    # Error tipado capturado (p.ej. "ValuationInputError: WACC must exceed g…"): el agente devuelve
    # stub=True + error en lugar de tumbar el grafo (ADR-033 §6). None = valoración normal.
    error: Optional[str] = None
    dcf_intrinsic_value: Optional[MetricValue] = None  # Equity value DCF (EV − net_claims, tras el bridge; ADR-033)
    dcf_intrinsic_per_share: Optional[MetricValue] = None  # Equity / share_count_used
    dcf_upside_pct: Optional[float] = None             # (per_share − price)/price × 100; None sin precio (EC-73)
    price_current: Optional[MetricValue] = None        # Precio de mercado actual (copia de MarketReport)
    reverse_dcf_implied_growth: Optional[float] = None  # g1 implícito: el DCF iguala el MARKET EV (EC-74)
    reverse_dcf_10y_equivalent: Optional[float] = None  # CAGR equivalente a 10 años del camino implícito (stage1+fade)
    ev_ebit_multiple: Optional[MetricValue] = None      # market EV / EBIT (Greenblatt — EBITDA no disponible en XBRL básico)
    ev_fcf_multiple: Optional[MetricValue] = None       # market EV / FCFF base
    pe_multiple: Optional[MetricValue] = None           # equity de mercado (price × shares) / net income
    earnings_yield_pct: Optional[float] = None          # EBIT/EV × 100 — componente Magic Formula
    magic_formula_rank: Optional[int] = None            # Rank según Magic Formula (requiere peer list — None en Phase 1)
    sensitivity_table: list[dict] = Field(default_factory=list)  # Tabla de sensibilidad WACC × growth
    assumptions: Optional[ValuationInput] = None        # Assumptions usadas en el DCF
    dcf_reliability_note: Optional[str] = None          # Warning cuando el DCF puede ser poco fiable
    fcf_quality_warning: Optional[str] = None           # EC-65: FCF inflado por advance payments / project timing
    near_term_growth_used_pct: Optional[float] = None   # Tasa de crecimiento FCF efectivamente usada en el DCF (e.g. 0.123 = 12.3%)
    near_term_growth_source: Optional[str] = None       # Procedencia: "user_override" | "fcf_cagr_Ny" | "fcf_cagr_Ny_rev_capped" | "revenue_cagr_fallback" | "default_7pct"
    # --- 3-stage DCF (ADR-029) ---
    stage1_years_used: Optional[int] = None             # Años con g1 constante
    fade_years_used: Optional[int] = None               # Años de fade lineal g1→terminal
    # --- EPV — Earnings Power Value (Greenwald) ---
    epv_enterprise: Optional[MetricValue] = None        # NOPAT normalizado / WACC (EV, mismo plano que el DCF)
    epv_per_share: Optional[MetricValue] = None         # (EPV − net_claims) / shares
    epv_upside_pct: Optional[float] = None              # (EPV/sh − price)/price × 100; None sin precio
    epv_source: Optional[str] = None                   # "ebit_median_5y" | "ebit_median_3y" | "ebit_latest"
    epv_growth_premium: Optional[float] = None          # 1 − EPV/sh ÷ price: lo que el precio paga por crecimiento (Greenwald)
    # --- WACC sugerido (ADR-029 → ADR-033) ---
    suggested_wacc_pct: Optional[float] = None          # WACC real (antes Ke): wE·Ke + wD·Kd·(1−t), en %
    suggested_wacc_breakdown: Optional[str] = None      # describe_wacc(): "Ke 9.2% (...) × 87% + Kd 4.8% (...) × (1−25%) × 13% = 8.5%"
    wacc_breakdown: Optional[WaccBreakdown] = None      # desglose estructurado del WACC sugerido
    # --- Convergence signaling (ADR-030) ---
    valuation_methods_summary: list[dict] = Field(default_factory=list)  # 4 métodos con clasificación cheap/fair/expensive
    # --- Plano enterprise (ADR-033): FCFF → EV → bridge → por acción ---
    valuation_basis: Optional[str] = None               # "FCFF discounted at WACC → EV → equity bridge"
    fcff_base: Optional[MetricValue] = None             # base del DCF (FCFF normalizado o último año); source = método
    fcff_breakdown: Optional[FcffBreakdown] = None      # componentes del FCFF del ÚLTIMO ejercicio
    dcf_base_source: Optional[str] = None               # "FCFF (...)" | "NOPAT proxy (EBIT × (1-t))"
    tax_rate_used: Optional[float] = None               # effective_tax_rate_multi (Σtax/Σpretax ≤3 años)
    enterprise_value_dcf: Optional[MetricValue] = None  # EV del DCF (capitalizado a la fecha de valoración)
    equity_bridge: Optional[EquityBridge] = None        # EV → equity con cada componente visible
    market_enterprise_value: Optional[MetricValue] = None  # price × shares + net_claims (base de múltiplos y reverse)
    share_count_used: Optional[float] = None            # acciones diluidas usadas en el per-share
    share_count_source: Optional[str] = None            # cómo se obtuvo share_count_used
    terminal_value_share: Optional[float] = None        # PV(TV)/EV del DCF; > 0.75 → dependencia del terminal
    mid_year_convention_used: Optional[bool] = None
    sensitivity_unit: Optional[str] = None              # "USD/share" (ADR-033); None = legacy (USD B de EV)


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
