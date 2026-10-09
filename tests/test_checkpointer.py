"""
Tests del checkpointer con allowlist de deserialización (EC-95, ADR-037).

LangGraph 1.1.8 avisa al reconstruir tipos no registrados y en una versión futura los
BLOQUEARÁ. Un tipo bloqueado no lanza: vuelve como datos en bruto (jsonplus.py
_create_msgpack_ext_hook) — un ValuationReport llegaría al Value Expert como dict. Aquí se
comprueba, con el modo estricto, que el estado real vuelve como los mismos objetos.
"""
import inspect
import json
import logging
from pathlib import Path

import pytest
from pydantic import BaseModel

from due_diligence.agents.valuation import valuation_agent
from due_diligence.graph.checkpointer import CHECKPOINT_ALLOWLIST, make_checkpointer, make_serializer
from due_diligence.schemas import base, llm_outputs, reports
from due_diligence.schemas.base import Citation
from due_diligence.schemas.reports import (CompetitiveReport, FilingsRAGReport, ManagementReport,
                                           PeerValuation, QuarterlySnapshot, ValuationReport)
from due_diligence.tools.edgar import EdgarAdapter

from tests.test_valuation import _levered_state

FIXTURES = Path(__file__).parent / "fixtures"


def _realistic_state() -> dict:
    """Estado con los tipos anidados reales: reports, MetricValue, Citation, bridge, ciclicidad…"""
    st = _levered_state()
    st["valuation"] = valuation_agent(st)["valuation"]
    st["financials"] = EdgarAdapter.parse_financials(json.loads((FIXTURES / "calm_xbrl_raw.json").read_text()))
    cit = Citation(claim="c", source_url="DEF 14A", excerpt="x" * 25, confidence=1.0, verified=True)
    st["mgmt"] = ManagementReport(ticker="LEV", stub=False, insider_ownership_pct=3.6, citations=[cit])
    st["filings"] = FilingsRAGReport(ticker="LEV", stub=False, citations=[cit])
    st["competitive"] = CompetitiveReport(ticker="LEV", stub=False, moat_classification="narrow")
    st["quarterly_snapshot"] = QuarterlySnapshot(period="2026-08-29 (Q1)", revenue=1.0)
    st["peer_valuations"] = [PeerValuation(ticker="PEER", roic=0.15, period="FY2025")]
    return st


def test_realistic_state_roundtrips_as_objects_in_strict_mode(caplog):
    serde = make_serializer()
    state = _realistic_state()
    with caplog.at_level(logging.WARNING):
        restored = serde.loads_typed(serde.dumps_typed(state))
    assert not [r for r in caplog.records if "msgpack" in r.getMessage().lower()]
    for key in ("valuation", "financials", "mgmt", "filings", "competitive", "quarterly_snapshot"):
        assert type(restored[key]) is type(state[key]), key
        assert restored[key] == state[key], key
    assert isinstance(restored["peer_valuations"][0], PeerValuation)
    assert isinstance(restored["valuation"], ValuationReport)


def test_allowlist_covers_every_schema_model():
    """Guard: un modelo nuevo en schemas/ entra solo; nada se registra a mano."""
    models = {(m.__name__, n) for m in (base, reports, llm_outputs)
              for n, obj in inspect.getmembers(m, inspect.isclass)
              if issubclass(obj, BaseModel) and obj.__module__ == m.__name__}
    assert models and models <= set(CHECKPOINT_ALLOWLIST)


def test_unregistered_type_is_blocked_not_silently_allowed(caplog):
    """El serializador es ESTRICTO: un tipo fuera de la lista no se reconstruye (y lo dice)."""
    class Foreign(BaseModel):
        x: int = 1
    serde = make_serializer()
    with caplog.at_level(logging.WARNING):
        restored = serde.loads_typed(serde.dumps_typed({"thing": Foreign()}))
    assert not isinstance(restored["thing"], Foreign)
    assert any("blocked" in r.getMessage().lower() for r in caplog.records)


def test_make_checkpointer_uses_strict_serializer():
    cp = make_checkpointer()
    assert cp.serde._allowed_msgpack_modules == set(CHECKPOINT_ALLOWLIST)


@pytest.mark.parametrize("path", ["src/due_diligence/cli.py", "scripts/e2e_run.py", "scripts/freeze_contexts.py"])
def test_entry_points_use_the_factory(path):
    src = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
    assert "MemorySaver()" not in src and "make_checkpointer(" in src
