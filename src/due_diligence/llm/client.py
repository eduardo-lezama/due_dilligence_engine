from typing import Optional
import structlog

from due_diligence.config import settings

log = structlog.get_logger()


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        self._anthropic = None
        self._openai = None

    def _get_anthropic(self):
        if self._anthropic is None:
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._anthropic

    def _get_openai(self):
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
        model = model or settings.default_model
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
        client = self._get_anthropic()
        kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text

    def _complete_openai(
        self, messages: list[dict], system: Optional[str], max_tokens: int
    ) -> str:
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
