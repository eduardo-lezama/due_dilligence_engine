"""
LLM Client — wrapper unificado para Anthropic Claude y OpenAI GPT.

Qué hace este módulo:
    Proporciona LLMClient con un método complete() unificado que:
    - Intenta usar Anthropic Claude (modelo principal) primero
    - Fallback automático a OpenAI GPT-4 si Claude falla
    - Lazy initialization de clientes (solo se crean cuando se necesitan)

Por qué fallback automático:
    - Claude tiene mejor calidad para análisis financieros complejos
    - Si la API key no está configurada o hay rate limits, fallback a GPT-4
    - Garantiza que el sistema siempre funcione aunque un proveedor falle

Lazy initialization:
    - Los clientes (_anthropic, _openai) se crean en la primera llamada
    - Evita importar librerías pesadas si no se usan
    - Permite arrancar el CLI sin API keys configuradas

Uso:
    client = get_client()
    response = client.complete(messages=[...], model="claude-sonnet-4-6")
"""

from typing import Optional
import structlog

from due_diligence.config import settings

log = structlog.get_logger()


class LLMError(Exception):
    """Excepción lanzada cuando fallan ambos proveedores LLM."""
    pass


class LLMClient:
    """Wrapper unificado para Anthropic Claude y OpenAI GPT."""

    def __init__(self) -> None:
        # Lazy initialization: los clientes se crean en la primera llamada
        self._anthropic = None
        self._openai = None

    def _get_anthropic(self):
        """Lazy initialization del cliente Anthropic."""
        if self._anthropic is None:
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._anthropic

    def _get_openai(self):
        """Lazy initialization del cliente OpenAI."""
        if self._openai is None:
            import openai
            self._openai = openai.OpenAI(api_key=settings.openai_api_key)
        return self._openai

    def complete(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """
        Envía mensajes al LLM con fallback automático.

        Args:
            messages: Lista de dicts con role/content
            model: Modelo a usar (default: settings.default_model)
            system: Prompt del sistema (opcional)
            max_tokens: Máximo tokens en la respuesta

        Returns:
            Texto de la respuesta del LLM
        """
        model = model or settings.default_model
        # Si no hay API key de Anthropic configurada, ir directo a OpenAI
        if not settings.anthropic_api_key:
            log.info("llm_complete", provider="openai", reason="no_anthropic_key")
            return self._complete_openai(messages, system, max_tokens)
        try:
            result = self._complete_anthropic(messages, model, system, max_tokens)
            log.debug("llm_complete", provider="anthropic", model=model)
            return result
        except Exception as e:
            log.warning("anthropic_failed_falling_back", error=str(e), model=model)
            return self._complete_openai(messages, system, max_tokens)

    def _complete_anthropic(
        self, messages: list[dict], model: str, system: Optional[str], max_tokens: int
    ) -> str:
        """Llamada a la API de Anthropic Claude."""
        client = self._get_anthropic()
        kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text

    def _complete_openai(
        self, messages: list[dict], system: Optional[str], max_tokens: int
    ) -> str:
        """Llamada a la API de OpenAI GPT-4 (fallback)."""
        client = self._get_openai()
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)
        response = client.chat.completions.create(
            model=settings.openai_fallback_model,
            messages=oai_messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content


_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
