"""Tests de tools/tenk_sections (EC-106): localizar las secciones 1, 1A y 7 en el texto completo del 10-K."""
from due_diligence.tools.tenk_sections import (MIN_SECTION_CHARS, TRUNCATION_TAG, extract_section,
                                                truncate_visibly)

_BODY = "Real business text. " * 60          # > MIN_SECTION_CHARS
_RISK = "Real risk text. " * 60
_MDA = "Real MD&A text. " * 60

# Estructura típica: índice al principio (encabezados seguidos), cuerpo, y una referencia cruzada
# al final que repite el encabezado del 1A con muy poco texto (el caso de JNJ).
_DOC = f"""TABLE OF CONTENTS
Item 1. Business
Item 1A. Risk Factors
Item 7. Management's Discussion and Analysis
Item 8. Financial Statements

PART I
Item 1. Business
{_BODY}
As discussed in Item 1A. Risk Factors, our results may vary.
Item 1A. Risk Factors
{_RISK}
Item 1B. Unresolved Staff Comments
None.
Item 7. Management's Discussion and Analysis of Financial Condition
{_MDA}
Item 7A. Quantitative and Qualitative Disclosures
Item 8. Financial Statements
CROSS REFERENCE INDEX
Item 1A. Risk Factors
page 12
Item 2. Properties
"""


def test_extracts_the_body_not_the_table_of_contents_or_cross_reference():
    assert extract_section(_DOC, "1").startswith("Real business text.")
    assert extract_section(_DOC, "1A").startswith("Real risk text.")
    assert "Real MD&A text." in extract_section(_DOC, "7")[:80]


def test_prose_reference_does_not_end_the_section_early():
    # "As discussed in Item 1A. Risk Factors" va en mitad de una línea: no es un encabezado.
    assert "our results may vary" in extract_section(_DOC, "1")


def test_short_or_unknown_sections_return_empty():
    assert extract_section("Item 1. Business\nshort\nItem 1A. Risk Factors\n", "1") == ""
    assert extract_section(_DOC, "9") == ""
    assert extract_section("", "1") == ""


def test_header_without_a_following_section_is_discarded():
    # Sin encabezado siguiente el segmento iría hasta el final del documento (formato del 10-K de CALM).
    assert extract_section("Item 1. Business\n" + _BODY, "1") == ""


def test_truncation_is_visible():
    text, cut = truncate_visibly("x" * 100, 40)
    assert cut and text.startswith("x" * 40) and TRUNCATION_TAG in text and "100" in text
    assert truncate_visibly("short", 40) == ("short", False)
    assert MIN_SECTION_CHARS == 500


class _FakeDoc:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class _FakeFiling:
    def __init__(self, text):
        self.document = _FakeDoc(text)


def test_edgar_section_text_falls_back_to_full_text_when_parser_is_empty():
    # EC-106 (KO, JNJ): edgartools devuelve la sección vacía aunque el texto completo existe.
    from due_diligence.tools.edgar import _section_text
    out = _section_text(_FakeFiling(_DOC), "KO", "1A", primary="", max_chars=60_000)
    assert out.startswith("Real risk text.")


def test_edgar_section_text_prefers_parser_and_marks_truncation():
    from due_diligence.tools.edgar import _section_text
    primary = "Parser text. " * 100
    out = _section_text(_FakeFiling(_DOC), "AAPL", "1", primary=primary, max_chars=600)
    assert out.startswith("Parser text.") and TRUNCATION_TAG in out
    assert _section_text(_FakeFiling(""), "X", "7", primary="", max_chars=600) == ""


def test_missing_or_truncated_sections_reach_the_expert_data_gaps():
    from due_diligence.agents.report_facts import filings_coverage_gaps
    from due_diligence.schemas.reports import FilingsRAGReport
    rep = FilingsRAGReport(ticker="KO", stub=False, sections_missing=["Item 7"], sections_truncated=["Item 1A"])
    reasons = [r for _, r in filings_coverage_gaps(rep)]
    assert any("Item 7" in r and "not found" in r for r in reasons)
    assert any("Item 1A" in r and "truncated" in r for r in reasons)
    assert filings_coverage_gaps(FilingsRAGReport(ticker="KO", stub=False)) == []


def test_prose_reference_at_the_start_of_a_line_does_not_end_the_section():
    # Revisión del bloque B (#2): "Item 1A of this report…" empieza línea pero no lleva título.
    doc = ("Item 1. Business\n" + _BODY + "\nItem 1A of this report describes our risks.\n" + _BODY
           + "\nItem 1A. Risk Factors\n" + _RISK + "\nItem 1B. Unresolved Staff Comments\n")
    body = extract_section(doc, "1")
    assert "describes our risks" in body and body.count("Real business text.") == 120
