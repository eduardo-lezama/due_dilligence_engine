"""
Errores tipados de la capa LLM (ADR-032).

Viven en un módulo propio, sin imports de SDK, por dos razones: (1) los agentes pueden
capturarlos sin tocar anthropic/openai (invariante 3); (2) tests/test_llm_client.py hace
importlib.reload(client), y si las clases vivieran allí el reload crearía clases nuevas
que el `except LLMError` de los agentes ya no reconocería.
"""


class LLMError(Exception):
    """Base de todos los fallos de la capa LLM. Los agentes solo capturan esta clase."""


class LLMProviderError(LLMError):
    """Fallo de transporte o del proveedor (red, 429, 5xx, auth).

    fallback_eligible=True → structured() prueba el otro proveedor.
    False (400/404/422: schema inválido, modelo inexistente) → error de programación, no fallback.
    """

    def __init__(self, message: str, *, provider: str, fallback_eligible: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.fallback_eligible = fallback_eligible


class LLMTruncatedError(LLMError):
    """La salida se cortó en max_tokens: el JSON está incompleto.

    No hay fallback: el otro proveedor truncaría igual con el mismo presupuesto.
    """


class LLMSchemaError(LLMError):
    """La salida no cumple el schema: JSON inválido, validación Pydantic, refusal o content filter."""
