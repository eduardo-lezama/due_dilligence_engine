"""Guard: todo campo de los reports especialistas tiene un consumidor o una excusa documentada.

Por qué existe (EC-70, H1-T4c): `governance_red_flags` se extrajo en EC-66 y ningún
consumidor lo leyó durante meses. Un campo sin consumidor es coste sin valor y una falsa
sensación de cobertura. Este test falla en cuanto un productor añade un campo que ni el
Value Expert, ni el Critic, ni el archiver, ni la CLI leen — salvo que se justifique en
`_ALLOWLIST` con un motivo explícito.

Cobertura (ruling R6): FilingsRAGReport, ManagementReport, CompetitiveReport,
FinancialsReport y CriticReport. ValuationReport lo añade el bloque C tras el merge.
"""
import inspect
import re

import pytest

from due_diligence import cli
from due_diligence.agents import archiver, critic, value_expert
from due_diligence.schemas.base import FinancialsReport
from due_diligence.schemas.reports import (
    CompetitiveReport,
    CriticReport,
    FilingsRAGReport,
    ManagementReport,
)

# Consumidores: los cuatro módulos que convierten datos en decisión o en informe.
_CONSUMER_SRC = (inspect.getsource(value_expert) + inspect.getsource(critic)
                 + inspect.getsource(archiver) + inspect.getsource(cli))

_FCFF_BLOCK_C = "insumo de la valoración FCFF/bridge/WACC (T10–T13, bloque C, en paralelo)"

_ALLOWLIST = {
    # --- metadatos / control de flujo ---
    "ticker": "identificador, no contenido analítico",
    "stub": "flag de control de flujo",
    "error": "consumido genéricamente vía getattr en archiver.collect_failures",
    "model_used": "metadato de trazabilidad por report; el pie 'Models used' usa el agregado RunStats.calls_by_model",
    "cik": "identificador (lo usa supervisor/edgar)",
    "citations": "consumido vía bucle genérico en archiver._render_citations_section y filtrado por verified en value_expert",
    # --- FinancialsReport: consumidos por productores de otros reports, no por los consumidores finales ---
    "shares_outstanding": "consumido por valuation.py (per-share); llega al Expert vía ValuationReport",
    "invested_capital": "consumido por competitive.py (contexto del moat) y por ROIC en edgar.parse_financials",
    "total_assets": "insumo interno del ROIC (umbral IC < 5 % de activos, T9); reservado para H3-1 scores contables",
    "stockholders_equity": "insumo interno del IC/ROIC (T9) y del bridge (bloque C)",
    # --- FinancialsReport: entradas FCFF/bridge extraídas en T8 para el bloque C ---
    "interest_expense": _FCFF_BLOCK_C,
    "interest_income": _FCFF_BLOCK_C,
    "sbc": _FCFF_BLOCK_C,
    "depreciation_amortization": _FCFF_BLOCK_C + "; también reservado para H3-1 scores contables",
    "diluted_shares": _FCFF_BLOCK_C,
    "basic_shares_weighted": _FCFF_BLOCK_C,
    "operating_lease_liabilities": _FCFF_BLOCK_C + " (memo de leases en el bridge)",
    "operating_rou_asset": "insumo del IC operativo (T9) y del bridge (bloque C)",
    "minority_interest": _FCFF_BLOCK_C + " (net claims)",
    "preferred_stock": _FCFF_BLOCK_C + " (net claims)",
    "income_tax": _FCFF_BLOCK_C + " (tipo impositivo canónico)",
    "pretax_income": _FCFF_BLOCK_C + " (tipo impositivo canónico)",
    "finance_lease_principal_payments": _FCFF_BLOCK_C,
    "long_term_investments": _FCFF_BLOCK_C + " (bridge EV→equity)",
    "equity_method_investments": _FCFF_BLOCK_C + " (bridge, sumado aparte con flag)",
    "total_debt_series": _FCFF_BLOCK_C + " (suggest_wacc, T11)",
    "cash_series": "serie de balance de T9; consumo previsto en valoración (bloque C) y H3-1 scores contables",
    "equity_series": "serie de balance de T9; consumo previsto en valoración (bloque C) y H3-1 scores contables",
    "data_quality_flags": "consumido en T13 (bloque C): sección 'Data quality flags' del archiver y bloque del Expert",
}


@pytest.mark.parametrize("model", [FilingsRAGReport, ManagementReport, CompetitiveReport,
                                   FinancialsReport, CriticReport])
def test_every_report_field_has_a_consumer(model):
    orphan = [f for f in model.model_fields
              if f not in _ALLOWLIST and not re.search(rf"\.{f}\b", _CONSUMER_SRC)]
    assert orphan == [], f"{model.__name__}: campos sin consumidor {orphan} — consumir o añadir a _ALLOWLIST con motivo"


def test_allowlist_entries_have_reasons():
    assert all(isinstance(v, str) and len(v) > 10 for v in _ALLOWLIST.values())


def test_rule_numbering_post_10k_acquisition_is_19():
    src = inspect.getsource(value_expert)
    assert "Per Rule 18:" not in src and "Per Rule 19:" in src
    assert re.search(r"^19\. POST-10-K ACQUISITION", value_expert._SYSTEM_PROMPT, re.M)
