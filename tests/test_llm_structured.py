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
    assert s.cost_usd == pytest.approx(0.40)      # precio oficial (antes la tabla decía 0,20)


def test_temperature_only_sent_to_claude_models_that_accept_it():
    """Opus 4.7+/Sonnet 5.5/Fable devuelven 400 si se envía temperature (skill claude-api, 2026-09)."""
    from due_diligence.llm.client import _claude_accepts_temperature

    assert not _claude_accepts_temperature("claude-opus-5-5")
    assert _claude_accepts_temperature("claude-sonnet-4-6")


def test_new_openai_models_have_official_prices():
    s = RunStats()
    s.add("gpt-6-luna", 1_000_000, 1_000_000)
    s.add("gpt-5.6-luna", 1_000_000, 1_000_000)
    # sin caché, la entrada de las generaciones 5.6/6 se factura como "cache write" (0,125 y 0,25)
    assert s.cost_usd == pytest.approx(0.125 + 0.50 + 0.25 + 1.20)


def test_runstats_cached_input_is_priced_at_the_cached_rate():
    """Nuestro contador sobreestimaba un 27 % frente a la factura: ignoraba la caché (2026-10-10)."""
    s = RunStats()
    s.add("gpt-5.4-mini", 1_000_000, 0, cached_tokens=600_000)
    assert s.cost_usd == pytest.approx(0.4 * 0.75 + 0.6 * 0.075)
    assert s.tokens_cached == 600_000


def test_runstats_cache_write_rate_for_new_generation_models():
    # gpt-6.1-sol cobra la entrada no cacheada como "cache write" (2,50 $) — panel de OpenAI, 2026-10-10
    s = RunStats()
    s.add("gpt-6.1-sol", 1_000_000, 0, cached_tokens=0)
    assert s.cost_usd == pytest.approx(2.50)
    s2 = RunStats()
    s2.add("gpt-6.1-sol", 1_000_000, 1_000_000, cached_tokens=1_000_000)
    assert s2.cost_usd == pytest.approx(0.10 + 10.0)
