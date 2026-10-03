"""
Validación post-hoc de citas contra el texto fuente REAL que vio el LLM (ADR-005, ADR-032).

Una cita es válida si su excerpt aparece en el corpus (substring normalizado o
rapidfuzz.partial_ratio ≥ 90) Y todos sus números aparecen en la MISMA ventana del
corpus. Las no válidas quedan con verified=False, confidence=0 y un motivo en
verification_note, y se excluyen de la síntesis (invariante 2). Comprobar que el CLAIM
se corresponde con el excerpt (p.ej. CEO vs CFO) es H2: aquí solo se demuestra que el
texto citado existe en la fuente.

Encaje: los agentes LLM (filings, management, competitive) construyen un corpus local
{etiqueta: texto} con exactamente lo que enviaron al LLM y llaman a estas funciones
dentro del nodo; nada del corpus entra en el estado checkpointeado.
"""
import math
import re
import unicodedata
from typing import Any, Optional

from rapidfuzz import fuzz

from due_diligence.schemas.base import Citation

FUZZY_THRESHOLD = 90.0   # partial_ratio: tolera re-tokenización y retoques de puntuación
MIN_EXCERPT_CHARS = 20   # con menos, partial_ratio encuentra coincidencias triviales

# Tipografía que los LLM "normalizan" al copiar (o que los 10-K usan): comillas curvas,
# guiones (hyphen, non-breaking hyphen, en/em dash, minus) y espacio duro → ASCII.
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                            "‐": "-", "‑": "-", "–": "-", "—": "-",
                            "−": "-", " ": " "})
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SCALED_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(billion|million|thousand|bn|mm|b|m|k)?\b")
_SCALES = (1.0, 1e3, 1e6, 1e9)


def normalize_text(text: str) -> str:
    """NFKC + comillas/guiones tipográficos a ASCII + minúsculas + espacios colapsados."""
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).lower()
    return re.sub(r"\s+", " ", text).strip()


def _norm_number(tok: str) -> str:
    """'10,000' → '10000'; '25.50' → '25.5'; '3.0' → '3' (forma canónica para comparar)."""
    tok = tok.replace(",", "")
    return tok.rstrip("0").rstrip(".") if "." in tok else tok


def numeric_tokens(text: str) -> set[str]:
    """Conjunto de números normalizados presentes en el texto."""
    return {_norm_number(t) for t in _NUM_RE.findall(text)}


def _reject(c: Citation, note: str) -> Citation:
    """Invariante 2 / ADR-005: cita sin validar → verified=False, confidence=0 y motivo."""
    return c.model_copy(update={"verified": False, "confidence": 0.0, "verification_note": note})


def _verify_one(c: Citation, corpus_norm: str) -> Citation:
    """Verifica una cita contra el corpus ya normalizado (ver docstring del módulo)."""
    excerpt = normalize_text(c.excerpt)
    if len(excerpt) < MIN_EXCERPT_CHARS:
        return _reject(c, "excerpt too short to verify")
    idx = corpus_norm.find(excerpt)
    if idx >= 0:
        window = excerpt
    else:
        al = fuzz.partial_ratio_alignment(excerpt, corpus_norm)
        if al is None or al.score < FUZZY_THRESHOLD:
            score = 0 if al is None else al.score
            return _reject(c, f"excerpt not found in source (fuzzy={score:.0f})")
        window = corpus_norm[al.dest_start:al.dest_end]
    # Los números se comprueban contra la ventana alineada y no contra todo el corpus:
    # un "100,000" inventado no puede justificarse con un "100,000" de otra página.
    missing = numeric_tokens(excerpt) - numeric_tokens(window)
    if missing:
        return _reject(c, f"numbers not in source window: {sorted(missing)}")
    return c.model_copy(update={"verified": True, "verification_note": None})


def validate_citations(citations: list[Citation], corpus: dict[str, str]) -> list[Citation]:
    """Valida cada cita contra la concatenación del corpus.

    Se concatena todo porque las etiquetas de fuente que da el LLM no son fiables.
    Corpus vacío (todas las descargas fallaron) → todas rechazadas con nota explícita
    (Review Focus #5): sin texto fuente nada puede quedar "verificado".
    """
    if not any((v or "").strip() for v in corpus.values()):
        return [_reject(c, "empty corpus — nothing to verify against") for c in citations]
    corpus_norm = normalize_text("\n".join(v for v in corpus.values() if v))
    return [_verify_one(c, corpus_norm) for c in citations]


def source_label_for(c: Citation, corpus: dict[str, str]) -> Optional[str]:
    """Etiqueta del corpus donde aparece el excerpt de una cita verificada (o None).

    Se usa para normalizar `source_url` a la fuente REAL (p.ej. "Web (Tavily)") en lugar
    de la que declare el LLM. Primero substring exacto normalizado; si no, la etiqueta
    con mejor partial_ratio por encima del umbral.
    """
    excerpt = normalize_text(c.excerpt)
    best: tuple[float, Optional[str]] = (0.0, None)
    for label, text in corpus.items():
        if not text:
            continue
        norm = normalize_text(text)
        if excerpt in norm:
            return label
        score = fuzz.partial_ratio(excerpt, norm)
        if score >= FUZZY_THRESHOLD and score > best[0]:
            best = (score, label)
    return best[1]


def value_supported_by_excerpt(value: float, excerpt: str, rel_tol: float = 0.01) -> bool:
    """True si algún número del excerpt, a escala ×1/×1e3/×1e6/×1e9, coincide con `value`.

    Se prueban todas las escalas porque los 10-K expresan tablas "(in millions)".
    Esto demuestra que los dígitos salen de la fuente; un error de magnitud sigue siendo
    posible (H2: unidad semántica).
    """
    for num, _unit in _SCALED_RE.findall(normalize_text(excerpt)):
        base = float(num.replace(",", ""))
        if any(math.isclose(base * s, value, rel_tol=rel_tol) for s in _SCALES):
            return True
    return False


def gate_llm_value(value: Any, cit_out, corpus: dict[str, str], *,
                   numeric: bool) -> tuple[Any, Optional[Citation]]:
    """Devuelve (valor_o_None, cita_validada_o_None).

    Un dato generado por el LLM solo se presenta como dato si lo respalda una cita
    verificada (ADR-005). Con numeric=True, además, el número debe aparecer en el
    excerpt verificado (a alguna escala). Sin cita → (None, None).
    """
    if value is None or cit_out is None:
        return None, None
    [cit] = validate_citations([cit_out.to_citation()], corpus)
    if not cit.verified:
        return None, cit
    if numeric and not value_supported_by_excerpt(float(value), cit.excerpt):
        return None, cit.model_copy(update={"verified": False, "confidence": 0.0,
                                            "verification_note": "value not present in excerpt"})
    return value, cit


def validate_and_relabel(citations: list[Citation], corpus: dict[str, str]) -> list[Citation]:
    """validate_citations + `source_url` de las verificadas = etiqueta REAL del corpus.

    La etiqueta que pone el LLM ("DEF 14A", una URL de blog...) no es fiable: si el
    excerpt se encontró en el texto de Tavily, la cita debe decir "Web (Tavily)" y no
    aparentar ser una fuente primaria. Las no verificadas conservan la etiqueta del LLM.
    """
    out = []
    for c in validate_citations(citations, corpus):
        if c.verified:
            label = source_label_for(c, corpus)
            if label is not None:
                c = c.model_copy(update={"source_url": label})
        out.append(c)
    return out


def count_verified(citations: list[Citation]) -> tuple[int, int]:
    """(verificadas, no verificadas) — para el log `<agent>_citations_validated`."""
    ok = sum(1 for c in citations if c.verified)
    return ok, len(citations) - ok
