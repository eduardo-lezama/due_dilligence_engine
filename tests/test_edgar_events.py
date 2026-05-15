"""
Tests para EdgarEventsAdapter.fetch_8k_events (ADR-017).

Qué se testea:
    - Filtra filings 8-K por item number relevante (2.02, 4.02, 5.02).
    - Ignora items no relevantes (e.g. 7.01).
    - Devuelve lista vacía si no hay filings.
    - Parsea correctamente filing_date, item_numbers, summary_text, filing_url.
    - Fallo en parse de un filing individual se omite silenciosamente.

Estrategia de aislamiento:
    edgartools Company + set_identity se mockean completamente.
    Zero llamadas reales a EDGAR.
"""

from unittest.mock import MagicMock, patch

import pytest


def _make_fake_filing(
    items: list[str],
    summary: str,
    date_str: str,
    url: str = "",
    item_texts: dict[str, str] | None = None,
) -> MagicMock:
    """Helper que construye un mock de filing 8-K con la API de edgartools.

    Args:
        item_texts: dict opcional {item_number: text} para simular obj["2.02"] etc.
            Si no se provee, obj[key] lanza KeyError (edgartools default cuando
            el item no tiene texto extraíble).
    """
    filing = MagicMock()
    filing.filing_date = date_str
    filing.filing_url = url
    filing.accession_no = "0000320193-25-000001"
    obj = MagicMock()
    obj.items = items
    obj.summary = summary

    # Simular acceso por item: obj["2.02"] → texto, KeyError si no existe
    _texts = item_texts or {}
    def _getitem(key: str) -> str:
        if key in _texts:
            return _texts[key]
        raise KeyError(key)
    obj.__getitem__ = MagicMock(side_effect=_getitem)

    # Desactivar atributos item_X_YY para no dar falsos positivos en fallback
    obj.configure_mock(**{
        f"item_{k.replace('.', '_')}": None
        for k in items
    })

    filing.obj.return_value = obj
    return filing


def test_fetch_8k_events_filters_by_item(mocker):
    """Solo devuelve filings que contienen al menos uno de los items solicitados."""
    from due_diligence.tools.edgar import EdgarEventsAdapter

    filing_relevant = _make_fake_filing(
        items=["5.02"],
        summary="Appointment of John Ternus as CEO.",
        date_str="2025-10-15",
        url="https://sec.gov/8k-ternus",
    )
    filing_irrelevant = _make_fake_filing(
        items=["7.01"],
        summary="FD disclosure — regulation FD.",
        date_str="2025-07-10",
    )

    fake_company = MagicMock()
    fake_company.get_filings.return_value = [filing_relevant, filing_irrelevant]
    mocker.patch("edgar.Company", return_value=fake_company)
    mocker.patch("edgar.set_identity")

    events = EdgarEventsAdapter.fetch_8k_events("AAPL", items=["2.02", "4.02", "5.02"], months=12)

    assert len(events) == 1
    assert events[0]["item_numbers"] == ["5.02"]
    assert "Ternus" in events[0]["summary_text"]
    assert events[0]["filing_url"] == "https://sec.gov/8k-ternus"
    assert events[0]["filing_date"] == "2025-10-15"


def test_fetch_8k_events_returns_empty_when_no_matches(mocker):
    """Sin filings con items relevantes, devuelve lista vacía."""
    from due_diligence.tools.edgar import EdgarEventsAdapter

    filing_irrelevant = _make_fake_filing(items=["7.01"], summary="FD.", date_str="2025-06-01")
    fake_company = MagicMock()
    fake_company.get_filings.return_value = [filing_irrelevant]
    mocker.patch("edgar.Company", return_value=fake_company)
    mocker.patch("edgar.set_identity")

    events = EdgarEventsAdapter.fetch_8k_events("AAPL", months=12)

    assert events == []


def test_fetch_8k_events_handles_multiple_items_in_one_filing(mocker):
    """Un filing con múltiples items relevantes incluye todos los que coinciden."""
    from due_diligence.tools.edgar import EdgarEventsAdapter

    filing = _make_fake_filing(
        items=["2.02", "9.01"],
        summary="Q4 earnings release.",
        date_str="2025-10-30",
    )
    fake_company = MagicMock()
    fake_company.get_filings.return_value = [filing]
    mocker.patch("edgar.Company", return_value=fake_company)
    mocker.patch("edgar.set_identity")

    events = EdgarEventsAdapter.fetch_8k_events("AAPL", items=["2.02", "4.02", "5.02"], months=12)

    assert len(events) == 1
    assert events[0]["item_numbers"] == ["2.02"]  # solo el que está en la lista solicitada


def test_fetch_8k_events_skips_failing_filing(mocker):
    """Un filing que falla en .obj() se omite sin abortar el resto."""
    from due_diligence.tools.edgar import EdgarEventsAdapter

    filing_bad = MagicMock()
    filing_bad.filing_date = "2025-09-01"
    filing_bad.obj.side_effect = Exception("parse error")

    filing_good = _make_fake_filing(
        items=["4.02"],
        summary="Restatement of Q2 results.",
        date_str="2025-08-01",
        url="https://sec.gov/8k-restatement",
    )
    fake_company = MagicMock()
    fake_company.get_filings.return_value = [filing_bad, filing_good]
    mocker.patch("edgar.Company", return_value=fake_company)
    mocker.patch("edgar.set_identity")

    events = EdgarEventsAdapter.fetch_8k_events("AAPL", items=["2.02", "4.02", "5.02"], months=12)

    assert len(events) == 1
    assert events[0]["item_numbers"] == ["4.02"]
    assert "Restatement" in events[0]["summary_text"]
