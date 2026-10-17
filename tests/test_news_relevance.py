"""
Tests del filtro determinista de relevancia de noticias (EC-111).

Los casos son cadenas REALES copiadas de data/reports/ (CALM 2026-10-10, SERV 2026-05-28,
AAPL 2026-10-09). Cero red: función pura + market_agent con adaptadores mockeados.
"""

import pytest

from due_diligence.agents.market import market_agent
from due_diligence.tools.news_relevance import (
    company_name_variants,
    filter_relevant_news,
    is_navigation_dump,
    mentions_company_name,
    mentions_ticker,
)

# --- CALM (palabra común): Tavily "extended coverage" del informe 2026-10-10 ---
_CALM_TAVILY = [
    ("Cal-Maine Foods Inc (CALM) Q3 2026 Earnings Call Highlights: Navigating Challenges with ...",
     "Cal-Maine Foods Inc (NASDAQ:CALM) reported a significant increase in speci"),
    ("Cal-Maine Foods (CALM) Dropped, So What Is Behind The Latest Attention?",
     "The weak quarter has quickly fed into sentiment around Cal-Maine Foods"),
    ("Is Cal Maine Foods (CALM) Fairly Valued On Full Year Earnings?",
     "Get insights on thousands of stocks ... Cal-Ma"),
    ("What's Keeping Supply Chains Afloat Amidst The Tariffs ...",
     "A single miscalculation on tariffs can erase the margin on an entire product line."),
    ("‘Fewer Choices and Higher Prices’: The Supply Chain of the Future - WSJ",
     "American consumers should brace for price increases and fewer options in the store"),
    ("Trade War Roils Logistics Sector After 2024's Relative Calm",
     "Calm U.S. logistics costs were flat. U.S. business logistics costs increased in 2024"),
    ("Exclusive | SEC Investigating Former Chair of Auditing Industry Regulator - WSJ",
     "The Securities and Exchange Commission is investigating whether the recently dismissed chairman"),
    ("Everybody Calm Down About GameStop",
     "The CFTC has opened a parallel civil investigation and the SEC has begun reviewing social media"),
    ("Exclusive | SEC Investigating Melvin Capital Management - WSJ",
     "The U.S. Securities and Exchange Commission is looking into Melvin Capital Management's risk controls"),
]

_CALM_FINNHUB = [
    ("What Investors Might Expect From September's Inflation Report",
     "U.S. bond yields surged to multi-decade highs ahead of the October 2026 CPI report."),
    ("Cal-Maine points to egg supply, demand rebalance",
     "Fewer hatched chicks from breeder eggs and a reduction in U.S. flock are helping ease the current glut."),
    ("1 Cash-Heavy Stock to Target This Week and 2 Facing Headwinds",
     "A surplus of cash can mean financial stability, but it can also indicate a reluctance to invest."),
    ("Is Cal-Maine’s (CALM) Egg Slump Already Priced Into the Stock?",
     "As the largest producer and distributor of fresh shell eggs in the US, Cal-Maine Foods (NASDAQ:CALM)"),
]

# --- SERV: volcado de menú Yahoo (informe 2026-05-28) ---
_SERV_MENU = (
    "### [News](https://www.yahoo.com/). * [US](https://www.yahoo.com/news/us/). "
    "* [Shopping](https://shopping.yahoo.com/). + [Health](https://shopping.yahoo.com/health/). "
    "+ [Tech](https://shopping.yahoo.com/tech/). ### [Finance](https://finance.yahoo.com/). "
    "* [My Portfolio](https://finance.yahoo.com/portfolios/). * [News](https://finance.yahoo.com/news/). "
    "+ [Tech](https://finance.yahoo.com/topic/tech/). + [Tariffs](https://finance.yahoo.com/topic/tariffs/)."
)
_SERV_TAVILY = [
    ("Why Serve Robotics Stock Lost 22% in November",
     "- Best gifts for sister-in-law. Serve, whose robots can be seen rolling down the sidewalk, "
     "is still a speculative stock as it's bringing in less than $1 million in quarterly revenue."),
    ("BASF Shares Drop After Earnings Miss Guidance - WSJ", "BASF shares fell after the chemicals maker missed."),
    ("THG Issues Profit Warning as Online Retailer's Woes Worsen", "THG shares dropped after a profit warning."),
    ("Under Armour (UAA): Do Tariff Rollbacks Mark a Turning Point in Its Margin Reset Strategy?", _SERV_MENU),
]

# --- AAPL: ruido genérico de mercado (informe 2026-10-09) ---
_AAPL_FINNHUB = [
    ("Sector Update: Consumer Stocks Rise Late Afternoon",
     "Consumer stocks rose late Friday afternoon, with the State Street Consumer Staples Select Sector SPD"),
    ("Sector Update: Tech Stocks Mixed Late Afternoon",
     "Tech stocks were mixed late Friday afternoon with the State Street Technology Select Sector SPDR ETF"),
    ("Apple Weighed Replacing “5,000 AppleCare Employees” With AI, and More Cuts Are Coming",
     "Bloomberg’s Mark Gurman reported that Apple seriously considered laying off 5,000 employees"),
    ("Friday's session: top gainers and losers in the dow jones index",
     "Uncover the latest developments among dow jones stocks in today's session."),
    ("Stock Market Today: Dow Pops 500 Points As Big Bank Earnings Loom; JPMorgan Chase In A Base (Live Coverage)",
     "The Dow Jones index rises Friday even as Apple stock sells off in morning trading."),
]


def _as_items(pairs, tk="title", bk="content"):
    return [{tk: t, bk: b} for t, b in pairs]


def test_calm_tavily_drops_word_matches_keeps_company_items():
    items = _as_items(_CALM_TAVILY)
    # Tavily usa title/content (Finnhub, headline/summary por defecto)
    kept, dropped = filter_relevant_news(items, "CALM", "Cal-Maine Foods, Inc.", "title", "content")
    assert [k["title"][:12] for k in kept] == ["Cal-Maine Fo", "Cal-Maine Fo", "Is Cal Maine"]
    assert dropped == 6  # antes del filtro entraban 9


def test_calm_word_alone_is_not_a_ticker_match():
    assert not mentions_ticker("Everybody Calm Down About GameStop", "CALM")
    assert not mentions_ticker("Relative Calm after the storm", "CALM")
    assert mentions_ticker("Cal-Maine Foods Inc (NASDAQ:CALM) reported", "CALM")
    assert mentions_ticker("buy $CALM now", "CALM")


def test_calm_finnhub_keeps_name_only_headline():
    # "Cal-Maine points to egg supply" no trae el ticker: debe entrar por el nombre acortado.
    kept, dropped = filter_relevant_news(_as_items(_CALM_FINNHUB, "headline", "summary"),
                                         "CALM", "Cal-Maine Foods, Inc.")
    assert [k["headline"][:9] for k in kept] == ["Cal-Maine", "Is Cal-Ma"]
    assert dropped == 2


def test_serv_menu_dump_dropped_even_if_it_would_otherwise_pass():
    assert is_navigation_dump(_SERV_MENU)
    kept, dropped = filter_relevant_news(_as_items(_SERV_TAVILY), "SERV", "Serve Robotics Inc.", "title", "content")
    assert [k["title"][:9] for k in kept] == ["Why Serve"]
    assert dropped == 3


def test_menu_phrases_trigger_dump_but_single_mention_does_not():
    assert is_navigation_dump("Sign in\nMail\nQuote Lookup\nMy Portfolio\nNews")
    assert not is_navigation_dump("Readers can sign in to the investor portal to see the filing.")


def test_aapl_generic_market_noise_dropped():
    kept, dropped = filter_relevant_news(_as_items(_AAPL_FINNHUB, "headline", "summary"), "AAPL", "Apple Inc.")
    assert [k["headline"][:5] for k in kept] == ["Apple", "Stock"]  # la 5.ª menciona "Apple stock"
    assert dropped == 3


@pytest.mark.parametrize("name,expected", [
    ("Cal-Maine Foods, Inc.", ["Cal-Maine Foods", "Cal-Maine"]),
    ("Apple Inc.", ["Apple"]),
    ("The Kroger Co.", ["Kroger"]),
    ("Serve Robotics Inc.", ["Serve Robotics"]),
    (None, []),
])
def test_company_name_variants(name, expected):
    assert company_name_variants(name) == expected


def test_single_word_name_requires_capitalization():
    assert mentions_company_name("Apple reported record revenue", "Apple Inc.")
    assert not mentions_company_name("an apple a day, apple pie recipes", "Apple Inc.")


def test_single_letter_ticker_needs_explicit_form():
    assert not mentions_ticker("A new report says that sales rose", "A")
    assert mentions_ticker("Agilent (NYSE: A) rose", "A")


def test_unknown_name_falls_back_to_ticker_only():
    kept, dropped = filter_relevant_news([{"headline": "Apple beats Q1", "summary": ""}], "AAPL", None)
    assert kept == [] and dropped == 1


# --- Integración con market_agent: el recuento descartado llega al informe ---

def test_market_agent_renders_filtered_count(mocker):
    mocker.patch("due_diligence.agents.market.PolygonAdapter.fetch_market_data", return_value={
        "price_current": 100.0, "price_52w_high": 110.0, "price_52w_low": 90.0,
        "market_cap": 1e9, "company_name": "Apple Inc.", "as_of_date": "2026-10-09"})
    mocker.patch("due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
                 return_value=[{"url": "u", "source": "Yahoo", "datetime": 1, **{"headline": h, "summary": s}}
                               for h, s in _AAPL_FINNHUB])
    result = market_agent({"ticker": "AAPL"})
    summary = result["market"].recent_news_summary
    assert "Apple Weighed Replacing" in summary
    assert "Sector Update" not in summary
    assert "(3 unrelated items filtered out)" in summary


def test_drawdown_tavily_uses_company_name_not_ticker(mocker):
    """EC-111: sin company_name en el state, las queries Tavily usaban el ticker ("CALM stock…")."""
    mocker.patch("due_diligence.agents.market.PolygonAdapter.fetch_market_data", return_value={
        "price_current": 50.0, "price_52w_high": 100.0, "price_52w_low": 40.0,
        "market_cap": 1e9, "company_name": "Cal-Maine Foods, Inc.", "as_of_date": "2026-10-09"})
    mocker.patch("due_diligence.agents.market.FinnhubAdapter.fetch_company_news", return_value=[])
    search = mocker.patch("due_diligence.agents.market.TavilyAdapter.search", return_value=[
        {"url": "u", "title": t, "content": c, "score": 1.0, "query": "q"} for t, c in _CALM_TAVILY])
    result = market_agent({"ticker": "CALM"})
    assert all("Cal-Maine Foods, Inc." in q for q, _ in search.call_args_list[0].args[0])
    summary = result["market"].recent_news_summary
    assert "Everybody Calm Down" not in summary
    assert "(6 unrelated items filtered out)" in summary


# --- Revisión de código: nombres EDGAR que no se parecen a como la prensa cita a la empresa ---
# Cada caso: (ticker, nombre EDGAR, titular). Sin alias por ticker: solo reglas genéricas.

@pytest.mark.parametrize("ticker,name,headline", [
    ("AMZN", "AMAZON COM INC", "Amazon raises prices"),                    # "COM" = sufijo legal
    ("MCD", "MCDONALDS CORP", "McDonald's same-store sales rise"),          # apóstrofos
    ("MCD", "MCDONALDS CORP", "McDonald’s same-store sales rise"),         # apóstrofo tipográfico
    ("XOM", "EXXON MOBIL CORP", "ExxonMobil cuts capex"),                   # forma compacta
    ("META", "Meta Platforms, Inc.", "Meta unveils new glasses"),           # primer token
    ("HPQ", "HP INC", "HP cuts outlook"),                                   # nombre de 2 letras
    ("MMM", "3M CO", "3M settles lawsuit"),
    ("T", "AT&T INC.", "AT&T beats estimates"),                             # & interno
    ("PG", "PROCTER & GAMBLE Co", "Procter & Gamble raises guidance"),
    ("BRK.B", "BERKSHIRE HATHAWAY INC", "Berkshire sells Apple stake"),    # primer token
])
def test_edgar_names_match_how_press_writes_them(ticker, name, headline):
    kept, dropped = filter_relevant_news([{"headline": headline, "summary": ""}], ticker, name)
    assert len(kept) == 1 and dropped == 0


@pytest.mark.parametrize("ticker,name,headline", [
    ("SO", "SOUTHERN CO", "Southern California wildfires spread"),          # "Southern" es genérica
    ("HPQ", "HP INC", "hp printers and the hp of a character"),             # 2 letras: solo mayúsculas
    ("ITX", "IT HOLDINGS INC", "Why IT spending rises"),                     # 2 letras ambiguas: no valen
    ("META", "Meta Platforms, Inc.", "The meta game in chess"),             # primer token en minúscula
    ("MCD", "MCDONALDS CORP", "mcdonalds-style burgers at home"),           # minúscula
])
def test_generic_or_lowercase_name_matches_dropped(ticker, name, headline):
    kept, dropped = filter_relevant_news([{"headline": headline, "summary": ""}], ticker, name)
    assert kept == [] and dropped == 1


def test_stoplisted_single_word_name_accepts_full_name_with_suffix():
    kept, _ = filter_relevant_news([{"headline": "Southern Company wins approval", "summary": ""}],
                                   "SO", "SOUTHERN CO")
    assert len(kept) == 1


def test_ambiguous_single_word_name_is_kept_when_capitalized():
    # Decisión documentada: "Target" capitalizado cuenta aunque sea ambiguo (precisión vs recall:
    # el ticker/nombre capitalizado es una señal razonable; descartarlo perdería noticias reales
    # de la empresa). Es el mismo criterio que "Apple", "Visa".
    kept, _ = filter_relevant_news([{"headline": "Target price raised on Nvidia", "summary": ""}],
                                   "TGT", "TARGET CORP")
    assert len(kept) == 1


def test_earnings_table_snippet_is_not_navigation_dump():
    table = "\n".join(["Revenue", "$1.2B", "EPS", "$0.45", "Net income", "$300M", "Margin", "41%",
                       "Guidance", "$5.1B", "Shares", "120M"]
                      + ["The company reported quarterly results above analyst expectations this period."])
    assert not is_navigation_dump(table)
    assert is_navigation_dump("\n".join(["Home", "News", "Markets", "Video", "Podcasts", "Sports", "Weather",
                                         "Login", "Help", "About", "Jobs", "Press", "Apps"]
                                        + ["A long sentence about something unrelated to menus."]))
