"""
Helpers de formateo compartidos por todos los módulos del sistema.

Por qué este módulo existe:
    Antes de centralizarse aquí, el formatter de USD se duplicaba en cuatro
    archivos (value_expert, archiver, cli, critic) — cada uno con su propia
    copia de `value/1e9`. Cuando arreglamos EC-14 (formatter adaptativo)
    olvidamos `critic.py` y el bug regresó parcialmente.

    Este módulo es el ÚNICO punto donde se decide cómo se renderizan los
    valores monetarios. Cualquier mejora futura (ej. soporte para EUR,
    micro-cap formatting) se hace aquí y se propaga a todos los consumidores.

Referencia: ADR-022 (formatter adaptativo) + meta-pattern post-EC-14.
"""

from __future__ import annotations
from datetime import date
from typing import Optional


def fmt_usd(value: float) -> str:
    """
    Formatea un valor USD eligiendo automáticamente la unidad apropiada.

    Reglas:
        >= $1B   → "$X.XB"  (large caps)
        >= $1M   → "$XXXM"  (small/mid caps)
        >= $1k   → "$XXk"   (micro caps)
        menor    → "$XX"

    Por qué adaptativo:
        Para una small cap con FCF=$39.5M, dividir siempre por 1e9 produce
        "$0.0B" — el LLM lo lee literal y reproduce "FCF was zero billion"
        en su análisis. La unidad debe coincidir con la magnitud para que
        el output sea legible (humano y modelo).

    Args:
        value: Valor en USD (puede ser negativo).

    Returns:
        String formateado con signo `$` y unidad apropiada.

    Examples:
        >>> fmt_usd(39_500_000)
        '$40M'
        >>> fmt_usd(2_800_000_000_000)
        '$2800.0B'
        >>> fmt_usd(-1_200_000_000)
        '$-1.2B'
    """
    abs_v = abs(value)
    if abs_v >= 1e9:
        return f"${value / 1e9:.1f}B"
    elif abs_v >= 1e6:
        return f"${value / 1e6:.0f}M"
    elif abs_v >= 1e3:
        return f"${value / 1e3:.0f}k"
    else:
        return f"${value:.0f}"


_STALE_PROXY_DAYS = 456   # ~15 meses: el proxy anual siguiente ya debería existir


def fmt_insider_ownership(pct: Optional[float], shares: Optional[int], below_1pct: bool,
                          multi_class_row: Optional[str] = None, as_of: Optional[str] = None,
                          ) -> Optional[str]:
    """Participación de directivos + consejo como grupo, para contexto LLM, checkpoint y CLI (EC-93).

    "3.6% (1,697,343 shares)" | "less than 1% (9,079,765 shares)" | None si no hay dato.
    "less than 1%" sin cifra cuando el proxy pone "*": nunca un número inventado.
    Multiclase: la fila literal (por clase y % de voto), porque un % único sería falso.
    as_of (fecha del DEF 14A): se añade siempre; si tiene más de ~15 meses, aviso de dato viejo.
    """
    if multi_class_row:
        text = f"multi-class shares — directors and officers as a group (per class / voting power): {multi_class_row}"
    elif pct is None and not below_1pct:
        return None
    else:
        head = "less than 1%" if below_1pct else f"{pct:.1f}%"
        text = f"{head} ({shares:,} shares)" if shares is not None else head
    if as_of:
        text += f" [proxy filed {as_of}]"
        try:
            if (date.today() - date.fromisoformat(as_of[:10])).days > _STALE_PROXY_DAYS:
                text += " ⚠️ stale proxy (>15 months) — ownership may have changed"
        except ValueError:
            pass   # fecha con formato inesperado: se muestra tal cual, sin juzgar antigüedad
    return text
