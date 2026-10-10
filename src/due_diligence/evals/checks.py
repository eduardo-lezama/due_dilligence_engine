"""
checks — comprobaciones deterministas del harness (H2-1, capa A): una regla del prompt = un check.

Entrada: la salida CRUDA del LLM (`ValueExpertLLMOutput`), el informe final (`ValueExpertReport`,
tras las reparaciones del código), el texto de contexto que vio el modelo y el estado. Se puntúa lo
crudo porque el código ya repara algunas reglas (avisos de reglas 23/24, clamps de confianza y
sizing): medir el informe final mediría la reparación, no al modelo. Cuánto repara el código se
cuenta aparte (`repairs`). Un check que no aplica devuelve `applicable=False`: nunca suma acierto.
"""
from __future__ import annotations

import bisect
import math
import re
from functools import lru_cache
from typing import Callable, Optional

from pydantic import BaseModel

from due_diligence.graph.state import AnalysisState
from due_diligence.schemas.llm_outputs import ValueExpertLLMOutput
from due_diligence.schemas.reports import ValueExpertReport
from due_diligence.validation.citations import normalize_text


class CheckResult(BaseModel):
    """Resultado de un check sobre una repetición."""
    name: str
    applicable: bool
    passed: Optional[bool] = None      # None si no aplica o si el check es continuo
    value: Optional[float] = None      # checks continuos (cifras sin respaldo, reparaciones)
    detail: str = ""


_SECTIONS = ("business_understanding", "moat_analysis", "management_assessment", "financial_quality",
             "growth_reinvestment", "valuation_section", "risks", "psychological_lens", "decision_frame")
_MIN_SECTION_CHARS = 40

# --- Cifras: signo, valor y unidad. El lookbehind evita "fy2025", "q1", "§6" y decimales sueltos. ---
_NUM_RE = re.compile(
    r"(?<![\w§.])(?P<sign>[-−])?(?P<cur>\$)?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|pp|basis points|bps|billion|million|thousand|bn|b|m|k|x)?(?![\w])")
_SCALE = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}
_PCT_UNITS = ("%", "pp", "bps", "basis points")
_BPS_UNITS = ("bps", "basis points")      # 340 bps = 3,4 pp (calibración con informes reales)


class _Num(BaseModel):
    """Una cifra: valor con signo y escala, unidad, token original, tolerancia de redondeo y si es $."""
    value: float
    unit: str
    token: str
    tol: float
    currency: bool


def _parse_numbers(text: str, *, skip_trivial: bool) -> list[_Num]:
    """Cifras del texto normalizado.

    La tolerancia sale de la PRECISIÓN ESCRITA: "36%" admite 35,5–36,5 y "8.1%" 8,05–8,15; "$2.9B"
    admite ±0,05 B. Una tolerancia relativa fija aceptaba casi cualquier cifra inventada (medido:
    100 % de porcentajes aleatorios "con respaldo" en el contexto de CALM).
    """
    out = []
    for m in _NUM_RE.finditer(normalize_text(text)):
        digits = m.group("num").replace(",", "")
        raw = float(digits)
        unit = (m.group("unit") or "").lower()
        unit = "%" if unit == "percent" else unit     # "16 percent" = "16%" (calibración con gpt-6.1-sol)
        if skip_trivial and not unit and not m.group("cur") and (
                raw <= 10 or (1900 <= raw <= 2099 and raw.is_integer())):
            continue          # contadores ("3 of 4"), numeración y años no son cifras financieras
        scale = 0.01 if unit in _BPS_UNITS else _SCALE.get(unit, 1.0)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        out.append(_Num(value=raw * scale * (-1.0 if m.group("sign") else 1.0), unit=unit,
                        token=m.group(0).strip().replace("−", "-"),
                        tol=0.5 * 10 ** -decimals * scale + 1e-9, currency=bool(m.group("cur"))))
    return out


@lru_cache(maxsize=64)
def _context_index(context: str) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Valores del contexto ordenados: absolutos, porcentajes y porcentajes DERIVABLES.

    Derivables = solo diferencias entre porcentajes, y solo se usan para cifras en puntos o bps
    ("el margen cayó 340 bps"). Cocientes entre importes NO: el contexto ya trae escritos el upside y
    los "% vs price", y 180 000 cocientes cubrían todo el eje (medido: 63-81 % de porcentajes
    aleatorios aceptados). Ver tests/test_evals_checks.py::test_ungrounded_is_not_trivially_permissive.
    """
    nums = _parse_numbers(context, skip_trivial=False)
    absolute = sorted(n.value for n in nums if n.unit not in _PCT_UNITS)
    pcts = sorted(n.value for n in nums if n.unit in _PCT_UNITS)
    diffs = {abs(a - b) for a in pcts[:300] for b in pcts[:300] if a != b}
    return tuple(absolute), tuple(pcts), tuple(sorted(diffs))


def _near(sorted_values: tuple[float, ...], v: float, tol: float) -> bool:
    """¿Hay algún valor en [v − tol, v + tol]? (búsqueda binaria)."""
    i = bisect.bisect_left(sorted_values, v - tol)
    return i < len(sorted_values) and sorted_values[i] <= v + tol


def ungrounded_numbers(text: str, context: str) -> list[str]:
    """Cifras materiales del texto sin respaldo en el contexto, dentro de su precisión escrita."""
    absolute, pcts, derived = _context_index(context)
    missing: list[str] = []
    for n in _parse_numbers(text, skip_trivial=True):
        if n.unit in ("pp", *_BPS_UNITS):
            pool = (pcts, derived)            # "cayó 3,4 pp / 340 bps": diferencia de dos porcentajes
        elif n.unit == "%":
            pool = (pcts,)
        else:
            pool = (absolute,)
        if not any(_near(values, n.value, n.tol) for values in pool) and n.token not in missing:
            missing.append(n.token)
    return missing


def _text(raw: ValueExpertLLMOutput, *fields: str) -> str:
    return "\n".join(getattr(raw, f) or "" for f in (fields or _SECTIONS))


def _na(name: str) -> CheckResult:
    return CheckResult(name=name, applicable=False)


def _valuation(state: AnalysisState):
    return state.get("valuation") if isinstance(state, dict) else None


# ----------------------------------------------------------------------------------------------
def check_sections_present(raw, report, context, state) -> CheckResult:
    """Las 9 secciones del checklist con contenido real (≥ 40 caracteres)."""
    short = [s for s in _SECTIONS if len((getattr(raw, s) or "").strip()) < _MIN_SECTION_CHARS]
    return CheckResult(name="sections_present", applicable=True, passed=not short, detail=", ".join(short))


def check_ungrounded_numbers(raw, report, context, state) -> CheckResult:
    """Regla 1 / ADR-005: cifras sin respaldo en el contexto. Continuo: se compara su media."""
    # §9 (decision_frame) se excluye: propone criterios de salida con cifras nuevas a propósito
    # ("exit if revenue falls below $120 million") — calibración con informes reales de H1.
    sections = [s for s in _SECTIONS if s != "decision_frame"]
    missing = ungrounded_numbers(_text(raw, *sections) + "\n" + raw.verdict_rationale, context)
    return CheckResult(name="ungrounded_numbers", applicable=True, value=float(len(missing)),
                       detail=", ".join(missing[:15]))


_CONV_RE = re.compile(r"Convergence: (\d+) cheap, (\d+) fair, (\d+) expensive")


def check_rule21_count(raw, report, context, state) -> CheckResult:
    """Regla 21(a): §6 declara la convergencia como "N of 4"."""
    m = _CONV_RE.search(context)
    if "## VALUATION METHODS CONVERGENCE" not in context or (m and sum(map(int, m.groups())) == 0):
        return _na("rule21_count")      # sin bloque, o ningún método calculable (todo n/a): nada que contar
    # Dos formas válidas de declarar el recuento: "3 of 4 methods…" o el formato del propio contexto
    # "0 cheap, 0 fair, 3 expensive out of 4" (calibrado con la baseline real: era un falso positivo).
    ok = re.search(r"\b[0-4] of 4\b|\b[0-4] cheap, [0-4] fair,? (?:and )?[0-4] expensive out of 4\b",
                   raw.valuation_section or "", re.IGNORECASE) is not None
    return CheckResult(name="rule21_count", applicable=True, passed=ok, detail="" if ok else "no 'N of 4' in §6")


def check_rule21_divergence_no_buy(raw, report, context, state) -> CheckResult:
    """Regla 21(c): con métodos divergentes (≥1 cheap y ≥1 expensive) no hay BUY."""
    m = _CONV_RE.search(context)
    if not m or int(m.group(1)) == 0 or int(m.group(3)) == 0:
        return _na("rule21_divergence_no_buy")
    return CheckResult(name="rule21_divergence_no_buy", applicable=True, passed=raw.verdict != "BUY",
                       detail=m.group(0))


def check_buy_floor(raw, report, context, state) -> CheckResult:
    """Un BUY exige más de un 20 % de upside de DCF (umbral 'cheap' de ADR-030/033).

    Aplica según el CONTEXTO (upside conocido y ≤ 20 %), no según el veredicto: si solo aplicara cuando el
    modelo dice BUY, una baseline casi toda HOLD nunca lo activaría y la puerta no vería un candidato que
    pasa a BUY con poco upside (revisión final H2-1).
    """
    val = _valuation(state)
    upside = getattr(val, "dcf_upside_pct", None)
    if upside is None or upside > 20:
        return _na("buy_floor")
    return CheckResult(name="buy_floor", applicable=True, passed=raw.verdict != "BUY", detail=f"DCF upside {upside}%")


# Frases prohibidas copiadas del prompt (regla 22). Un test guard comprueba que siguen en él.
_RULE22_FORBIDDEN = {
    "RISING": ("declining roic", "eroding roic", "deteriorating roic", "falling roic", "moat is eroding"),
    "FALLING": ("improving", "recovering", "strengthening"),
}
_ROIC_LABEL_RE = re.compile(r"ROIC trend interpretation \(use in §5\): (RISING|FALLING)")


def check_rule22_roic_language(raw, report, context, state) -> CheckResult:
    """Regla 22: el lenguaje de §4/§5 no contradice la etiqueta determinista de tendencia del ROIC."""
    m = _ROIC_LABEL_RE.search(context)
    if not m:
        return _na("rule22_roic_language")      # sin etiqueta, STABLE o n/a: nada que contradecir
    label = m.group(1)
    text = normalize_text(_text(raw, "financial_quality", "growth_reinvestment"))
    if label == "RISING":
        hits = [p for p in _RULE22_FORBIDDEN["RISING"] if p in text]
    else:   # FALLING: palabras de mejora en una frase que habla del ROIC
        # La palabra de mejora debe referirse al ROIC: se trocea también por contraste ("falling ROIC
        # versus improving operating performance" habla de otra métrica; calibración con gpt-6.1-sol).
        pieces = re.split(r"(?<=[.;!?])\s+|\b(?:versus|vs\.?|but|while|whereas|although|though|despite|however)\b", text)
        sentences = [s for s in pieces if s and "roic" in s]
        hits = [w for w in _RULE22_FORBIDDEN["FALLING"] for s in sentences if re.search(rf"\b{w}\b", s)]
    return CheckResult(name="rule22_roic_language", applicable=True, passed=not hits,
                       detail=f"{label}: {', '.join(sorted(set(hits)))}" if hits else label)


_HYP_HEADER = "## INVESTOR'S OWN HYPOTHESES"
_HYP_LABEL_RE = re.compile(r"\b(SUPPORTED|CONTRADICTED|UNTESTABLE)\b")


def check_rule24_hypotheses(raw, report, context, state) -> CheckResult:
    """Regla 24: §8 etiqueta cada hipótesis del inversor (sobre lo CRUDO: el aviso del código
    contiene las tres etiquetas y haría pasar el check siempre)."""
    if _HYP_HEADER not in context:
        return _na("rule24_hypotheses")
    block = context.split(_HYP_HEADER, 1)[1]
    n_hyp = sum(1 for line in block.splitlines() if line.startswith("- Q:"))
    n_labels = len(_HYP_LABEL_RE.findall(raw.psychological_lens or ""))
    return CheckResult(name="rule24_hypotheses", applicable=n_hyp > 0, passed=n_labels >= n_hyp if n_hyp else None,
                       detail=f"{n_labels} labels for {n_hyp} hypotheses")


# Nombrar otro veredicto para descartarlo ("needed for BUY", "argues against SELL", "withhold a BUY",
# "rather than a SELL") no es concluirlo (calibración con gpt-6.1-sol, que razona sobre lo que descarta).
# Se descartan las menciones en una CLÁUSULA con negación o condición ("do not yet establish a definitive
# SELL case", "insufficiently resolved for BUY"): con un modelo que razona sobre lo que descarta, una
# ventana de pocas palabras tras la negación se quedaba corta (calibración con el run completo de sol).
_NEGATION_CUE_RE = re.compile(
    r"\b(?:not|no|nor|never|against|without|insufficient\w*|rather than|instead of|withhold\w*|prevent\w*"
    r"|premature\w*|before|until|needed for|required for)\b", re.IGNORECASE)


def _discard_negated_verdicts(text: str) -> str:
    """Quita las cláusulas (separadas por . ; :) que contienen una negación o condición."""
    clauses = re.split(r"[.;:]", text)
    return " ".join(c for c in clauses if not _NEGATION_CUE_RE.search(c))


def check_rationale_matches_verdict(raw, report, context, state) -> CheckResult:
    """La justificación no concluye otro veredicto (falla si nombra veredictos y ninguno es el propio)."""
    text = _discard_negated_verdicts(raw.verdict_rationale or "")
    named = set(re.findall(r"\b(BUY|HOLD|WATCHLIST|PASS|SELL)\b", text))     # H1 + H2 (ADR-041)
    ok = not named or raw.verdict in named
    return CheckResult(name="rationale_matches_verdict", applicable=True, passed=ok,
                       detail="" if ok else f"verdict {raw.verdict}, rationale names {sorted(named)}")


def check_repairs(raw, report, context, state) -> CheckResult:
    """Informativo: cuántas cosas corrigió el código tras el modelo (avisos ⚠️ añadidos y clamps)."""
    added = sum((getattr(report, s) or "").count("⚠️") - (getattr(raw, s) or "").count("⚠️") for s in _SECTIONS)
    if getattr(raw, "verdict_confidence", None) is not None:      # H1: el código recortaba confianza y sizing
        clamps = int(not math.isclose(report.verdict_confidence or 0.0, raw.verdict_confidence, abs_tol=1e-9))
        clamps += int(not math.isclose(report.suggested_sizing_pct or 0.0, raw.suggested_sizing_pct, abs_tol=1e-9))
    else:                                                         # H2: el código rebajaba el veredicto
        d = getattr(report, "decision", None)
        clamps = int(d is not None and d.verdict != d.verdict_raw)
    return CheckResult(name="repairs", applicable=True, value=float(max(added, 0) + clamps))


def check_buy_mos_floor(raw, report, context, state) -> CheckResult:
    """H2 (ADR-041): el modelo no elige BUY por encima del precio de entrada que exige el margen de seguridad.

    Aplica siempre que haya precio de entrada (no solo cuando el modelo dice BUY), para que la puerta vea
    un candidato que empieza a comprar sin margen. El código lo rebaja a WATCHLIST, pero se mide lo crudo.
    """
    d = getattr(report, "decision", None)
    if d is None or d.entry_price is None or d.price is None:
        return _na("buy_mos_floor")
    ok = not (raw.verdict == "BUY" and d.price > d.entry_price)
    return CheckResult(name="buy_mos_floor", applicable=True, passed=ok,
                       detail=f"price {d.price:.2f} vs entry {d.entry_price:.2f} ({d.business_class})")


def check_business_class_consistent(raw, report, context, state) -> CheckResult:
    """Diagnóstico: el tipo de negocio del modelo no tuvo que endurecerse (cíclico o sin moat)."""
    d = getattr(report, "decision", None)
    if d is None:
        return _na("business_class_consistent")
    ok = d.business_class == d.business_class_llm
    return CheckResult(name="business_class_consistent", applicable=True, passed=ok,
                       detail="" if ok else f"{d.business_class_llm} -> {d.business_class}")


ALL_CHECKS: tuple[Callable[..., CheckResult], ...] = (
    check_sections_present, check_ungrounded_numbers, check_rule21_count, check_rule21_divergence_no_buy,
    check_buy_floor, check_rule22_roic_language, check_rule24_hypotheses, check_rationale_matches_verdict,
    check_repairs, check_buy_mos_floor, check_business_class_consistent,
)
CHECK_NAMES: tuple[str, ...] = (
    "sections_present", "ungrounded_numbers", "rule21_count", "rule21_divergence_no_buy", "buy_floor",
    "rule22_roic_language", "rule24_hypotheses", "rationale_matches_verdict", "repairs",
    "buy_mos_floor", "business_class_consistent",
)


def run_checks(raw: ValueExpertLLMOutput, report: ValueExpertReport, context: str,
               state: AnalysisState) -> list[CheckResult]:
    """Todos los checks, en el orden de CHECK_NAMES."""
    return [check(raw, report, context, state) for check in ALL_CHECKS]
