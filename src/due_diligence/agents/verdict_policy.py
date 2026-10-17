"""
verdict_policy — parte numérica del veredicto del Value Expert, en código (H2-2 ADR-041; bloque B ADR-044).

Por qué existe: en H1 el LLM elegía BUY/HOLD/SELL y además inventaba la confianza y el tamaño de
posición; casi todo salía HOLD con confianza 0,6–0,8, desacoplado de la valoración. Ahora el modelo
elige la etiqueta (BUY/WATCHLIST/PASS/SELL) y el tipo de negocio, y aquí se aplican los números.

Bloque B (ADR-044): la decisión se toma por RENTABILIDAD frente a un listón explícito. La valoración
(tool lane) calcula la rentabilidad anual que da el precio de hoy en el caso base y en el pesimista y
el precio de entrada (base ≥ listón y pesimista ≥ Rf). Antes el precio de entrada era
"min(DCF, EPV) × (1 − 30/50/60 %)": un margen de seguridad apilado sobre estimaciones ya conservadoras
que dejaba el BUY casi inalcanzable (0 de 30 empresas, ni con el precio un 40 % más bajo).
Regla de oro: el código solo hace el veredicto MÁS prudente, nunca lo sube. Sin LLM, sin I/O.
"""
from __future__ import annotations

from typing import Optional

from due_diligence.schemas.reports import VerdictDecision

_CLASSES = ("quality_compounder", "average", "cyclical_or_low_quality")

# Fiabilidad del DCF: sin ella la confianza baja (ya no cambia el ancla, que desapareció en ADR-044).
_MAX_TERMINAL_SHARE = 0.75
_UNRELIABLE_GROWTH_SOURCES = ("default_7pct",)

# Confianza: base + componentes deterministas + ajuste del LLM acotado. Pesos iniciales sin calibrar
# contra resultados (lo hará el seguimiento prospectivo, MCE-36); todos visibles en el informe.
_CONF_BASE = 0.5
_CONF_PER_METHOD = 0.05
_CONF_DCF_UNRELIABLE = -0.10
_CONF_EVIDENCE = 0.05
_EVIDENCE_MIN_CITATIONS = 10
_LLM_ADJUSTMENT_CAP = 0.10

# Tamaño (% de cartera): solo BUY. §9.2 del checklist: máximo 10 %.
_SIZE_BASE = 5.0
_SIZE_STEP = 2.5
_SIZE_CAP = 10.0


def effective_business_class(llm_class: str, *, moat: Optional[str], is_cyclical: Optional[bool]) -> tuple[str, Optional[str]]:
    """Endurece la clase del modelo: cíclico → la más exigente; sin moat → no puede ser compounder.

    Desde ADR-044 la clase es descriptiva (no fija el margen exigido), pero el informe no debe llamar
    "compounder" a un negocio sin moat confirmado ni "normal" a uno cíclico. Devuelve (clase, nota).
    """
    cls = llm_class if llm_class in _CLASSES else "average"
    if is_cyclical and cls != "cyclical_or_low_quality":
        return "cyclical_or_low_quality", f"{llm_class} → cyclical_or_low_quality (cyclicality detector)"
    if cls == "quality_compounder" and moat not in ("wide", "narrow"):
        return "average", f"{llm_class} → average (no confirmed moat: {moat or 'unknown'})"
    return cls, None


def dcf_is_reliable(*, terminal_value_share: Optional[float], growth_source: Optional[str]) -> bool:
    """El DCF es fiable si no depende sobre todo del valor terminal ni del crecimiento por defecto."""
    if terminal_value_share is not None and terminal_value_share > _MAX_TERMINAL_SHARE:
        return False
    return growth_source not in _UNRELIABLE_GROWTH_SOURCES


def reconcile_verdict(raw: str, *, price: Optional[float], entry: Optional[float]) -> tuple[str, Optional[str]]:
    """Solo hacia la prudencia: un BUY por encima del precio de entrada (o sin él) baja a WATCHLIST."""
    if raw != "BUY":
        return raw, None
    if entry is None or price is None:
        return "WATCHLIST", "⚠️ BUY downgraded to WATCHLIST: no entry price can be computed."
    if price > entry:
        return "WATCHLIST", (f"⚠️ BUY downgraded to WATCHLIST: price ${price:,.2f} is above the entry price "
                             f"${entry:,.2f} (the price at which the expected return meets the hurdle).")
    return "BUY", None


def composite_confidence(verdict: str, *, cheap: int, expensive: int, dcf_reliable: bool,
                         verified_citations: int, llm_adjustment: float) -> tuple[float, dict[str, float]]:
    """Confianza = base + acuerdo de los métodos con la dirección del veredicto + calidad de datos/evidencia
    + ajuste del LLM (±0,1). WATCHLIST/PASS no tienen dirección de valoración: sin término de métodos."""
    parts: dict[str, float] = {"base": _CONF_BASE}
    if verdict == "BUY":
        parts["methods_agreement"] = round((cheap - expensive) * _CONF_PER_METHOD, 4)
    elif verdict == "SELL":
        parts["methods_agreement"] = round((expensive - cheap) * _CONF_PER_METHOD, 4)
    else:
        parts["methods_agreement"] = 0.0
    if not dcf_reliable:
        parts["dcf_unreliable"] = _CONF_DCF_UNRELIABLE
    if verified_citations >= _EVIDENCE_MIN_CITATIONS:
        parts["evidence"] = _CONF_EVIDENCE
    parts["llm_adjustment"] = max(-_LLM_ADJUSTMENT_CAP, min(_LLM_ADJUSTMENT_CAP, llm_adjustment))
    return max(0.0, min(1.0, round(sum(parts.values()), 4))), parts


def position_size(verdict: str, *, bear_return: Optional[float], hurdle: Optional[float], price: Optional[float],
                  epv_ps: Optional[float]) -> tuple[float, dict[str, float]]:
    """Tamaño por riesgo de pérdida, no por confianza (ADR-044): 0 si no es BUY; base 5 %; +2,5 % si
    incluso el caso pesimista rinde el listón; −2,5 % si el precio exige crecimiento (por encima del
    EPV, Greenwald); máximo 10 % (§9.2)."""
    if verdict != "BUY":
        return 0.0, {"not_buy": 0.0}
    parts = {"base": _SIZE_BASE}
    if bear_return is not None and hurdle is not None and bear_return >= hurdle:
        parts["bear_case_meets_hurdle"] = _SIZE_STEP
    if price is not None and epv_ps is not None and price > epv_ps:
        parts["price_above_epv"] = -_SIZE_STEP
    return max(0.0, min(_SIZE_CAP, sum(parts.values()))), parts


def _value(mv) -> Optional[float]:
    return mv.value if mv is not None else None


def _caveats(valuation, class_note: Optional[str], downgrade: Optional[str]) -> list[str]:
    """Avisos que el código añade a la decisión: lo que cambió y lo que no se pudo comprobar."""
    out = [c for c in (class_note and f"ℹ️ Business class hardened: {class_note}.", downgrade) if c]
    if valuation is None:
        return out
    if valuation.entry_basis and "downside not tested" in valuation.entry_basis:
        out.append("⚠️ No bear scenario (fewer than 3 years of FCFF margins or NOPAT base): the entry price "
                   "only tests the base case.")
    cyc = getattr(valuation, "cyclicality", None)
    if cyc is not None and not cyc.assessed:
        out.append(f"⚠️ Cyclicality not assessed ({cyc.years} years of operating margin): a cyclical peak "
                   f"would not be normalized.")
    return out


def decide(raw_verdict: str, llm_class: str, state, *, llm_adjustment: float,
           verified_citations: int) -> VerdictDecision:
    """Decisión completa a partir del estado del grafo (valoración, mercado, competencia)."""
    valuation = state.get("valuation")
    market = state.get("market")
    competitive = state.get("competitive")
    val_ok = valuation is not None and not getattr(valuation, "stub", True)
    moat = competitive.moat_classification if competitive is not None and not competitive.stub else None
    cyclicality = getattr(valuation, "cyclicality", None) if val_ok else None
    business_class, class_note = effective_business_class(
        llm_class, moat=moat, is_cyclical=getattr(cyclicality, "is_cyclical", None))

    epv_ps = _value(valuation.epv_per_share) if val_ok else None
    reliable = val_ok and dcf_is_reliable(terminal_value_share=valuation.terminal_value_share,
                                          growth_source=valuation.near_term_growth_source)
    # Un solo precio para todo (el del mercado, el mismo que guarda el seguimiento prospectivo).
    price = _value(market.price_current) if market is not None else None
    entry = valuation.entry_price if val_ok else None
    bear = next((sc.implied_return for sc in valuation.dcf_scenarios if sc.name == "bear"), None) if val_ok else None
    hurdle = valuation.hurdle_rate if val_ok else None

    verdict, downgrade = reconcile_verdict(raw_verdict, price=price, entry=entry)
    methods = valuation.valuation_methods_summary if val_ok else []
    cheap = sum(1 for m in methods if m.get("classification") == "cheap")
    expensive = sum(1 for m in methods if m.get("classification") == "expensive")
    confidence, conf_parts = composite_confidence(verdict, cheap=cheap, expensive=expensive, dcf_reliable=reliable,
                                                  verified_citations=verified_citations, llm_adjustment=llm_adjustment)
    size, size_parts = position_size(verdict, bear_return=bear, hurdle=hurdle, price=price, epv_ps=epv_ps)
    return VerdictDecision(
        verdict=verdict, verdict_raw=raw_verdict, business_class_llm=llm_class, business_class=business_class,
        hurdle_rate=hurdle, implied_return=valuation.implied_return if val_ok else None, implied_return_bear=bear,
        entry_price=entry, entry_basis=(valuation.entry_basis or "") if val_ok else "no valuation", price=price,
        confidence=confidence, sizing_pct=size, confidence_components=conf_parts, sizing_components=size_parts,
        caveats=_caveats(valuation if val_ok else None, class_note, downgrade),
    )
