"""
Consola segura para logs (bloque A, A6, ADR-043).

Por qué existe: structlog escribe en sys.stdout con la codificación del sistema. En Windows, con la
salida redirigida a un fichero, es cp1252, y un mensaje con "≈" (valuation_math: "weight ≈ 0")
lanzaba UnicodeEncodeError y abortaba la valoración de ADP en el diagnóstico. Un log nunca debe
tumbar un cálculo: si la codificación no es UTF-8, los caracteres no representables se sustituyen.
"""
import sys


def make_console_safe(*streams) -> None:
    """Reconfigura los streams (por defecto stdout y stderr) con errors="replace" si no son UTF-8.

    No toca streams sin `reconfigure` (capturas de pytest, StringIO) ni los que ya son UTF-8.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
