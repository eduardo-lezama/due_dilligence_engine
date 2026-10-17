"""
Tests del control de evidencia (EC-94): un especialista con 0 citas del LLM verificadas no
puede pasar por un análisis normal.

Medido tras H1 (4 análisis en vivo): management 0 verificadas en 4/4, filings 0 citas en 3/4.
Nada lo detectaba: la narrativa llegaba al Value Expert sin etiqueta, el informe omitía la
sección en silencio y no se activaba el tope de confianza por falta de datos.
"""
from due_diligence.agents.archiver import _render_citations_section
from due_diligence.agents.value_expert import _data_gaps, _format_context, _missing_essentials
from due_diligence.schemas.base import Citation
from due_diligence.schemas.reports import FilingsRAGReport, ManagementReport
from due_diligence.validation.citations import llm_evidence_count


def _cit(verified: bool, note: str | None = None) -> Citation:
    return Citation(claim="c", source_url="10-K Item 7", excerpt="Revenue increased 5% to $1,200 million.",
                    confidence=0.9 if verified else 0.0, verified=verified, verification_note=note)


_DETERMINISTIC = _cit(True, "deterministic: parsed from the DEF 14A beneficial ownership table (EC-93)")


def _state(filings_cits, mgmt_cits):
    return {"ticker": "TST",
            "filings": FilingsRAGReport(ticker="TST", stub=False, business_description="Makes widgets.",
                                        citations=filings_cits),
            "mgmt": ManagementReport(ticker="TST", stub=False, capital_allocation_summary="Buybacks.",
                                     citations=mgmt_cits)}


def test_llm_evidence_ignores_deterministic_citations():
    assert llm_evidence_count([_DETERMINISTIC]) == 0
    assert llm_evidence_count([_DETERMINISTIC, _cit(True), _cit(False, "not found")]) == 1


def test_zero_llm_evidence_counts_as_data_gap_and_caps_confidence():
    st = _state(filings_cits=[], mgmt_cits=[_DETERMINISTIC])
    gaps = dict(_data_gaps(st))
    assert "0 verified citations" in gaps["Filings"] and "0 verified citations" in gaps["Management"]
    assert set(_missing_essentials(st)) == {"Filings", "Management"}   # → tope de confianza 0,6


def test_agent_with_verified_evidence_is_not_a_gap():
    st = _state(filings_cits=[_cit(True)], mgmt_cits=[_DETERMINISTIC, _cit(True)])
    assert _data_gaps(st) == [] and _missing_essentials(st) == []


def test_unevidenced_narrative_is_labelled_in_expert_context():
    ctx = _format_context(_state(filings_cits=[], mgmt_cits=[_cit(True)]))
    filings_block = ctx.split("## FILINGS REPORT (10-K)")[1]
    assert filings_block.lstrip().startswith("⚠️ UNVERIFIED")
    mgmt_block = ctx.split("## MANAGEMENT REPORT")[1].split("##")[0]
    assert "UNVERIFIED" not in mgmt_block


def test_report_says_no_verified_evidence_instead_of_skipping():
    md = "\n".join(_render_citations_section(_state(filings_cits=[], mgmt_cits=[_cit(True)])))
    assert "### Filings (10-K) — no verified evidence" in md


# --- B2: productores de evidencia (filings) ---
import inspect  # noqa: E402


from due_diligence.agents.filings import filings_agent  # noqa: E402
from due_diligence.schemas.llm_outputs import CitationOut, FilingsLLMOutput  # noqa: E402
from due_diligence.tools.edgar import EdgarFilingsAdapter, TENK_ITEM_MAX_CHARS  # noqa: E402

MDA = "Net sales increased 12% to $4.26 billion in fiscal 2025, driven by higher egg prices."


def _fout(citations):
    return FilingsLLMOutput(business_description="Eggs.", risk_factors_summary="Avian flu.",
                            mda_highlights="Sales up.", backlog_usd=None, backlog_citation=None,
                            revenue_guidance=None, geographic_revenue_breakdown=None,
                            key_regulatory_events=None, governance_red_flags=None,
                            active_sec_investigation=None, material_weakness=None,
                            governance_citation=None, citations=citations)


def _cout(excerpt):
    return CitationOut(claim="c", source_url="10-K Item 7", excerpt=excerpt, confidence=0.9,
                       gaap_vs_nongaap_flag=None)


def _patch_items(mocker, business="We produce eggs for retail customers.", risk="Avian influenza risk.", mda=MDA):
    p = "due_diligence.agents.filings.EdgarFilingsAdapter."
    mocker.patch(p + "fetch_10k_business", return_value=business)
    mocker.patch(p + "fetch_10k_risk_factors", return_value=risk)
    mocker.patch(p + "fetch_10k_mda", return_value=mda)


def _client(mocker, *outs):
    client = mocker.MagicMock()
    client.structured.side_effect = list(outs)
    mocker.patch("due_diligence.agents.filings.get_client", return_value=client)
    return client


def test_10k_items_are_not_cut_to_a_fifth_of_the_section():
    # CALM: 8-10k caracteres eran el 15-20 % de cada sección (auditoría EC-94).
    assert TENK_ITEM_MAX_CHARS >= 25_000
    for fn in (EdgarFilingsAdapter.fetch_10k_business, EdgarFilingsAdapter.fetch_10k_risk_factors,
               EdgarFilingsAdapter.fetch_10k_mda):
        assert inspect.signature(fn).parameters["max_chars"].default == TENK_ITEM_MAX_CHARS


def test_all_10k_items_empty_fails_loud_without_llm_call(mocker):
    _patch_items(mocker, business="", risk="", mda="")
    client = _client(mocker)
    rep = filings_agent({"ticker": "TST"})["filings"]
    assert rep.stub is True and "10-K" in rep.error
    client.structured.assert_not_called()


def test_filings_retries_once_when_no_citation_verifies(mocker):
    bad = _fout([_cout("Sales went up a lot because eggs were expensive this year.")])
    good = _fout([_cout(MDA)])
    _patch_items(mocker)
    client = _client(mocker, bad, good)
    rep = filings_agent({"ticker": "TST"})["filings"]
    assert client.structured.call_count == 2
    retry_prompt = client.structured.call_args_list[1].kwargs["messages"][-1]["content"]
    assert "Sales went up a lot" in retry_prompt and "VERBATIM" in retry_prompt
    assert any(c.verified and c.excerpt == MDA for c in rep.citations)


def test_filings_no_retry_when_evidence_verifies(mocker):
    _patch_items(mocker)
    client = _client(mocker, _fout([_cout(MDA)]))
    filings_agent({"ticker": "TST"})
    assert client.structured.call_count == 1


def test_filings_retry_failure_keeps_first_report(mocker):
    from due_diligence.llm.errors import LLMSchemaError
    _patch_items(mocker)
    client = _client(mocker, _fout([]), LLMSchemaError("bad"))
    rep = filings_agent({"ticker": "TST"})["filings"]
    assert client.structured.call_count == 2 and rep.stub is False and rep.error is None


# --- Hallazgos de la investigación AAPL (rechazos de management) ---
def test_tenure_prompt_forbids_training_data_dates():
    # AAPL: "Tim Cook has been CEO since August 24, 2011" — la fecha no estaba en NINGUNA fuente; el
    # prompt exigía cita siempre que hubiera antigüedad y el modelo la inventó desde su entrenamiento.
    from due_diligence.agents.management import _SYSTEM_PROMPT
    assert "set ceo_tenure_years to null" in _SYSTEM_PROMPT
    assert "never from your own knowledge" in _SYSTEM_PROMPT


def test_unverified_citations_listed_once():
    dup = _cit(False, "excerpt not found in source (fuzzy=55)")
    md = "\n".join(_render_citations_section(_state(filings_cits=[], mgmt_cits=[dup, dup])))
    assert md.count("fuzzy=55") == 1
