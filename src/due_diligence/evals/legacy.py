"""
legacy — lectura de salidas crudas del Value Expert de la taxonomía H1 (BUY/HOLD/SELL).

Por qué existe: los runs guardados (baselines incluidas) son el registro histórico del harness y deben
poder repuntuarse aunque cambie el contrato del LLM. H2-2 (ADR-041) cambió las etiquetas y quitó la
confianza y el tamaño de la salida; este modelo congela el contrato H1 solo para leer runs antiguos.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict

from due_diligence.schemas.llm_outputs import ValueExpertLLMOutput


class ValueExpertLLMOutputH1(BaseModel):
    """Contrato H1 congelado (hasta 2026-10-10). No usar fuera de evals."""
    model_config = ConfigDict(extra="forbid")
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
    verdict_confidence: float
    suggested_sizing_pct: float
    verdict_rationale: str


AnyExpertOutput = Union[ValueExpertLLMOutput, ValueExpertLLMOutputH1]


def taxonomy_of(raw: dict) -> str:
    """'H2' si la salida trae el tipo de negocio (contrato nuevo); 'H1' si no."""
    return "H2" if "business_class" in raw else "H1"


def parse_raw(raw: dict) -> AnyExpertOutput:
    """Salida cruda guardada → modelo del contrato que la produjo."""
    return ValueExpertLLMOutput(**raw) if taxonomy_of(raw) == "H2" else ValueExpertLLMOutputH1(**raw)
