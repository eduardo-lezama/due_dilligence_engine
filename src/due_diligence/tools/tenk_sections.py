"""
tenk_sections — localiza las secciones 1, 1A y 7 en el texto completo de un 10-K (bloque B, EC-106).

Por qué existe: el parser de secciones de edgartools devuelve `items: []` en algunos 10-K (KO y JNJ
en sus 10-K de 2025, verificado el 2026-10-17) y el Filings agent recibía el apartado VACÍO sin que
nadie lo dijera. El texto completo del filing sí está. Aquí se buscan los encabezados de cada sección
en ese texto. Funciones puras: `edgar.py` hace la red y llama aquí si el parser primario falla.
"""
from __future__ import annotations

import re

# Encabezado = "Item N" + título, al principio de una línea. Anclar al inicio de línea evita las
# referencias en prosa ("see Item 1A. Risk Factors"), que no son el comienzo de la sección.
_TITLES = {
    "1": r"Business",
    "1A": r"Risk\s+Factors",
    "7": r"Management[’'`s]*\s+Discussion",
}
# Entre número y título se admiten puntuación y "|" (encabezados en tablas de texto: "| ITEM 1A. |RISK
# FACTORS |", BBW). \s cubre el espacio duro (\xa0) que usan muchos filings.
_HEADER = r"(?im)^[ \t\W]*Item\s*{num}[\s\.:—–|-]*{title}"

# Final de la sección: el encabezado del Item que la sigue, o una nueva aparición del encabezado de la
# propia sección u otra sección conocida (lo que llegue antes). Lo segundo corta las entradas del índice
# aunque el índice no liste el Item siguiente. "Cualquier Item cierra la sección" se probó con 10-K y cortaba el
# cuerpo en prosa que empieza línea ("Item 8 of this report", KO) y en formatos con encabezados
# repetidos (MSFT): por eso solo cierra el Item esperado.
_NEXT = {"1": r"1A|1B|1C|2", "1A": r"1B|1C|2", "7": r"7A|8"}
# El cierre exige un TÍTULO tras el número (mayúscula; "Item 1A. Risk Factors", "ITEM 2. PROPERTIES"):
# la prosa que empieza línea ("Item 1A of this report describes…") no cierra la sección (revisión del
# bloque B, #2). Sin re.I: "Item"/"ITEM" se escriben explícitos para que [A-Z] distinga mayúsculas.
_END = r"(?m)^[ \t\W]*(?:Item|ITEM)\s*(?:{nxt})\b[\s\.:—–|-]*[A-Z]"

# Un segmento más corto que esto es un índice o una referencia cruzada, no la sección.
MIN_SECTION_CHARS = 500

# Prefijo de la marca de recorte: lo busca el Filings agent para listar las secciones recortadas.
TRUNCATION_TAG = "[TRUNCATED:"


def extract_section(full_text: str, item: str) -> str:
    """Texto de la sección `item` ("1", "1A" o "7") del 10-K, o "" si no se localiza.

    Un 10-K repite los encabezados (índice al principio, a veces un "cross reference index" al
    final). En lugar de elegir la primera o la última aparición, se mide el segmento que va de cada
    encabezado hasta el final de la sección (ver _NEXT) y se devuelve el MÁS LARGO: el índice y las
    referencias cruzadas son cortos; el cuerpo, no. Segmentos < MIN_SECTION_CHARS → "".
    Un encabezado sin final reconocible se descarta: el "segmento" llegaría hasta el final del
    documento (formatos con una palabra por línea, como el 10-K de CALM).
    """
    if not full_text or item not in _TITLES:
        return ""
    start_re = re.compile(_HEADER.format(num=item, title=_TITLES[item]))
    end_re = re.compile(_END.format(nxt=_NEXT[item]))
    # Cualquier encabezado CON TÍTULO de las tres secciones también cierra (una entrada del índice
    # seguida de otra entrada del índice); exigir el título evita cortar por prosa.
    titled_re = re.compile("|".join(_HEADER.format(num=n, title=t)[len("(?im)"):]
                                    for n, t in _TITLES.items()), re.I | re.M)
    best = ""
    for m in start_re.finditer(full_text):
        end = end_re.search(full_text, m.end())
        if end is None:
            continue
        other = titled_re.search(full_text, m.end())
        stop = min(end.start(), other.start()) if other else end.start()
        segment = full_text[m.end():stop].strip()
        if len(segment) > len(best):
            best = segment
    return best if len(best) >= MIN_SECTION_CHARS else ""


def truncation_marker(kept: int, total: int) -> str:
    """Marca visible que se añade al texto recortado: el LLM y el validador saben que falta el resto."""
    return f"\n{TRUNCATION_TAG} first {kept:,} of {total:,} characters of this section]"


def truncate_visibly(text: str, max_chars: int) -> tuple[str, bool]:
    """(texto, recortado). Si supera max_chars se corta y se añade la marca (invariante 5: nada en silencio)."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + truncation_marker(max_chars, len(text)), True
