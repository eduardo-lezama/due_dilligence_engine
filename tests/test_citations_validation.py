"""Tests del validador de citas (ADR-005 implementado, ADR-032, EC-69).

Qué se testea:
    - normalize_text: comillas/guiones tipográficos, mayúsculas y espacios.
    - validate_citations: substring normalizado, fuzzy (partial_ratio ≥ 90), números en la
      ventana alineada, excerpt demasiado corto, corpus vacío.
    - value_supported_by_excerpt: escalas ×1/×1e3/×1e6/×1e9 y porcentajes.
    - gate_llm_value: un número del LLM solo pasa con cita verificada que lo contenga.
Sin red ni LLM: funciones puras.
"""
from due_diligence.schemas.base import Citation
from due_diligence.schemas.llm_outputs import CitationOut
from due_diligence.validation.citations import (
    gate_llm_value,
    normalize_text,
    numeric_tokens,
    validate_citations,
    value_supported_by_excerpt,
)

CORPUS = {"10-K Item 7": "On March 3, 2025 the CEO purchased 10,000 shares at $25.50 per share. "
                         "Backlog was $1.27 billion at year end, up 12% year‑over‑year."}


def _cit(excerpt, conf=0.8):
    return Citation(claim="c", source_url="10-K", excerpt=excerpt, confidence=conf)


def test_normalize_handles_quotes_dashes_case_whitespace():
    assert normalize_text("  “Hello” —  World ") == '"hello" - world'


def test_numeric_tokens_normalizes_separators_and_trailing_zeros():
    assert numeric_tokens("10,000 shares at $25.50 and 3.0x") == {"10000", "25.5", "3"}


def test_exact_excerpt_verified():
    [c] = validate_citations([_cit("the CEO purchased 10,000 shares at $25.50 per share")], CORPUS)
    assert c.verified is True and c.confidence == 0.8 and c.verification_note is None


def test_fuzzy_excerpt_with_minor_drift_verified():
    [c] = validate_citations([_cit("The CEO  purchased 10,000 shares at $25.50 a share")], CORPUS)
    assert c.verified is True


def test_altered_number_rejected_even_if_fuzzy_matches():
    [c] = validate_citations([_cit("the CEO purchased 100,000 shares at $25.50 per share")], CORPUS)
    assert c.verified is False and c.confidence == 0.0
    assert "100000" in c.verification_note


def test_invented_excerpt_rejected():
    [c] = validate_citations([_cit("management guided to 20% margin expansion next year")], CORPUS)
    assert c.verified is False and "not found" in c.verification_note


def test_too_short_excerpt_rejected():
    [c] = validate_citations([_cit("10,000")], CORPUS)
    assert c.verified is False and "too short" in c.verification_note


def test_empty_corpus_marks_all_unverified_with_note():
    cits = validate_citations([_cit("the CEO purchased 10,000 shares at $25.50 per share")],
                              {"DEF 14A": "", "Form 4": "   "})
    assert all(not c.verified and "empty corpus" in c.verification_note for c in cits)


def test_value_supported_by_excerpt_scales():
    assert value_supported_by_excerpt(1.27e9, "Backlog was $1.27 billion at year end")
    assert value_supported_by_excerpt(1.27e9, "Backlog (in millions) $1,270")
    assert not value_supported_by_excerpt(2.5e9, "Backlog was $1.27 billion at year end")
    assert value_supported_by_excerpt(12.5, "insiders own 12.5% of outstanding shares")


def _cout(excerpt):
    return CitationOut(claim="c", source_url="10-K", excerpt=excerpt, confidence=0.9,
                       gaap_vs_nongaap_flag=None)


def test_gate_llm_value_requires_verified_citation_containing_value():
    ok_val, ok_cit = gate_llm_value(1.27e9, _cout("Backlog was $1.27 billion at year end"), CORPUS, numeric=True)
    assert ok_val == 1.27e9 and ok_cit.verified is True
    none_val, none_cit = gate_llm_value(1.27e9, None, CORPUS, numeric=True)
    assert none_val is None and none_cit is None
    bad_val, bad_cit = gate_llm_value(2.5e9, _cout("Backlog was $1.27 billion at year end"), CORPUS, numeric=True)
    assert bad_val is None and bad_cit.verified is False and "value not present" in bad_cit.verification_note
    inv_val, inv_cit = gate_llm_value(1.27e9, _cout("Backlog reached a record $1.27 billion in Q4"), CORPUS,
                                      numeric=True)
    assert inv_val is None and inv_cit.verified is False
