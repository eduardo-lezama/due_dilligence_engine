"""
Tests del parser de la tabla de participaciones del DEF 14A (EC-93).

Fixtures = tablas HTML reales de 5 proxies (scripts/fetch_def14a_fixtures.py), incluidas las
tablas señuelo que también dicen "as a group" (premios en acciones de PRDO, viñetas y notas al
pie de CALM). Los valores esperados se leyeron a mano de la fila "as a group" de cada fixture.
"""
from pathlib import Path

import pytest

from due_diligence.tools.proxy_ownership import parse_beneficial_ownership, render_ownership

FIXTURES = Path(__file__).parent / "fixtures"


def _html(ticker: str) -> str:
    return (FIXTURES / f"{ticker}_def14a_ownership.html").read_text(encoding="utf-8")


# (ticker, acciones del grupo, % o None si "*", ¿menos del 1 %?, fragmento de la etiqueta)
_EXPECTED = [
    ("calm", 1_697_343, 3.6, False, "(14 persons)"),     # % en su propia celda: "3.6 | %"
    ("aapl", 9_079_765, None, True, "(12 persons)"),     # "*" = menos del 1 %; nota "(17)" pegada
    ("azo", 435_783, 2.6, False, "(26) persons"),        # 5 columnas: el TOTAL va justo antes del %
    ("bbw", 502_388, 4.0, False, "(11 persons)"),
    ("prdo", 915_649, 1.46, False, "(17 persons)"),      # señuelo: tabla de premios "as a group" con $
]


@pytest.mark.parametrize("ticker,shares,pct,below,label_part", _EXPECTED, ids=[e[0] for e in _EXPECTED])
def test_group_row_from_real_proxies(ticker, shares, pct, below, label_part):
    bo = parse_beneficial_ownership(_html(ticker))
    assert bo is not None and bo.group is not None
    g = bo.group
    assert g.shares == shares
    assert g.percent == pct                       # nunca un 0,5 inventado para "*"
    assert g.below_1pct is below
    assert label_part in g.label and "as a group" in g.label.lower()


def test_holder_rows_parsed_with_percentages():
    bo = parse_beneficial_ownership(_html("calm"))
    by_label = {r.label: r for r in bo.rows}
    baker = next(r for label, r in by_label.items() if label.startswith("Adolphus B. Baker"))
    assert baker.shares == 1_504_175 and baker.percent == 3.2
    blackrock = next(r for label, r in by_label.items() if label.startswith("BlackRock"))
    assert blackrock.percent == 13.3


def test_render_is_citable_text():
    bo = parse_beneficial_ownership(_html("aapl"))
    text = render_ownership(bo)
    assert "All current directors and executive officers as a group (12 persons) | 9,079,765 shares | less than 1%" in text
    assert bo.group.text in text


def test_no_ownership_table_returns_none():
    assert parse_beneficial_ownership("<html><body><p>No tables here</p></body></html>") is None
    assert parse_beneficial_ownership("") is None


def test_footnote_and_award_decoys_are_ignored():
    html = """<html><body>
    <table><tr><td>All executive officers as a group</td><td>$</td><td>5,405,572</td><td>216,830</td></tr></table>
    <table><tr><td>(18)</td><td>the number of shares beneficially owned by all directors and executive officers
      as a group includes 11,393 shares</td></tr></table>
    </body></html>"""
    assert parse_beneficial_ownership(html) is None


def test_less_than_one_percent_text_variant():
    html = """<html><body><table>
    <tr><td>Name of Beneficial Owner</td><td>Shares</td><td>Percent</td></tr>
    <tr><td>Big Holder LP</td><td>1,000,000</td><td>8.5%</td></tr>
    <tr><td>All directors and executive officers as a group (9 persons)</td><td>12,345(4)</td><td>Less than 1%</td></tr>
    </table></body></html>"""
    g = parse_beneficial_ownership(html).group
    assert g.shares == 12_345 and g.percent is None and g.below_1pct is True


# --- Consumidores: el Value Expert, el checkpoint 1 y la CLI ven el dato con su procedencia ---
def _mgmt(**over):
    from due_diligence.schemas.reports import ManagementReport
    base = dict(ticker="TST", stub=False)
    base.update(over)
    return ManagementReport(**base)


def test_expert_context_labels_deterministic_ownership():
    from due_diligence.agents.value_expert import _format_context
    ctx = _format_context({"ticker": "TST", "mgmt": _mgmt(insider_ownership_pct=3.6,
                                                         insider_ownership_shares=1_697_343)})
    assert "Insider ownership (DEF 14A ownership table, deterministic): 3.6% (1,697,343 shares)" in ctx


def test_expert_context_shows_below_one_percent_without_number():
    from due_diligence.agents.value_expert import _format_context
    ctx = _format_context({"ticker": "TST", "mgmt": _mgmt(insider_ownership_below_1pct=True,
                                                         insider_ownership_shares=9_079_765)})
    assert "Insider ownership (DEF 14A ownership table, deterministic): less than 1% (9,079,765 shares)" in ctx


def test_checkpoint1_asks_about_below_one_percent_ownership():
    from due_diligence.agents.checkpoint import _build_socratic_questions
    qs = _build_socratic_questions({"ticker": "TST", "mgmt": _mgmt(insider_ownership_below_1pct=True)})
    assert any("less than 1%" in q for q in qs)


# --- Multiclase (PATH, GOOGL, BRK-B): no hay UN porcentaje; la última celda es el % de VOTO ---
# Con la regla de "última celda" PATH salía como "85,2 % de participación" cuando es el voto que
# controlan (Class B). Un % único sería falso: se marca multi_class y se conserva la fila literal.
@pytest.mark.parametrize("ticker,must_contain", [
    ("path", "64,690,706 | 100% | 85.2%"),     # Class A 11.4 %, Class B 100 %, voto 85.2 %
    ("googl", "770,340,078 | 92.2 | 54.3"),    # Class B 92.2 %, voto 54.3 %
    ("brk-b", "197,224"),                       # Class A del grupo
], ids=["path", "googl", "brk-b"])
def test_multi_class_group_row_kept_literal_without_single_percent(ticker, must_contain):
    bo = parse_beneficial_ownership(_html(ticker))
    g = bo.group
    assert g is not None and g.multi_class is True
    assert g.percent is None and g.shares is None and g.below_1pct is False
    assert must_contain in g.text and "as a group" in g.text.lower()


def test_nue_single_class_with_count_in_label():
    g = parse_beneficial_ownership(_html("nue")).group
    assert g.multi_class is False and g.shares == 1_406_923 and g.below_1pct is True
    assert g.label.startswith("All 19 directors")


def test_multi_class_flows_to_report_and_expert_context():
    from due_diligence.agents.management import _build_report
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.llm_outputs import ManagementLLMOutput
    out = ManagementLLMOutput(founder_ceo=None, ceo_tenure_years=None, ceo_tenure_citation=None,
                              insider_transaction_pattern=None, compensation_structure_notes=None,
                              capital_allocation_summary=None, citations=[])
    bo = parse_beneficial_ownership(_html("path"))
    rep = _build_report(out, "PATH", {"DEF 14A": "x"}, 0, ownership=bo)
    assert rep.insider_ownership_pct is None and rep.insider_ownership_multi_class_row == bo.group.text
    ctx = _format_context({"ticker": "PATH", "mgmt": rep})
    assert "multi-class shares" in ctx and "85.2%" in ctx


@pytest.mark.parametrize("ticker", ["aapl", "bbw"])
def test_footnote_tables_mentioning_voting_power_are_not_ownership_rows(ticker):
    # Visto en los fixtures reales: las tablas de notas al pie dicen "sole voting power" o "Class"
    # y se colaban como filas multiclase (párrafos enteros en el texto que ve el LLM).
    bo = parse_beneficial_ownership(_html(ticker))
    assert [r.text[:60] for r in bo.rows if r.multi_class] == []


# --- Revisión adversarial (33 emisores en vivo): casos con CIFRA EQUIVOCADA, peor que None ---
# Todos son tablas con dos clases o dos grupos de columnas que la cabecera no delata:
#   NKE: la clase va en una celda de cada fila; NWSA: "Non-VotingClass ACommonStock" (<br> pegado);
#   HEI: "Common Stock" + "Class A Common Stock"; UHAL: "Voting" / "Non-Voting Common";
#   SPG: dos pares número/% ("Shares and units" frente a "Units").
@pytest.mark.parametrize("ticker,truth_fragment", [
    ("nke", None),
    ("nwsa", "62,590,166"),
    ("hei", "5,352,157"),        # Common Stock real; antes devolvía el 1,69 % de Class A
    ("uhal", "9,834,757"),       # Voting real; antes el 43 % de Non-Voting
    ("spg", "30,779,325"),       # "Shares and units"; antes el 7,31 % de "Units"
], ids=["nke", "nwsa", "hei", "uhal", "spg"])
def test_multi_column_tables_never_yield_a_single_wrong_percent(ticker, truth_fragment):
    g = parse_beneficial_ownership(_html(ticker)).group
    assert g is not None and g.multi_class is True and g.percent is None and g.shares is None
    if truth_fragment:
        assert truth_fragment in g.text


def test_group_row_alone_in_its_own_table_is_found():
    f = parse_beneficial_ownership(_html("f")).group            # Ford: tabla de 1 fila
    assert f is not None and f.shares == 24_614_803 and f.percent == 0.63
    bfb = parse_beneficial_ownership(_html("bf-b")).group       # Brown-Forman: multiclase, 1 fila
    assert bfb is not None and bfb.multi_class is True


def test_star_percent_variant_means_below_one_percent():
    g = parse_beneficial_ownership(_html("acn")).group          # Accenture: "*%"
    assert g is not None and g.shares == 144_910 and g.below_1pct is True and g.percent is None


def test_stale_proxy_is_flagged_in_formatter():
    from due_diligence.utils.formatting import fmt_insider_ownership
    old = fmt_insider_ownership(32.6, 1_000, False, as_of="2022-01-10")
    assert "proxy filed 2022-01-10" in old and "stale" in old
    from datetime import date
    fresh = fmt_insider_ownership(3.6, 1_000, False, as_of=date.today().isoformat())
    assert "stale" not in fresh
