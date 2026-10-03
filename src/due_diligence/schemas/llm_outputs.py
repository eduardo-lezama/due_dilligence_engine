"""
Schemas de SALIDA del LLM (contrato con el proveedor, ADR-032).

Distintos de schemas/reports.py: aquí todo es required y sin defaults (requisito de
OpenAI strict json_schema; Anthropic lo acepta igual). Los Optional se expresan como
`X | None` requeridos. Los agentes convierten estos objetos a reports de dominio tras
validar citas y aplicar reglas de negocio (clamps, gating de campos numéricos).

Por qué dos contratos: el report de dominio puede evolucionar (campos deterministas,
defaults para checkpoints antiguos) sin romper el schema que se envía al proveedor, y
el schema del proveedor puede ser estricto sin contaminar el dominio con restricciones
que solo existen para la generación.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from due_diligence.schemas.base import Citation


class _Strict(BaseModel):
    """Base común: extra='forbid' genera additionalProperties:false en el JSON schema."""
    model_config = ConfigDict(extra="forbid")


class CitationOut(_Strict):
    """Cita tal como la emite el LLM; aún NO verificada (verified=False al convertir)."""
    claim: str
    source_url: str
    excerpt: str
    confidence: float          # sin ge/le en el schema (strict no las admite bien): se clampa en to_citation()
    gaap_vs_nongaap_flag: str | None

    def to_citation(self) -> Citation:
        """Convierte a Citation de dominio (verified=False) acotando confidence a [0, 1]."""
        return Citation(claim=self.claim, source_url=self.source_url, excerpt=self.excerpt,
                        confidence=max(0.0, min(1.0, self.confidence)),
                        gaap_vs_nongaap_flag=self.gaap_vs_nongaap_flag)


class FilingsLLMOutput(_Strict):
    """Salida del Filings agent (10-K Items 1, 1A y 7)."""
    business_description: str | None
    risk_factors_summary: str | None
    mda_highlights: str | None
    backlog_usd: float | None
    backlog_citation: CitationOut | None       # obligatoria para mostrar backlog (T3b)
    revenue_guidance: str | None
    geographic_revenue_breakdown: str | None
    key_regulatory_events: str | None
    governance_red_flags: str | None
    citations: list[CitationOut]


class ManagementLLMOutput(_Strict):
    """Salida del Management agent.

    form4_transactions_12m NO está aquí: se calcula de forma determinista a partir de
    los Form 4 descargados (T3b). Los números (ownership, tenure) solo llegan al report
    si su cita se verifica contra fuentes primarias.
    """
    insider_ownership_pct: float | None
    insider_ownership_citation: CitationOut | None
    founder_ceo: bool | None
    ceo_tenure_years: float | None
    ceo_tenure_citation: CitationOut | None
    insider_transaction_pattern: str | None
    compensation_structure_notes: str | None
    capital_allocation_summary: str | None
    citations: list[CitationOut]


class CompetitiveLLMOutput(_Strict):
    """Salida del Competitive agent. El Literal sustituye la validación manual del moat."""
    moat_classification: Literal["wide", "narrow", "none"]
    moat_sources: list[str]
    citations: list[CitationOut]


class CriticLLMOutput(_Strict):
    """Salida del Critic (red team)."""
    weaknesses: list[str]
    contra_thesis: str | None
    pre_mortem_scenario: str | None


class ValueExpertLLMOutput(_Strict):
    """Salida del Value Expert: 9 secciones del checklist + VERDICT (ADR-009)."""
    business_understanding: str | None
    moat_analysis: str | None
    management_assessment: str | None
    financial_quality: str | None
    growth_reinvestment: str | None
    valuation_section: str | None
    risks: str | None
    psychological_lens: str | None
    decision_frame: str | None
    verdict: Literal["BUY", "HOLD", "SELL"]
    verdict_confidence: float      # clamp [0,1] en value_expert._to_report
    suggested_sizing_pct: float    # clamp [0,20] y ≤ confidence×10 en _to_report
    verdict_rationale: str


class ThesisBreakCheck(_Strict):
    """Salida del validador de thesis-break (ADR-031)."""
    is_currently_true: bool
    reason: str
