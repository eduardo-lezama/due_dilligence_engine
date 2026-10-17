"""
news_relevance — filtro determinista de relevancia para noticias (EC-111).

Qué hace este módulo:
    Función pura que decide si una noticia (Finnhub o Tavily) trata de la empresa
    analizada. Una noticia se conserva solo si su título o cuerpo menciona el ticker
    como token o el nombre de la empresa, y se descarta si es un volcado de navegación
    (menús de Yahoo, "Sign in", "Quote Lookup", decenas de enlaces markdown).

Por qué existe:
    Los informes reales traían ruido: para CALM, la búsqueda Tavily coincidía con la
    PALABRA "calm" ("Everybody Calm Down About GameStop", "Trade War… Relative Calm");
    para SERV se volcó un menú entero de Yahoo; para AAPL, "Dow gainers and losers".
    El LLM downstream razonaba sobre ese ruido. Es una decisión determinista (cero LLM,
    ADR-005), por eso vive en tools/ y se prueba con cadenas reales copiadas de informes.

Reglas de coincidencia (precisión sobre recall):
    - Ticker: sensible a mayúsculas, con límite de palabra ("CALM", "$CALM",
      "NASDAQ: CALM"). "Calm" en minúscula NO cuenta. Tickers de 1 letra exigen forma
      explícita ($X, (X), "NASDAQ: X") porque la letra suelta aparece en cualquier frase.
    - Nombre de varias palabras ("Cal-Maine Foods", "Serve Robotics"): sin distinguir
      mayúsculas y normalizando guiones/puntuación ("Cal Maine" == "Cal-Maine").
    - Nombre de UNA palabra ("Apple", "Target", "Visa"): puede ser una palabra común,
      así que se exige escrita con mayúscula inicial (o toda en mayúsculas).

Cómo encaja:
    market_agent filtra con `filter_relevant_news` antes de formatear el texto que ve el
    informe, el Value Expert y la CLI; el recuento descartado se loguea y se imprime.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

log = structlog.get_logger()

# Sufijos legales que no identifican a la empresa ("Apple Inc." -> "Apple"). "com" cubre
# "AMAZON COM INC" (EDGAR escribe el ".com" como palabra suelta).
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "plc", "lp", "sa", "nv", "ag", "holdings", "holding", "group", "the", "com",
}
# Palabras corporativas/geográficas genéricas: NO identifican a una empresa por sí solas, ni como
# primer token de un nombre largo ("General Motors" -> no "General") ni como nombre de una palabra
# ("SOUTHERN CO" -> solo cuenta "Southern Company", no "Southern California"). Lista agnóstica de
# empresa: no hay alias por ticker (invariante de sistemicidad del proyecto).
_GENERIC_WORDS = {
    "general", "american", "united", "first", "national", "southern", "northern", "eastern",
    "western", "international", "global", "new", "great", "royal", "standard", "public",
    "federal", "consolidated", "pacific", "atlantic", "digital", "advanced", "applied",
    "energy", "financial",
}
# Nombres de 2 letras que son palabras/siglas comunes en prensa: no sirven como nombre.
_COMMON_TWO_LETTER = {
    "IT", "US", "ON", "IN", "AT", "BE", "OR", "AS", "IS", "TO", "BY", "SO", "NO", "GO", "UP", "AI",
}
# Descriptores genéricos finales: "Cal-Maine Foods" se cita también como "Cal-Maine".
_GENERIC_TAILS = {
    "foods", "brands", "stores", "technologies", "industries", "international",
    "systems", "resources", "enterprises", "financial", "bancorp", "pharmaceuticals",
    "therapeutics", "energy", "partners", "trust", "labs", "solutions",
}
# Frases de menú/cabecera típicas de páginas volcadas completas (Yahoo, etc.).
_MENU_PHRASES = (
    "sign in", "quote lookup", "my portfolio", "newsletters", "privacy policy",
    "terms of service", "cookie", "subscribe now", "mail", "watchlist",
)
_MD_LINK = re.compile(r"\[[^\]]*\]\(https?://[^)]*\)")


def _clean(text: str) -> str:
    """Quita apóstrofos (' y ’) y convierte el '&' interno en 'and', conservando mayúsculas.

    "McDonald's" -> "McDonalds"; "AT&T" -> "ATandT". Un '&' rodeado de espacios
    ("Procter & Gamble") se deja para que _normalize lo trate como separador.
    """
    text = text.replace("'", "").replace("’", "")
    return re.sub(r"(?<=[A-Za-z0-9])&(?=[A-Za-z0-9])", "and", text)


def _normalize(text: str) -> str:
    """Minúsculas, sin apóstrofos y todo lo no alfanumérico -> espacio ('Cal-Maine's' -> 'cal maines')."""
    return re.sub(r"[^a-z0-9]+", " ", _clean(text).lower()).strip()


def _stripped_words(company_name: str | None) -> list[str]:
    """Palabras del nombre sin sufijos legales finales ni 'The' inicial (conserva mayúsculas).

    Conserva el '&' interno de una palabra ("AT&T") pero descarta el '&' suelto
    ("PROCTER & GAMBLE" -> ['PROCTER', 'GAMBLE']).
    """
    if not company_name:
        return []
    cleaned = lambda w: re.sub(r"[^A-Za-z0-9&-]", "", w)  # noqa: E731 - quita puntos/comas/apóstrofos
    words = [cleaned(w) for w in re.split(r"[\s,]+", company_name.strip())]
    words = [w for w in words if re.search(r"[A-Za-z0-9]", w)]
    while len(words) > 1 and words[-1].lower() in _LEGAL_SUFFIXES:
        words.pop()
    while words and words[0].lower() == "the":
        words.pop(0)
    return words


def company_name_variants(company_name: str | None) -> list[str]:
    """Variantes de nombre para buscar: completo sin sufijo legal y sin descriptor final.

    'Cal-Maine Foods, Inc.' -> ['Cal-Maine Foods', 'Cal-Maine']; 'Apple Inc.' -> ['Apple'].
    Devuelve [] si el nombre es None o es solo ruido legal. Conserva la capitalización
    original (la necesita la regla de nombres de una palabra). El primer token de un nombre
    largo ("Meta" en "Meta Platforms") se trata aparte en `mentions_company_name`.
    """
    words = _stripped_words(company_name)
    if not words:
        return []
    variants = [" ".join(words)]
    if len(words) > 1 and words[-1].lower() in _GENERIC_TAILS:
        variants.append(" ".join(words[:-1]))
    return variants


def _capitalized_word_match(clean_text: str, compact: str) -> bool:
    """True si `compact` (minúsculas, sin espacios) aparece como palabra entera escrita con inicial en mayúscula.

    Sin distinguir mayúsculas en el resto ("McDonalds" == "MCDONALDS"), pero la forma en el texto
    debe empezar en mayúscula o dígito: "apple pie" no cuenta, "Apple" / "APPLE" sí.
    """
    pattern = rf"(?<![A-Za-z0-9]){re.escape(compact)}(?![A-Za-z0-9])"
    return any(not m.group()[:1].islower()
               for m in re.finditer(pattern, clean_text, flags=re.IGNORECASE))


def mentions_ticker(text: str, ticker: str) -> bool:
    """True si `ticker` aparece como token en mayúsculas ("CALM", "$CALM", "NASDAQ: CALM").

    Sensible a mayúsculas: "Calm down" no cuenta. Un ticker de 1 letra solo cuenta en forma
    explícita ($X, (X), "NASDAQ: X"/"NYSE: X"), porque la letra suelta es ruido.
    """
    if not ticker:
        return False
    t = re.escape(ticker)
    if len(ticker) == 1:
        pattern = rf"(\$|\(|(?:NASDAQ|NYSE|AMEX|NYSEAMERICAN)\s*[:\-]\s*){t}(?![A-Za-z0-9])"
    else:
        pattern = rf"(?<![A-Za-z0-9]){t}(?![A-Za-z0-9])"
    return re.search(pattern, text) is not None


def mentions_company_name(text: str, company_name: str | None) -> bool:
    """True si el texto nombra a la empresa (ver reglas de una palabra / varias en el docstring del módulo).

    Reglas añadidas tras la revisión de código (nombres EDGAR vs prensa):
      - apóstrofos y '&' interno normalizados ("McDonald's", "AT&T");
      - nombre de varias palabras: también su forma compacta si tiene >= 6 letras
        ("EXXON MOBIL" -> "ExxonMobil");
      - primer token de un nombre largo ("Meta Platforms" -> "Meta") si tiene >= 4 letras, es
        alfabético y no es una palabra genérica; exige mayúscula inicial como los nombres de una palabra;
      - nombre de una palabra genérica ("SOUTHERN CO"): solo cuenta con sufijo ("Southern Company");
      - nombre de 2 letras ("HP", "3M"): solo en MAYÚSCULAS tal cual, y no si es una palabra común.
    Decisión: un nombre de una palabra ambiguo pero capitalizado ("Target") se acepta; es la misma
    regla de precisión que "Apple"/"Visa" y descartarlo perdería noticias reales de la empresa.
    """
    norm_text = _normalize(text)
    clean_text = _clean(text)
    for variant in company_name_variants(company_name):
        norm_variant = _normalize(variant)
        if " " in norm_variant:
            # Varias palabras: ya es unívoco; se ignora mayúsculas y guiones.
            if re.search(rf"(?<![a-z0-9]){re.escape(norm_variant)}(?![a-z0-9])", norm_text):
                return True
            compact = norm_variant.replace(" ", "")
            if len(compact) >= 6 and _capitalized_word_match(clean_text, compact):
                return True
        elif norm_variant in _GENERIC_WORDS:
            # "Southern" a secas es ruido; "Southern Company"/"Southern Co" sí identifica.
            suffixed = "|".join(sorted(_LEGAL_SUFFIXES - {"the", "com"}))
            if re.search(rf"(?<![a-z0-9]){re.escape(norm_variant)} (?:{suffixed})(?![a-z0-9])", norm_text):
                return True
        elif len(norm_variant) >= 3:
            # Una palabra (posible palabra común): solo "Apple"/"APPLE", no "apple".
            if _capitalized_word_match(clean_text, norm_variant):
                return True
        elif len(norm_variant) == 2 and variant.upper() not in _COMMON_TWO_LETTER:
            # "HP", "3M": solo en mayúsculas tal cual (case-sensitive), con límite de palabra.
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(variant.upper())}(?![A-Za-z0-9])", clean_text):
                return True
    words = _stripped_words(company_name)
    if len(words) > 1:
        first = words[0]
        if first.isalpha() and len(first) >= 4 and first.lower() not in _GENERIC_WORDS:
            return _capitalized_word_match(clean_text, first.lower())
    return False


def is_navigation_dump(text: str) -> bool:
    """True si el texto es un volcado de menú/navegación y no un artículo.

    Señales: >= 6 enlaces markdown, o >= 3 frases de menú distintas ("Sign in",
    "Quote Lookup"...), o >= 10 líneas muy cortas (<= 3 palabras) sin dígitos con más
    líneas de menú que del resto (las cortas con cifras son tablas de resultados). Umbrales conservadores: un artículo con 2 enlaces o 1 "Sign in" pasa.
    """
    if not text:
        return False
    if len(_MD_LINK.findall(text)) >= 6:
        return True
    low = text.lower()
    if sum(1 for p in _MENU_PHRASES if re.search(rf"\b{re.escape(p)}\b", low)) >= 3:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Solo cuentan líneas cortas SIN dígitos: "EPS"/"$0.45" de una tabla de resultados no es un menú.
    short = sum(1 for ln in lines if len(ln.split()) <= 3 and not re.search(r"\d", ln))
    return short >= 10 and short > len(lines) - short


def is_relevant_news(title: str, body: str, ticker: str, company_name: str | None) -> bool:
    """Decide si una noticia trata de la empresa: ticker o nombre en título/cuerpo y no es un volcado de menú."""
    text = f"{title}\n{body}"
    if is_navigation_dump(body):
        return False
    return mentions_ticker(text, ticker) or mentions_company_name(text, company_name)


def filter_relevant_news(
    items: list[dict[str, Any]],
    ticker: str,
    company_name: str | None,
    title_key: str = "headline",
    body_key: str = "summary",
) -> tuple[list[dict[str, Any]], int]:
    """Filtra noticias no relacionadas con la empresa. Devuelve (conservadas, nº descartadas).

    Finnhub usa headline/summary; Tavily usa title/content. Se evalúa el texto COMPLETO
    (antes de truncar para el prompt). Loguea el recuento descartado (EC-111).
    """
    kept = [
        it for it in items
        if is_relevant_news(it.get(title_key) or "", it.get(body_key) or "", ticker, company_name)
    ]
    dropped = len(items) - len(kept)
    if dropped:
        log.info(
            "news_unrelated_filtered",
            ticker=ticker,
            dropped=dropped,
            kept=len(kept),
            titles=[(it.get(title_key) or "")[:60] for it in items if it not in kept],
        )
    return kept, dropped
