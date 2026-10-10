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
from typing import Optional, TypeVar
import threading

import structlog
from pydantic import BaseModel

from due_diligence.config import settings

T = TypeVar("T", bound=BaseModel)

# Precios por modelo (USD / 1M tokens, entrada/salida). Actualizar cuando cambien tarifas.
# OpenAI: pricepertoken.com, verificado abril 2026.
# Claude: tabla oficial de Anthropic (skill claude-api, cacheada 2026-09-25).
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":   (0.150, 0.600),
    "gpt-5.4-mini":  (0.750, 4.500),
    "gpt-4.1":       (2.000, 8.000),
    "gpt-4.1-mini":  (0.200, 0.800),
    "gpt-4o":        (2.500, 10.000),
    # Generaciones 5.6/6 (precios oficiales platform.openai.com/docs/pricing, verificados 2026-10-10).
    "gpt-6-luna":    (0.100, 0.500),
    "gpt-5.6-luna":  (0.200, 1.200),
    "gpt-6.1-sol":   (2.000, 10.000),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7":   (5.0, 25.0),
    "claude-haiku-4-5":  (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5":   (4.0, 20.0),
    "claude-fable-5-1":  (10.0, 50.0),
}
_DEFAULT_PRICING = (0.002, 0.008)  # fallback conservador para modelos no listados


def _price_for(model: str) -> tuple[float, float]:
    """Precio por modelo. Coincidencia exacta primero; si no, el prefijo más largo
    (los ids con fecha, p.ej. 'claude-haiku-4-5-20251001', heredan el precio base).
    La coincidencia exacta va primero para que 'gpt-4.1-mini' no se cobre como 'gpt-4.1'."""
    if model in _MODEL_PRICING:
        return _MODEL_PRICING[model]
    matches = [k for k in _MODEL_PRICING if model.startswith(k)]
    return _MODEL_PRICING[max(matches, key=len)] if matches else _DEFAULT_PRICING


# Modelos Claude que aún aceptan parámetros de muestreo (temperature). Desde Opus 4.7,
# Sonnet 5.5 y Fable, enviar temperature devuelve un 400 (skill claude-api, sept 2026):
# el cliente lo omite para el resto en vez de fallar.
_CLAUDE_SAMPLING_OK = ("claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5",
                       "claude-sonnet-4-5", "claude-opus-4-5")


def _claude_accepts_temperature(model: str) -> bool:
    """True si el modelo Claude acepta `temperature` (familias 4.5/4.6 y Haiku 4.5)."""
    return model.startswith(_CLAUDE_SAMPLING_OK)


# Modelos OpenAI de razonamiento que solo aceptan la temperatura por defecto: con 0.2 devuelven un 400
# "Unsupported value: 'temperature'" (verificado en vivo con gpt-6-luna y gpt-5.6-luna, 2026-10-10).
# Mismo patrón que _CLAUDE_SAMPLING_OK: se omite el parámetro en vez de fallar.
_OPENAI_NO_SAMPLING = ("gpt-6", "gpt-5.6")


def _openai_accepts_temperature(model: str) -> bool:
    """True si el modelo OpenAI acepta `temperature` distinta de la por defecto."""
    return not model.startswith(_OPENAI_NO_SAMPLING)


@dataclass
class RunStats:
    """Acumula métricas de uso LLM para un run completo. ADR-021."""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    # Modelo REAL que respondió cada llamada. Con fallback entre proveedores puede no
    # ser el que se pidió: es la única forma de que el informe diga la verdad (ADR-032).
    calls_by_model: dict[str, int] = field(default_factory=dict)
    # El harness de evals (H2-1) llama al LLM desde varios hilos: las sumas se protegen con un lock.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Acumula tokens, coste y recuento por modelo de una llamada individual (thread-safe)."""
        pricing = _price_for(model)
        call_cost = (prompt_tokens * pricing[0] + completion_tokens * pricing[1]) / 1_000_000
        with self._lock:
            self.tokens_in += prompt_tokens
            self.tokens_out += completion_tokens
            self.cost_usd += call_cost
            self.calls += 1
            self.calls_by_model[model] = self.calls_by_model.get(model, 0) + 1

log = structlog.get_logger()

# Errores tipados en módulo propio (ver llm/errors.py: identidad de clase estable ante reload).
from due_diligence.llm.errors import (  # noqa: E402  re-export para compatibilidad
    LLMError, LLMProviderError, LLMSchemaError, LLMTruncatedError,
)


def _provider_error(provider: str, exc: Exception) -> LLMProviderError:
    """Traduce un error de SDK a LLMProviderError y decide si merece fallback.

    Sin status_code = conexión/timeout (transitorio). 401/403 = este proveedor es
    inutilizable, el otro puede funcionar. 408/409/429/5xx = transitorio.
    Otros 4xx (400/404/422) = petición mal formada → bug nuestro, no fallback.
    """
    status = getattr(exc, "status_code", None)
    eligible = status is None or status in (401, 403, 408, 409, 429) or status >= 500
    return LLMProviderError(f"{provider} [{status or 'connection'}]: {exc}",
                            provider=provider, fallback_eligible=eligible)


class LLMClient:
    """Wrapper unificado para Anthropic Claude y OpenAI GPT."""

    def __init__(self) -> None:
        self._anthropic = None
        self._openai = None
        self._run_stats: RunStats = RunStats()
        # Modelo que respondió la ÚLTIMA llamada (con fallback puede diferir del pedido).
        # Los agentes lo copian a report.model_used para que el informe diga la verdad.
        self.last_model_used: Optional[str] = None

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
        temperature: float = 0.2,
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
            temperature: Temperatura de sampling (default: 0.2). Bajo para máximo
                         instruction-following y determinismo — los agentes de este sistema
                         hacen JSON estructurado y role-identification, no escritura creativa.
                         T=0.2 previene reasoning drift sin los artefactos de T=0 exacto.

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
            return self._complete_openai(messages, system, max_tokens, model=oai_model, response_format=response_format, temperature=temperature)
        try:
            result = self._complete_anthropic(messages, model, system, max_tokens, temperature=temperature)
            log.debug("llm_complete", provider="anthropic", model=model, temperature=temperature)
            return result
        except Exception as e:  # legacy: structured() es el camino fail-loud (ADR-032)
            log.warning("anthropic_failed_falling_back", error=str(e), model=model)
            return self._complete_openai(messages, system, max_tokens, model=model, response_format=response_format, temperature=temperature)

    def _complete_anthropic(
        self, messages: list[dict], model: str, system: Optional[str], max_tokens: int,
        temperature: float = 0.2,
    ) -> str:
        """Llamada a la API de Anthropic Claude."""
        client = self._get_anthropic()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        # Opus 4.7+/Sonnet 5.5/Fable rechazan temperature con un 400: solo se envía donde se acepta.
        if _claude_accepts_temperature(model):
            kwargs["temperature"] = temperature
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        # Antes las llamadas a Claude no se contabilizaban (coste ≈ 0 en el informe).
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._record_usage(model, usage.input_tokens, usage.output_tokens)
        return next((b.text for b in response.content if getattr(b, "type", "text") == "text"), "")

    def _complete_openai(
        self, messages: list[dict], system: Optional[str], max_tokens: int,
        model: Optional[str] = None,
        response_format: Optional[dict] = None,
        temperature: float = 0.2,
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
        if _openai_accepts_temperature(resolved_model):
            create_kwargs["temperature"] = temperature
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
            self._record_usage(resolved_model, response.usage.prompt_tokens,
                               response.usage.completion_tokens)
        return choice.message.content

    def _record_usage(self, model: str, tokens_in: int, tokens_out: int) -> None:
        """Único punto de contabilidad (ADR-021): RunStats + last_model_used + log DEBUG."""
        self._run_stats.add(model=model, prompt_tokens=tokens_in, completion_tokens=tokens_out)
        self.last_model_used = model
        log.debug("llm_tokens", model=model, in_tokens=tokens_in, out_tokens=tokens_out)

    # ───────────────────── Salida estructurada (ADR-032) ─────────────────────
    def structured(self, schema: type[T], messages: list[dict], *, system: Optional[str] = None,
                   model: Optional[str] = None, max_tokens: int = 4096,
                   temperature: float = 0.2) -> T:
        """Llama al LLM y devuelve una instancia validada de `schema` (ADR-032).

        Enruta por prefijo de modelo: 'claude*' → Anthropic (si hay key); el resto → OpenAI.
        Fallback al otro proveedor SOLO ante LLMProviderError(fallback_eligible=True)
        (red, 401/403/408/409/429/5xx). Truncación, schema inválido y refusal se propagan
        tal cual: fail loud (invariante 5). Nunca devuelve un objeto "de relleno".
        """
        model = model or settings.default_model
        if model.startswith("claude") and settings.anthropic_api_key:
            primary = "anthropic"
        else:
            primary = "openai"
            if model.startswith("claude"):
                # Sin key de Anthropic: mismo comportamiento que complete() — modelo OpenAI de fallback.
                model = settings.openai_fallback_model
        try:
            return self._structured_call(primary, schema, messages, system, model, max_tokens, temperature)
        except LLMProviderError as e:
            if not e.fallback_eligible:
                raise
            secondary, alt_model = self._fallback_target(primary)
            if secondary is None:
                raise
            log.warning("llm_structured_fallback", from_provider=primary, to_provider=secondary,
                        model=alt_model, error=str(e))
            return self._structured_call(secondary, schema, messages, system, alt_model, max_tokens, temperature)

    def _fallback_target(self, primary: str) -> tuple[Optional[str], Optional[str]]:
        """Proveedor y modelo alternativos. OpenAI→Anthropic solo si hay key y el default es Claude."""
        if primary == "anthropic":
            return "openai", settings.openai_fallback_model
        if settings.anthropic_api_key and settings.default_model.startswith("claude"):
            return "anthropic", settings.default_model
        return None, None

    def _structured_call(self, provider, schema, messages, system, model, max_tokens, temperature):
        """Despacha al método de cada proveedor."""
        if provider == "anthropic":
            return self._structured_anthropic(schema, messages, system, model, max_tokens, temperature)
        return self._structured_openai(schema, messages, system, model, max_tokens, temperature)

    def _structured_openai(self, schema, messages, system, model, max_tokens, temperature):
        """OpenAI Structured Outputs (json_schema strict, generado por el SDK desde `schema`)."""
        import openai
        import pydantic
        client = self._get_openai()
        oai_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)
        try:
            sampling = {"temperature": temperature} if _openai_accepts_temperature(model) else {}
            completion = client.chat.completions.parse(
                model=model, messages=oai_messages, response_format=schema,
                max_completion_tokens=max_tokens, **sampling,
            )
        except openai.LengthFinishReasonError as e:
            # La llamada se cobró aunque falle: contabilizar antes de lanzar.
            usage = getattr(e.completion, "usage", None)
            if usage:
                self._record_usage(model, usage.prompt_tokens, usage.completion_tokens)
            raise LLMTruncatedError(f"{model}: output truncated at max_tokens={max_tokens}") from e
        except openai.ContentFilterFinishReasonError as e:
            raise LLMSchemaError(f"{model}: output blocked by content filter") from e
        except pydantic.ValidationError as e:
            raise LLMSchemaError(f"{model}: output does not match {schema.__name__} "
                                 f"({e.error_count()} errors)") from e
        except openai.APIError as e:   # NO captura Length/ContentFilter (heredan de OpenAIError)
            raise _provider_error("openai", e) from e
        if completion.usage:
            self._record_usage(model, completion.usage.prompt_tokens, completion.usage.completion_tokens)
        message = completion.choices[0].message
        if message.refusal:
            raise LLMSchemaError(f"{model}: refusal: {str(message.refusal)[:200]}")
        if message.parsed is None:
            raise LLMSchemaError(f"{model}: empty parsed output for {schema.__name__}")
        return message.parsed

    def _structured_anthropic(self, schema, messages, system, model, max_tokens, temperature):
        """Anthropic structured outputs vía output_config.format (json_schema).

        Uso messages.create y no messages.parse: parse valida dentro de su parser
        (anthropic/lib/_parse/_response.py:16-20) ANTES de que podamos leer stop_reason,
        y una truncación en max_tokens aparecería como ValidationError. Necesitamos
        distinguir LLMTruncatedError de LLMSchemaError, así que validamos nosotros.
        """
        import anthropic
        import pydantic
        client = self._get_anthropic()
        kwargs: dict = {
            "model": model, "max_tokens": max_tokens, "messages": messages,
            "output_config": {"format": {
                "type": "json_schema",
                "schema": anthropic.transform_schema(schema.model_json_schema()),
            }},
        }
        if _claude_accepts_temperature(model):
            kwargs["temperature"] = temperature
        if system:
            kwargs["system"] = system
        try:
            response = client.messages.create(**kwargs)
        except anthropic.APIError as e:
            raise _provider_error("anthropic", e) from e
        self._record_usage(model, response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "max_tokens":
            raise LLMTruncatedError(f"{model}: output truncated at max_tokens={max_tokens}")
        if response.stop_reason == "refusal":
            raise LLMSchemaError(f"{model}: refusal")
        # Con thinking activo (Opus 5.5/Fable), el primer bloque puede ser de razonamiento.
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
        try:
            return schema.model_validate_json(text)
        except pydantic.ValidationError as e:
            raise LLMSchemaError(f"{model}: output does not match {schema.__name__} "
                                 f"({e.error_count()} errors)") from e


_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
