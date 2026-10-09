"""
proxy_ownership — lectura determinista de la tabla de participaciones del DEF 14A (EC-93).

Por qué existe: el agente de management recibía el proxy como texto recortado por títulos, y
en CALM y PRDO la ventana caía DESPUÉS de la tabla (sus títulos no coincidían con los
marcadores); en AZO la conversión a texto troceaba la tabla. Sin la tabla, el LLM sacaba el %
de la web. Aquí la tabla se lee por su ESTRUCTURA HTML (filas/celdas) y el % de insiders se
calcula en código (invariante 1, ADR-005); el LLM recibe la tabla ya limpia para citarla.

Función pura sobre HTML: la red vive en EdgarManagementAdapter.fetch_def14a_html.
No se usa `ProxyStatement.beneficial_ownership` de edgartools 5.59.1: devuelve vacío para estos
proxies (Filing.html() → None) y convierte "*" en un 0,5 % inventado (html_extractor.py:959).
"""
from __future__ import annotations

import re
from typing import Optional

import lxml.html
import structlog

from due_diligence.schemas.reports import BeneficialOwnership, BeneficialOwnershipRow

log = structlog.get_logger()

# Fila del grupo: "All (current) directors and executive officers as a group (14 persons)".
# Exigir "directors|officers" evita "Non-executive officer employees as a group" no, pero esa fila
# de la tabla de premios de PRDO cae igualmente por no tener celda de porcentaje.
_GROUP_RE = re.compile(r"\b(directors|officers)\b.{0,80}\bas\s+a\s+group\b", re.I)
_FOOTNOTE_RE = re.compile(r"\(\d{1,3}\)")                   # "(17)" pegado a cifras o nombres
_LABEL_FOOTNOTE_RE = re.compile(r"\(\d{1,3}\)(?!\s*persons)", re.I)
_INT_RE = re.compile(r"^\d{1,3}(,\d{3})+$|^\d+$")            # 1,697,343 | 502388
_PCT_INLINE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")         # "9.63%" | "3.6 %"
_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")                     # "3.6" (con "%" en la celda siguiente)
_PARSER = lxml.html.HTMLParser(encoding="utf-8", remove_comments=True)
# Cabecera multiclase: dos letras de clase distintas ("Class A" ... "Class B") o "voting power".
# Solo se mira la CABECERA (primeras filas): las notas al pie de tablas de una sola clase también
# dicen "sole voting power" (CALM).
_CLASS_RE = re.compile(r"\bclass\s+([a-c])\b", re.I)
_VOTING_RE = re.compile(r"voting\s*power", re.I)
_HEADER_ROWS = 4
_CELL_FIGURE_RE = re.compile(r"^(\d{1,3}(,\d{3})+|\d+(\.\d+)?)\s*%?$|^\*+$")
# "*", "**", "*%" (ACN) o "Less than 1%": menos del 1 %, sin cifra.
_LT1_RE = re.compile(r"^\*+%?$|less\s+than\s+(1|one)(\.0)?\s*(%|percent)", re.I)
# Celda que nombra una clase de acción: la clase va POR FILA (NKE: "Class B" / "Preferred").
_CLASS_CELL_RE = re.compile(r"^(class\s+[a-c]|preferred|common)(\s+(common\s+)?stock)?$", re.I)
# Cabecera con columnas de voto sin "voting power" (UHAL: "Voting Common" / "Non-Voting Common").
_VOTING_COMMON_RE = re.compile(r"(non-?\s*)?voting\s+(class\s+[a-c]\s+)?common", re.I)


def _clean(cell_text: str) -> str:
    """Normaliza una celda: espacios duros/de ancho cero/BOM fuera, espacios colapsados."""
    cell_text = cell_text.replace("﻿", "")   # BOM pegado a notas al pie ("(17﻿)", PLUG)
    text = cell_text.replace("\xa0", " ").replace("​", "")
    return " ".join(text.split())


def _row_cells(tr) -> list[str]:
    """Celdas no vacías de una fila, en orden (las vacías son espaciadores de maquetación)."""
    # itertext() + espacio: text_content() pegaba los trozos separados por <br>
    # ("Non-VotingClass ACommonStock" en NWSA, "anddirectors" en MSGS).
    cells = [_clean(" ".join(td.itertext())) for td in tr if td.tag in ("td", "th")]
    return [c for c in cells if c]


def _parse_row(cells: list[str]) -> Optional[BeneficialOwnershipRow]:
    """Fila de participaciones o None.

    Regla (verificada en 5 proxies reales): la ÚLTIMA celda es el porcentaje ("3.6"+"%",
    "9.63%", "*", "Less than 1%"); el número de acciones es el último entero ANTES de él (en
    tablas con columnas de RSU/opciones, ese es el total). Sin porcentaje no es una fila de
    participación: así caen las tablas de premios y las notas al pie que dicen "as a group".
    """
    if len(cells) < 3:
        return None
    vals = [_FOOTNOTE_RE.sub("", c).strip() for c in cells]
    percent: Optional[float] = None
    below = False
    if vals[-1] == "%" and _NUM_RE.match(vals[-2]):
        percent, end = float(vals[-2]), len(vals) - 2
    elif m := _PCT_INLINE_RE.match(vals[-1]):
        percent, end = float(m.group(1)), len(vals) - 1
    elif _LT1_RE.search(vals[-1]):
        below, end = True, len(vals) - 1
    else:
        return None
    shares = next((int(v.replace(",", "")) for v in reversed(vals[1:end]) if _INT_RE.match(v)), None)
    if shares is None:
        return None
    # En la etiqueta, "(26) persons" (AZO) es el nº de personas, no una nota: solo se quitan las
    # marcas "(n)" que no van seguidas de "persons".
    label = " ".join(_LABEL_FOOTNOTE_RE.sub("", cells[0]).split())
    pct_text = "less than 1%" if below else f"{percent:g}%"
    return BeneficialOwnershipRow(label=label, shares=shares, percent=percent, below_1pct=below,
                                  text=f"{label} | {shares:,} shares | {pct_text}")


def _pct_slots(cells: list[str]) -> int:
    """Nº de celdas de porcentaje de una fila ("3.6"+"%" cuenta una vez, "9.63%", "*", "*%")."""
    vals = [_FOOTNOTE_RE.sub("", c).strip() for c in cells]
    n = 0
    for i, v in enumerate(vals):
        if _PCT_INLINE_RE.match(v) or _LT1_RE.match(v):
            n += 1
        elif _NUM_RE.match(v) and i + 1 < len(vals) and vals[i + 1] == "%":
            n += 1
    return n


def _is_multi_class(rows: list[list[str]]) -> bool:
    """¿Hay más de una columna de % (varias clases, voto, "acciones y unidades")?

    Revisión adversarial en vivo (33 emisores): mirar solo "Class A ... Class B" en la cabecera
    dejaba pasar 5 tablas que daban una CIFRA EQUIVOCADA. Por eso, además de la cabecera:
      - cualquier fila con ≥ 2 celdas de % (HEI, UHAL, SPG, NWSA: dos clases o dos grupos);
      - cualquier fila con una celda que nombra una clase (NKE: la clase va por fila).
    """
    header = " ".join(" ".join(r) for r in rows[:_HEADER_ROWS])
    if (len({m.lower() for m in _CLASS_RE.findall(header)}) >= 2 or _VOTING_RE.search(header)
            or _VOTING_COMMON_RE.search(header)):
        return True
    return any(_pct_slots(r) >= 2 or any(_CLASS_CELL_RE.match(c) for c in r[1:]) for r in rows)


def _multi_class_row(cells: list[str]) -> Optional[BeneficialOwnershipRow]:
    """Fila de una tabla multiclase, literal: sin acciones ni % únicos (serían falsos)."""
    if len(cells) < 2:
        return None
    vals = [_FOOTNOTE_RE.sub("", c).strip() for c in cells[1:]]
    label = " ".join(_LABEL_FOOTNOTE_RE.sub("", cells[0]).split())
    # Una fila de la tabla tiene etiqueta y alguna cifra CORTA ("52,117,848", "11.4%", "*").
    # Las notas al pie (mismo <table>, texto con "voting power") son párrafos: fuera.
    if not label or not any(_CELL_FIGURE_RE.match(v) for v in vals):
        return None
    return BeneficialOwnershipRow(label=label, shares=None, percent=None, multi_class=True,
                                  text=" | ".join([label] + [v for v in vals if v]))


def parse_beneficial_ownership(html: str) -> Optional[BeneficialOwnership]:
    """Filas de participaciones de todas las tablas candidatas + la fila del grupo.

    Tablas candidatas: las que mencionan "beneficial" o "as a group" y tienen ≥ 2 filas válidas
    (titulares 5 % y consejo pueden ir en tablas separadas: AZO, PRDO). Las tablas multiclase
    (varias clases o % de voto en la cabecera: PATH, GOOGL, BRK-B) se leen fila a fila literal.
    Visto en vivo: con la regla de la última celda, PATH salía "85,2 %" — su poder de VOTO. Devuelve None si no hay
    ninguna (proxy sin tabla legible, p.ej. tabla como imagen) — el llamador lo registra; nunca
    se adivina un porcentaje.
    """
    if not html or not html.strip():
        return None
    try:
        # Encoding EXPLÍCITO: sin él lxml decodifica los bytes como latin-1 y el espacio duro
        # ( ) se convierte en "Â", una celda no vacía que rompe la regla de la última celda.
        tree = lxml.html.fromstring(html.encode("utf-8"), parser=_PARSER)
    except (ValueError, lxml.etree.ParserError) as e:
        log.warning("def14a_ownership_html_unparseable", error=str(e))
        return None
    rows: list[BeneficialOwnershipRow] = []
    group_candidates: list[tuple[int, BeneficialOwnershipRow]] = []   # (filas de su tabla, fila)
    for table in tree.iter("table"):
        text = _clean(" ".join(table.itertext())).lower()
        if "beneficial" not in text and "as a group" not in text:
            continue
        table_rows = [c for tr in table.iter("tr") if (c := _row_cells(tr))]
        row_parser = _multi_class_row if _is_multi_class(table_rows) else _parse_row
        parsed = [r for cells in table_rows if (r := row_parser(cells)) is not None]
        groups = [r for r in parsed if _GROUP_RE.search(r.label)]
        # Una tabla de 1 fila vale si ES la fila del grupo (Ford y Brown-Forman la separan).
        if len(parsed) >= 2 or groups:
            rows.extend(parsed)
            group_candidates += [(len(parsed), g) for g in groups]
    if not rows:
        return None
    # Si varias tablas tienen una fila "as a group", gana la de la tabla con más filas: la de
    # participaciones, no una de planes de acciones que aparezca antes (PLUG, CRM).
    group = max(group_candidates, key=lambda c: c[0])[1] if group_candidates else None
    return BeneficialOwnership(rows=rows, group=group)


def render_ownership(bo: BeneficialOwnership) -> str:
    """Texto limpio de la tabla para el LLM y para el corpus de citas (una fila por línea).

    El LLM cita estas líneas tal cual; como son también el corpus, la cita se verifica sin
    depender de cómo edgartools convirtió la tabla a texto.
    """
    return "\n".join(r.text for r in bo.rows)
