"""
Utilidades comunes de los agentes LLM para el patrón fail-loud (ADR-032).

Los cinco agentes LLM (filings, management, competitive, value_expert, critic) repiten
el mismo patrón: `structured()` → report; `LLMError` → `stub=True, error=...`. Este
módulo centraliza las dos piezas que deben ser idénticas en todos ellos (el formato
del error y la lectura del modelo realmente usado) para que archiver, CLI y SQLite
puedan tratarlos de forma genérica.
"""
from typing import Optional


def error_label(e: BaseException) -> str:
    """Formato canónico del campo `error` de los reports: '<ClaseError>: mensaje'.

    archiver.collect_failures y cli._show_failures lo muestran tal cual; los tests
    comprueban el prefijo de clase (p.ej. 'LLMTruncatedError').
    """
    return f"{type(e).__name__}: {e}"


def model_used(client) -> Optional[str]:
    """Modelo que realmente respondió la última llamada (LLMClient.last_model_used).

    Puede diferir del pedido si hubo fallback de proveedor. Se acepta solo un `str`:
    cualquier otra cosa (p.ej. un MagicMock en tests que no lo configuran) → None,
    para no contaminar el report con un valor no serializable.
    """
    value = getattr(client, "last_model_used", None)
    return value if isinstance(value, str) else None
