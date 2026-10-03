"""
Tests de LLMClient.structured() por proveedor (ADR-032, H1 T1b/T1c).

Sin red: los clientes SDK se inyectan como fakes en `client._openai` / `client._anthropic`.
Cubren: salida tipada, contabilidad por modelo, truncación, refusal, schema inválido,
elegibilidad de fallback y particularidades de Claude (temperature, bloques no-texto).
"""
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict

import due_diligence.config as cfg
from due_diligence.llm.client import LLMClient
from due_diligence.llm.errors import LLMProviderError, LLMSchemaError, LLMTruncatedError


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str


def _oai_completion(parsed, refusal=None, pt=100, ct=20):
    msg = MagicMock(parsed=parsed, refusal=refusal)
    return MagicMock(choices=[MagicMock(message=msg)],
                     usage=MagicMock(prompt_tokens=pt, completion_tokens=ct))


def _ant_message(text, stop="end_turn", it=50, ot=10):
    return MagicMock(stop_reason=stop, content=[MagicMock(type="text", text=text)],
                     usage=MagicMock(input_tokens=it, output_tokens=ot))


@pytest.fixture
def no_anthropic(monkeypatch):
    monkeypatch.setattr(cfg.settings, "anthropic_api_key", "")


@pytest.fixture
def with_anthropic(monkeypatch):
    monkeypatch.setattr(cfg.settings, "anthropic_api_key", "sk-test")


def _client_with(openai_fake=None, anthropic_fake=None) -> LLMClient:
    c = LLMClient()
    c._openai = openai_fake      # los getters lazy devuelven el fake inyectado
    c._anthropic = anthropic_fake
    return c


# ───────────────────────── T1b: OpenAI ─────────────────────────
def test_structured_openai_returns_model_and_records_usage(no_anthropic):
    fake = MagicMock()
    fake.chat.completions.parse.return_value = _oai_completion(_Out(answer="ok"))
    c = _client_with(openai_fake=fake)
    out = c.structured(_Out, [{"role": "user", "content": "hi"}], system="sys", model="gpt-4.1")
    assert out == _Out(answer="ok")
    kw = fake.chat.completions.parse.call_args.kwargs
    assert kw["response_format"] is _Out
    assert kw["messages"][0] == {"role": "system", "content": "sys"}
    assert kw["max_completion_tokens"] == 4096
    stats = c.get_run_stats()
    assert stats.calls_by_model == {"gpt-4.1": 1} and stats.tokens_in == 100
    assert c.last_model_used == "gpt-4.1"


def test_structured_claude_model_without_key_routes_to_openai_fallback_model(no_anthropic):
    fake = MagicMock()
    fake.chat.completions.parse.return_value = _oai_completion(_Out(answer="ok"))
    c = _client_with(openai_fake=fake)
    c.structured(_Out, [{"role": "user", "content": "hi"}], model="claude-sonnet-4-6")
    assert fake.chat.completions.parse.call_args.kwargs["model"] == cfg.settings.openai_fallback_model


def test_structured_openai_truncation_raises_and_counts_usage(no_anthropic):
    fake = MagicMock()
    trunc = MagicMock(usage=MagicMock(prompt_tokens=10, completion_tokens=4096))
    fake.chat.completions.parse.side_effect = openai.LengthFinishReasonError(completion=trunc)
    c = _client_with(openai_fake=fake)
    with pytest.raises(LLMTruncatedError):
        c.structured(_Out, [{"role": "user", "content": "hi"}], model="gpt-4.1")
    assert c.get_run_stats().tokens_out == 4096


def test_structured_openai_refusal_is_schema_error(no_anthropic):
    fake = MagicMock()
    fake.chat.completions.parse.return_value = _oai_completion(None, refusal="I can't help")
    with pytest.raises(LLMSchemaError):
        _client_with(openai_fake=fake).structured(_Out, [{"role": "user", "content": "x"}], model="gpt-4.1")


def test_structured_openai_validation_error_is_schema_error(no_anthropic):
    fake = MagicMock()

    def boom(**_):
        _Out.model_validate_json("{}")   # lanza pydantic.ValidationError real
    fake.chat.completions.parse.side_effect = boom
    with pytest.raises(LLMSchemaError):
        _client_with(openai_fake=fake).structured(_Out, [{"role": "user", "content": "x"}], model="gpt-4.1")


def test_structured_openai_400_is_not_fallback_eligible(with_anthropic):
    fake_oai, fake_ant = MagicMock(), MagicMock()
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    fake_oai.chat.completions.parse.side_effect = openai.BadRequestError(
        "invalid schema", response=httpx.Response(400, request=req), body=None)
    c = _client_with(openai_fake=fake_oai, anthropic_fake=fake_ant)
    with pytest.raises(LLMProviderError) as ei:
        c.structured(_Out, [{"role": "user", "content": "x"}], model="gpt-4.1")
    assert ei.value.fallback_eligible is False
    fake_ant.messages.create.assert_not_called()


# ─────────────────────── T1c: Anthropic + fallback ───────────────────────
def test_structured_anthropic_uses_output_config_and_records_usage(with_anthropic):
    fake = MagicMock()
    fake.messages.create.return_value = _ant_message('{"answer": "ok"}')
    c = _client_with(anthropic_fake=fake, openai_fake=MagicMock())
    out = c.structured(_Out, [{"role": "user", "content": "x"}], model="claude-sonnet-4-6")
    assert out.answer == "ok"
    fmt = fake.messages.create.call_args.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert c.get_run_stats().calls_by_model == {"claude-sonnet-4-6": 1}
    assert c.get_run_stats().cost_usd == pytest.approx((50 * 3 + 10 * 15) / 1e6)


def test_structured_anthropic_omits_temperature_for_models_that_reject_it(with_anthropic):
    fake = MagicMock()
    fake.messages.create.return_value = _ant_message('{"answer": "ok"}')
    c = _client_with(anthropic_fake=fake, openai_fake=MagicMock())
    c.structured(_Out, [{"role": "user", "content": "x"}], model="claude-opus-5-5")
    assert "temperature" not in fake.messages.create.call_args.kwargs


def test_structured_anthropic_max_tokens_is_truncation_no_fallback(with_anthropic):
    fake_ant, fake_oai = MagicMock(), MagicMock()
    fake_ant.messages.create.return_value = _ant_message('{"answ', stop="max_tokens")
    c = _client_with(anthropic_fake=fake_ant, openai_fake=fake_oai)
    with pytest.raises(LLMTruncatedError):
        c.structured(_Out, [{"role": "user", "content": "x"}], model="claude-sonnet-4-6")
    fake_oai.chat.completions.parse.assert_not_called()


def test_structured_anthropic_invalid_json_is_schema_error_no_fallback(with_anthropic):
    fake_ant, fake_oai = MagicMock(), MagicMock()
    fake_ant.messages.create.return_value = _ant_message('{"wrong": 1}')
    c = _client_with(anthropic_fake=fake_ant, openai_fake=fake_oai)
    with pytest.raises(LLMSchemaError):
        c.structured(_Out, [{"role": "user", "content": "x"}], model="claude-sonnet-4-6")
    fake_oai.chat.completions.parse.assert_not_called()


def test_structured_anthropic_rate_limit_falls_back_to_openai(with_anthropic):
    fake_ant, fake_oai = MagicMock(), MagicMock()
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake_ant.messages.create.side_effect = anthropic.RateLimitError(
        "rate", response=httpx.Response(429, request=req), body=None)
    fake_oai.chat.completions.parse.return_value = _oai_completion(_Out(answer="oai"))
    c = _client_with(anthropic_fake=fake_ant, openai_fake=fake_oai)
    out = c.structured(_Out, [{"role": "user", "content": "x"}], model="claude-sonnet-4-6")
    assert out.answer == "oai"
    assert c.get_run_stats().calls_by_model == {cfg.settings.openai_fallback_model: 1}
    assert c.last_model_used == cfg.settings.openai_fallback_model


def test_structured_anthropic_skips_non_text_blocks(with_anthropic):
    """Review Focus #4: con thinking siempre activo, el primer bloque puede no ser texto."""
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(
        stop_reason="end_turn", usage=MagicMock(input_tokens=5, output_tokens=5),
        content=[MagicMock(type="thinking", text=None), MagicMock(type="text", text='{"answer": "ok"}')])
    c = _client_with(anthropic_fake=fake, openai_fake=MagicMock())
    assert c.structured(_Out, [{"role": "user", "content": "x"}], model="claude-sonnet-4-6").answer == "ok"
