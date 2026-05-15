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

from dataclasses import dataclass, field
from typing import Optional
import structlog

from due_diligence.config import settings

# Precios por modelo (USD / 1M tokens). Actualizar cuando cambien tarifas.
# Fuente: pricepertoken.com, verificado abril 2026.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":   (0.150, 0.600),
    "gpt-5.4-mini":  (0.750, 4.500),
    "gpt-4.1":       (2.000, 8.000),
    "gpt-4.1-mini":  (0.200, 0.800),
    "gpt-4o":        (2.500, 10.000),
}
_DEFAULT_PRICING = (0.002, 0.008)  # fallback conservador para modelos no listados


@dataclass
class RunStats:
    """Acumula métricas de uso LLM para un run completo. ADR-021."""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Acumula tokens y coste de una llamada individual."""
        pricing = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
        call_cost = (prompt_tokens * pricing[0] + completion_tokens * pricing[1]) / 1_000_000
        self.tokens_in += prompt_tokens
        self.tokens_out += completion_tokens
        self.cost_usd += call_cost
        self.calls += 1

log = structlog.get_logger()


class LLMError(Exception):
    """Excepción lanzada cuando fallan ambos proveedores LLM."""
    pass


class LLMClient:
    """Wrapper unificado para Anthropic Claude y OpenAI GPT."""

    def __init__(self) -> None:
        self._anthropic = None
        self._openai = None
        self._run_stats: RunStats = RunStats()

    def reset_run_stats(self) -> None:
        """Reinicia el acumulador antes de un nuevo análisis. Llamar desde CLI."""
        self._run_stats = RunStats()

    def get_run_stats(self) -> RunStats:
        """Devuelve las métricas acumuladas del run en curso."""
        return self._run_stats

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
        response_format: Optional[dict] = None,
    ) -> str:
        """
        Envía mensajes al LLM con fallback automático.

        Args:
            messages: Lista de dicts con role/content
            model: Modelo a usar (default: settings.default_model)
            system: Prompt del sistema (opcional)
            max_tokens: Máximo tokens en la respuesta
            response_format: OpenAI response_format dict (e.g. {"type": "json_object"}).
                             Ignored for Anthropic calls.

        Returns:
            Texto de la respuesta del LLM
        """
        model = model or settings.default_model
        # Si no hay API key de Anthropic configurada, ir directo a OpenAI.
        if not settings.anthropic_api_key:
            # Resolver el modelo OpenAI real antes de loguear para evitar
            # confusión al ver "claude-*" en los logs siendo provider=openai.
            oai_model = model if (model and not model.startswith("claude")) else settings.openai_fallback_model
            log.info("llm_complete", provider="openai", reason="no_anthropic_key", model=oai_model)
            return self._complete_openai(messages, system, max_tokens, model=oai_model, response_format=response_format)
        try:
            result = self._complete_anthropic(messages, model, system, max_tokens)
            log.debug("llm_complete", provider="anthropic", model=model)
            return result
        except Exception as e:
            log.warning("anthropic_failed_falling_back", error=str(e), model=model)
            return self._complete_openai(messages, system, max_tokens, model=model, response_format=response_format)

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
        self, messages: list[dict], system: Optional[str], max_tokens: int,
        model: Optional[str] = None,
        response_format: Optional[dict] = None,
    ) -> str:
        """
        Llamada a la API de OpenAI.

        model: si el caller pasa un modelo Claude (empieza por 'claude-'), se ignora
               y se usa openai_fallback_model. Si pasa un modelo OpenAI explícito
               (ej. 'gpt-5.4-mini'), se usa ese directamente.
        response_format: si se pasa {"type": "json_object"}, fuerza JSON mode en
               la respuesta del modelo. Previene truncaciones mid-JSON.
        """
        client = self._get_openai()
        # Ignorar modelos Claude si llegan aquí por fallback
        if model and not model.startswith("claude"):
            resolved_model = model
        else:
            resolved_model = settings.openai_fallback_model
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)
        # GPT-5.x usa max_completion_tokens en lugar de max_tokens.
        # max_completion_tokens funciona en todos los modelos OpenAI actuales.
        create_kwargs: dict = {
            "model": resolved_model,
            "messages": oai_messages,
            "max_completion_tokens": max_tokens,
        }
        if response_format is not None:
            create_kwargs["response_format"] = response_format
        response = client.chat.completions.create(**create_kwargs)
        choice = response.choices[0]
        # Detectar truncación por límite de tokens — el JSON quedará incompleto.
        if choice.finish_reason == "length":
            log.warning(
                "llm_response_truncated",
                model=resolved_model,
                max_tokens=max_tokens,
                finish_reason="length",
                hint="Increase max_tokens for this agent if parse errors follow.",
            )
        # Acumular tokens y coste (ADR-021)
        if response.usage:
            self._run_stats.add(
                model=resolved_model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
            log.debug(
                "llm_tokens",
                model=resolved_model,
                in_tokens=response.usage.prompt_tokens,
                out_tokens=response.usage.completion_tokens,
            )
        return choice.message.content


_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
