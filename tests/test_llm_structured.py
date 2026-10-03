"""
Tests de LLMClient.structured() y de la contabilidad de RunStats (ADR-032).

Ningún test hace red: los clientes de SDK se inyectan como fakes en
`client._openai` / `client._anthropic` (los getters lazy los devuelven tal cual).
"""
import pytest

from due_diligence.llm.client import RunStats
from due_diligence.llm.errors import LLMError, LLMProviderError, LLMSchemaError, LLMTruncatedError


def test_error_hierarchy_all_subclass_llmerror():
    for cls in (LLMProviderError, LLMSchemaError, LLMTruncatedError):
        assert issubclass(cls, LLMError)
    e = LLMProviderError("boom", provider="openai", fallback_eligible=True)
    assert e.provider == "openai" and e.fallback_eligible is True


def test_runstats_claude_pricing_dated_ids_and_calls_by_model():
    s = RunStats()
    s.add("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert s.cost_usd == pytest.approx(18.0)
    s.add("claude-haiku-4-5-20251001", 1_000_000, 0)   # sufijo de fecha → prefijo más largo
    assert s.cost_usd == pytest.approx(19.0)
    s.add("gpt-4.1", 10, 10)
    assert s.calls_by_model == {"claude-sonnet-4-6": 1, "claude-haiku-4-5-20251001": 1, "gpt-4.1": 1}


def test_runstats_exact_match_wins_over_prefix():
    # gpt-4.1-mini existe en la tabla: no debe cobrarse al precio de gpt-4.1 por prefijo
    s = RunStats()
    s.add("gpt-4.1-mini", 1_000_000, 0)
    assert s.cost_usd == pytest.approx(0.20)


def test_complete_legacy_omits_temperature_for_claude_models_that_reject_it(monkeypatch):
    """Opus 4.7+/Sonnet 5.5/Fable devuelven 400 si se envía temperature (skill claude-api, 2026-09)."""
    from unittest.mock import MagicMock
    import due_diligence.config as cfg
    from due_diligence.llm.client import LLMClient

    monkeypatch.setattr(cfg.settings, "anthropic_api_key", "sk-test")
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="ok")], usage=MagicMock(input_tokens=1, output_tokens=1))
    c = LLMClient()
    c._anthropic = fake
    c.complete([{"role": "user", "content": "x"}], model="claude-opus-5-5")
    assert "temperature" not in fake.messages.create.call_args.kwargs
    c.complete([{"role": "user", "content": "x"}], model="claude-sonnet-4-6")
    assert fake.messages.create.call_args.kwargs["temperature"] == 0.2
    assert c.get_run_stats().calls_by_model == {"claude-opus-5-5": 1, "claude-sonnet-4-6": 1}
