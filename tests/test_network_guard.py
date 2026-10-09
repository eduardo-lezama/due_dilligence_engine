"""
Guard de red de los tests (EC-89, EC-93): ningún test puede llegar a SEC EDGAR.

En la tarea EC-93 se vio que los tests del agente de management descargaban filings reales
(2 s por test; con la red cortada seguían pasando porque el adaptador degrada en silencio).
"""
import pytest


def test_edgar_company_is_blocked_by_default():
    import edgar
    with pytest.raises(RuntimeError, match="network"):
        edgar.Company("AAPL")


@pytest.mark.parametrize("target", ["finnhub.Client", "polygon.RESTClient"])
def test_market_data_clients_are_blocked_by_default(target):
    import importlib
    module_name, attr = target.split(".")
    with pytest.raises(RuntimeError, match="network"):
        getattr(importlib.import_module(module_name), attr)(api_key="x")
