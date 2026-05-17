"""
Tests para el Supervisor node.

Verifican:
- Resolución de ticker → CIK (formato 10 dígitos con ceros a la izquierda).
- Extracción de as_of_date del 10-K más reciente.
- Generación de run_id único.
- Normalización del ticker a mayúsculas.
- Error explícito cuando el ticker no existe en EDGAR (fail loud, ADR-005).
- Tavily quota check: propagación de TavilyQuotaExceededError.

Todos los tests mockean edgartools.Company — zero llamadas reales a EDGAR.
El conftest.py auto-mockea check_tavily_quota() para evitar llamadas HTTP reales.
"""

import pytest

# Capturar la función real antes de que el autouse fixture del conftest la parchee.
# El autouse reemplaza el atributo del módulo; esta referencia apunta al objeto
# función original y no se ve afectada por el patch.
from due_diligence.tools.tavily import check_tavily_quota as _real_check_tavily_quota


def test_supervisor_resolves_cik(mocker):
    """Resuelve ticker a CIK con formato 10 dígitos (zfill)."""
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193                          # entero — como lo devuelve edgartools
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    result = supervisor_node({"ticker": "AAPL"})

    assert result["cik"] == "0000320193"               # zfill(10): 320193 → "0000320193"
    assert result["as_of_date"] == "2024-09-28"
    assert "run_id" in result
    assert len(result["run_id"]) > 0


def test_supervisor_uppercases_ticker(mocker):
    """Ticker en minúsculas se normaliza a mayúsculas antes de llamar a EDGAR."""
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    result = supervisor_node({"ticker": "aapl"})
    assert result["cik"] == "0000320193"


def test_supervisor_invalid_ticker_raises(mocker):
    """Ticker inexistente en EDGAR → ValueError con el ticker en el mensaje (fail loud)."""
    mocker.patch(
        "due_diligence.agents.supervisor.Company",
        side_effect=Exception("company not found"),
    )

    from due_diligence.agents.supervisor import supervisor_node

    with pytest.raises(ValueError, match="INVALID_TICKER"):
        supervisor_node({"ticker": "INVALID_TICKER"})


def test_supervisor_foreign_filer_20f_detected(mocker):
    """
    EC-36: foreign private issuer (NMM-like) sin 10-K pero con 20-F debe fallar
    con DataUnavailableError educativo, NO con AttributeError críptico.
    """
    from due_diligence.tools.edgar import DataUnavailableError

    mock_company = mocker.MagicMock()
    mock_company.cik = 1415921
    mock_company.name = "Navios Maritime Partners L.P."
    mock_company.industry = "Deep Sea Foreign Transportation of Freight"

    # 10-K query returns .latest() == None (foreign filer no presenta 10-K)
    mock_10k_filings = mocker.MagicMock()
    mock_10k_filings.latest.return_value = None
    # 20-F query returns a real filing (the foreign filer's annual report)
    mock_20f_filings = mocker.MagicMock()
    mock_20f_latest = mocker.MagicMock()
    mock_20f_latest.filing_date = "2026-03-12"
    mock_20f_latest.period_of_report = "2025-12-31"
    mock_20f_filings.latest.return_value = mock_20f_latest

    def get_filings_side_effect(form=None):
        if form == "10-K":
            return mock_10k_filings
        if form == "20-F":
            return mock_20f_filings
        return mocker.MagicMock()

    mock_company.get_filings.side_effect = get_filings_side_effect
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    with pytest.raises(DataUnavailableError) as exc_info:
        supervisor_node({"ticker": "NMM"})

    error_msg = str(exc_info.value)
    assert "foreign private issuer" in error_msg
    assert "20-F" in error_msg
    assert "Phase 3" in error_msg or "MCE-13" in error_msg
    assert "2026-03-12" in error_msg  # filing date helpful for user


def test_supervisor_no_10k_no_20f_distinct_message(mocker):
    """
    EC-36: ticker existe en EDGAR pero sin 10-K NI 20-F (private/SPAC/delisted)
    debe fallar con DataUnavailableError distinto al de foreign filer.
    """
    from due_diligence.tools.edgar import DataUnavailableError

    mock_company = mocker.MagicMock()
    mock_company.cik = 9999999

    # Ni 10-K ni 20-F disponibles
    mock_empty_filings = mocker.MagicMock()
    mock_empty_filings.latest.return_value = None
    mock_company.get_filings.return_value = mock_empty_filings

    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    with pytest.raises(DataUnavailableError) as exc_info:
        supervisor_node({"ticker": "SHELL"})

    error_msg = str(exc_info.value)
    # Mensaje distinto al de foreign filer — categoría diferente
    assert "private company" in error_msg or "delisted" in error_msg or "SPAC" in error_msg
    assert "foreign private issuer" not in error_msg  # NO confundir con foreign filer


# Tests: Tavily quota check (EC — rate limit guard)

def test_supervisor_propagates_tavily_quota_exceeded(mocker):
    """
    Si check_tavily_quota() lanza TavilyQuotaExceededError, supervisor_node la propaga
    sin consumir EDGAR ni ninguna otra API. El conftest auto-mock es sobreescrito aquí.
    """
    from due_diligence.tools.tavily import TavilyQuotaExceededError

    mocker.patch(
        "due_diligence.tools.tavily.check_tavily_quota",
        side_effect=TavilyQuotaExceededError("960/1000 credits used, 40 remaining"),
    )
    # Company NO debe ser llamado — la excepción ocurre antes
    mock_company = mocker.patch("due_diligence.agents.supervisor.Company")

    from due_diligence.agents.supervisor import supervisor_node

    with pytest.raises(TavilyQuotaExceededError, match="960/1000"):
        supervisor_node({"ticker": "AAPL"})

    mock_company.assert_not_called()


def test_supervisor_continues_when_quota_ok(mocker):
    """
    Cuando check_tavily_quota() retorna sin error, supervisor_node completa con normalidad.
    El conftest ya provee este mock como no-op, pero este test lo hace explícito.
    """
    mocker.patch("due_diligence.tools.tavily.check_tavily_quota")  # no-op explícito

    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    result = supervisor_node({"ticker": "AAPL"})
    assert result["cik"] == "0000320193"
    assert result["ticker"] == "AAPL"


def test_check_tavily_quota_raises_when_nearly_exhausted(mocker):
    """
    check_tavily_quota() lanza TavilyQuotaExceededError cuando credits_used >= limit - buffer.
    Buffer default=40: con 960/1000 usados queda solo 40 → debe lanzar.

    Usa _real_check_tavily_quota (capturada a nivel de módulo antes del autouse del conftest)
    para acceder a la implementación real sin que el autouse mock interfiera.
    """
    import json
    import due_diligence.config as config_module
    from due_diligence.tools.tavily import TavilyQuotaExceededError

    mock_resp = mocker.MagicMock()
    mock_resp.__enter__ = mocker.MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = mocker.MagicMock(return_value=False)
    mock_resp.read.return_value = json.dumps(
        {"usage": {"credits_used": 960, "credits_limit": 1000}}
    ).encode()
    mocker.patch("due_diligence.tools.tavily.urllib.request.urlopen", return_value=mock_resp)
    mocker.patch.object(config_module.settings, "tavily_api_key", "tvly-test-key")

    # _real_check_tavily_quota apunta al objeto función original (capturado antes del autouse)
    with pytest.raises(TavilyQuotaExceededError, match="960/1000"):
        _real_check_tavily_quota(buffer=40)
