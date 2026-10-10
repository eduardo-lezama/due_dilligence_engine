"""
verdict_policy — parte numérica del veredicto del Value Expert, en código (H2-2, ADR-041).

Por qué existe: en H1 el LLM elegía BUY/HOLD/SELL y además inventaba la confianza y el tamaño de
posición; casi todo salía HOLD con confianza 0,6–0,8, desacoplado de la valoración. Ahora el modelo
elige la etiqueta (BUY/WATCHLIST/PASS/SELL) y el tipo de negocio, y aquí se calculan el ancla de valor,
el precio de entrada (margen de seguridad del §9.1 del checklist), la confianza y el tamaño.
Regla de oro: el código solo hace el veredicto MÁS prudente, nunca lo sube. Sin LLM, sin I/O.
"""
from __future__ import annotations

from typing import Optional

from due_diligence.schemas.reports import VerdictDecision

# §9.1 del checklist: margen de seguridad exigido según el tipo de negocio.
REQUIRED_MOS = {"quality_compounder": 0.30, "average": 0.50, "cyclical_or_low_quality": 0.60}
_STRICTNESS = ("quality_compounder", "average", "cyclical_or_low_quality")   # de menos a más exigente

# Fiabilidad del DCF para usarlo como ancla de un compounder (si no, mínimo de DCF y EPV).
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
_DEEP_MARGIN_FACTOR = 1.5      # margen real ≥ 1,5 × exigido → +2,5 %


def effective_business_class(llm_class: str, *, moat: Optional[str], is_cyclical: Optional[bool]) -> tuple[str, Optional[str]]:
    """Endurece la clase del modelo: cíclico → la más exigente; sin moat → no puede ser compounder.

    Evita que el modelo llame "calidad" a un negocio para desbloquear el margen del 30 % (sicofancia).
    Devuelve (clase, nota) con la nota si se endureció.
    """
    cls = llm_class if llm_class in REQUIRED_MOS else "average"
    if is_cyclical and cls != "cyclical_or_low_quality":
        return "cyclical_or_low_quality", f"{llm_class} → cyclical_or_low_quality (cyclicality detector)"
    if cls == "quality_compounder" and moat not in ("wide", "narrow"):
        return "average", f"{llm_class} → average (no confirmed moat: {moat or 'unknown'})"
    return cls, None


def dcf_is_reliable(*, terminal_value_share: Optional[float], growth_source: Optional[str]) -> bool:
    """El DCF es ancla aceptable si no depende sobre todo del valor terminal ni del crecimiento por defecto."""
    if terminal_value_share is not None and terminal_value_share > _MAX_TERMINAL_SHARE:
        return False
    return growth_source not in _UNRELIABLE_GROWTH_SOURCES


def intrinsic_anchor(business_class: str, *, dcf_ps: Optional[float], epv_ps: Optional[float],
                     dcf_reliable: bool) -> tuple[Optional[float], str]:
    """Valor por acción sobre el que se calcula el precio de entrada.

    Compounder con DCF fiable → DCF (paga crecimiento demostrable). Resto → mínimo de DCF y EPV positivos
    (Graham: no pagar crecimiento no demostrado). Sin valores positivos → None (no hay precio de compra).
    """
    dcf = dcf_ps if dcf_ps is not None and dcf_ps > 0 else None
    epv = epv_ps if epv_ps is not None and epv_ps > 0 else None
    if business_class == "quality_compounder" and dcf is not None:
        if dcf_reliable:
            return dcf, "DCF (quality compounder)"
        note = "min(DCF, EPV) — compounder but DCF unreliable"
    else:
        note = "min(DCF, EPV)"
    values = [v for v in (dcf, epv) if v is not None]
    if not values:
        return None, "no positive DCF or EPV"
    return min(values), note if len(values) == 2 else ("DCF" if dcf is not None else "EPV")


def entry_price(anchor: float, required_mos: float) -> float:
    """Precio máximo de compra = ancla × (1 − margen exigido)."""
    return anchor * (1 - required_mos)


def actual_mos(anchor: float, price: float) -> float:
    """Margen de seguridad al precio actual (negativo si cotiza por encima del ancla)."""
    return 1 - price / anchor


def reconcile_verdict(raw: str, *, price: Optional[float], entry: Optional[float]) -> tuple[str, Optional[str]]:
    """Solo hacia la prudencia: un BUY sin margen (o sin precio de entrada) baja a WATCHLIST."""
    if raw != "BUY":
        return raw, None
    if entry is None or price is None:
        return "WATCHLIST", "⚠️ BUY downgraded to WATCHLIST: no entry price can be computed (no positive DCF/EPV or price)."
    if price > entry:
        return "WATCHLIST", (f"⚠️ BUY downgraded to WATCHLIST: price ${price:,.2f} is above the entry price "
                             f"${entry:,.2f} required by the margin of safety.")
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


def position_size(verdict: str, *, actual_mos: Optional[float], required_mos: float, price: Optional[float],
                  epv_ps: Optional[float]) -> tuple[float, dict[str, float]]:
    """Tamaño por margen y riesgo, no por confianza: 0 si no es BUY; base 5 %, +2,5 % con margen profundo,
    −2,5 % si el precio exige crecimiento (por encima del EPV); máximo 10 %."""
    if verdict != "BUY":
        return 0.0, {"not_buy": 0.0}
    parts = {"base": _SIZE_BASE}
    if actual_mos is not None and actual_mos >= _DEEP_MARGIN_FACTOR * required_mos:
        parts["deep_margin"] = _SIZE_STEP
    if price is not None and epv_ps is not None and price > epv_ps:
        parts["price_above_epv"] = -_SIZE_STEP
    return max(0.0, min(_SIZE_CAP, sum(parts.values()))), parts


def _value(mv) -> Optional[float]:
    return mv.value if mv is not None else None


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
    required = REQUIRED_MOS[business_class]

    dcf_ps = _value(valuation.dcf_intrinsic_per_share) if val_ok else None
    epv_ps = _value(valuation.epv_per_share) if val_ok else None
    reliable = val_ok and dcf_is_reliable(terminal_value_share=valuation.terminal_value_share,
                                          growth_source=valuation.near_term_growth_source)
    anchor, anchor_source = intrinsic_anchor(business_class, dcf_ps=dcf_ps, epv_ps=epv_ps, dcf_reliable=reliable)
    # Un solo precio para todo (el del mercado, el mismo que guarda el seguimiento prospectivo).
    price = _value(market.price_current) if market is not None else None
    entry = entry_price(anchor, required) if anchor is not None else None
    margin = actual_mos(anchor, price) if anchor is not None and price else None

    verdict, downgrade = reconcile_verdict(raw_verdict, price=price, entry=entry)
    methods = valuation.valuation_methods_summary if val_ok else []
    cheap = sum(1 for m in methods if m.get("classification") == "cheap")
    expensive = sum(1 for m in methods if m.get("classification") == "expensive")
    confidence, conf_parts = composite_confidence(verdict, cheap=cheap, expensive=expensive, dcf_reliable=reliable,
                                         verified_citations=verified_citations, llm_adjustment=llm_adjustment)
    size, size_parts = position_size(verdict, actual_mos=margin, required_mos=required, price=price, epv_ps=epv_ps)
    return VerdictDecision(
        verdict=verdict, verdict_raw=raw_verdict, business_class_llm=llm_class, business_class=business_class,
        required_mos=required, anchor_value=anchor, anchor_source=anchor_source, entry_price=entry, price=price,
        actual_mos=margin, confidence=confidence, sizing_pct=size,
        confidence_components=conf_parts, sizing_components=size_parts,
        caveats=[c for c in (class_note and f"ℹ️ Business class hardened: {class_note}.", downgrade) if c],
    )
