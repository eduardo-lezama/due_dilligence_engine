"""
EdgarAdapter — interfaz con SEC EDGAR para datos financieros XBRL.

Qué hace este módulo:
    Proporciona EdgarAdapter con dos métodos estáticos:
    - fetch_raw(ticker): obtiene datos XBRL crudos de EDGAR vía edgartools
    - parse_financials(raw): transforma el dict crudo en FinancialsReport validado

Por qué separar fetch y parse:
    - fetch_raw: I/O de red, no determinista, requiere EDGAR
    - parse_financials: función pura, determinista, 100% testable con fixtures
    - Permite cachear el raw y reusarlo
    - Los tests pueden usar parse_financials con fixtures JSON sin llamar a EDGAR

Edgartools 5.x:
    Usa la nueva API: facts.income_statement(), balance_sheet(), cashflow_statement()
    que devuelven MultiPeriodStatement con columnas 'FY 2025', 'FY 2024', etc.
    El formato del dict devuelto es compatible con parse_financials existente.

Uso en tests:
    - NO usar fetch_raw en tests (llamadas reales a EDGAR)
    - Usar fixtures JSON guardados con --save-fixture en el CLI
    - parse_financials(fixture) es 100% determinista y rápido
"""

from __future__ import annotations

from datetime import date
from typing import Any
import structlog

from due_diligence.config import settings
from due_diligence.schemas.base import FinancialsReport, MetricValue

log = structlog.get_logger()


class DataUnavailableError(Exception):
    pass


class EdgarAdapter:
    @staticmethod
    def fetch_raw(ticker: str) -> dict[str, Any]:
        """
        Live EDGAR fetch via edgartools 5.x API. Use parse_financials(fixture) in tests.

        edgartools 5.x cambió de facts.us_gaap.Concept.to_dataframe() a
        facts.income_statement() / balance_sheet() / cashflow_statement(),
        que devuelven MultiPeriodStatement con columnas 'FY 2025', 'FY 2024', etc.
        El formato del dict devuelto es idéntico al anterior para que parse_financials
        no necesite cambios.
        """
        import pandas as pd
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        log.info("edgar_fetch_start", ticker=ticker)

        company = Company(ticker)
        facts = company.get_facts()

        filings = company.get_filings(form="10-K")
        latest_10k = filings.latest()
        fy_end = str(latest_10k.period_of_report)
        fy_label = f"FY{fy_end[:4]}"

        # Obtener los tres estados financieros como DataFrames
        # Índice = concepto XBRL, columnas = ['label', ..., 'FY 2025', 'FY 2024', ...]
        inc_df = facts.income_statement().to_dataframe()
        bs_df = facts.balance_sheet().to_dataframe()
        cf_df = facts.cashflow_statement().to_dataframe()

        def _fy_cols(df: Any) -> list[str]:
            """FY columns sorted descending: ['FY 2025', 'FY 2024', ...]."""
            return sorted([c for c in df.columns if c.startswith("FY ")], reverse=True)

        def _get_annual(df: Any, concept: str, n: int = 10) -> list[dict]:
            """Extrae hasta n años de un concepto. Devuelve [] si no existe.

            Si df.loc[concept] devuelve un DataFrame (concepto con filas duplicadas,
            como ocurre en algunas empresas que usan el mismo concepto XBRL con
            contextos distintos), tomamos la primera fila para evitar que pd.notna()
            reciba una Series en lugar de un escalar.
            """
            if concept not in df.index:
                return []
            row = df.loc[concept]
            # Protección contra duplicados: si hay varias filas con el mismo concepto
            # (DataFrame en lugar de Series), usar la primera.
            if row.ndim == 2:  # DataFrame con filas duplicadas (ndim=2), no Series (ndim=1)
                row = row.iloc[0]
            result = []
            for col in _fy_cols(df)[:n]:
                val = row.get(col)
                try:
                    if pd.notna(val):
                        period = col.replace("FY ", "FY")
                        result.append({"value": float(val), "period": period})
                except (ValueError, TypeError):
                    # val es una Series en contextos inesperados — skip
                    pass
            return result

        def _get_point(df: Any, concept: str, period: str) -> dict:
            """Extrae el valor más reciente de un concepto puntual (balance sheet)."""
            if concept not in df.index:
                raise DataUnavailableError(f"{ticker}: XBRL concept '{concept}' not found")
            row = df.loc[concept]
            if row.ndim == 2:  # DataFrame con duplicados — tomar la primera fila
                row = row.iloc[0]
            for col in _fy_cols(df):
                val = row.get(col)
                try:
                    if pd.notna(val):
                        return {"value": float(val), "period": period}
                except (ValueError, TypeError):
                    pass
            raise DataUnavailableError(f"{ticker}: '{concept}' has no annual data")

        # --- Income statement ---
        revenue = _get_annual(inc_df, "RevenueFromContractWithCustomerExcludingAssessedTax")
        if not revenue:
            revenue = _get_annual(inc_df, "Revenues")
        if not revenue:
            raise DataUnavailableError(f"{ticker}: revenue concept not found in income statement")

        # --- Balance sheet ---
        # LongTermDebt: distintas empresas usan distintos conceptos XBRL.
        # Intentamos en orden de prevalencia; si ninguno existe, total_debt = 0.
        lt_debt = None
        for _lt_concept in ("LongTermDebt", "LongTermDebtNoncurrent",
                             "LongTermDebtAndCapitalLeaseObligations",
                             "LongTermNotesPayable"):
            if _lt_concept in bs_df.index:
                try:
                    lt_debt = _get_point(bs_df, _lt_concept, fy_label)
                    break
                except DataUnavailableError:
                    pass
        if lt_debt is None:
            lt_debt = {"value": 0.0, "period": fy_label}

        # ShortTermBorrowings + porción corriente de LTD (opcional — no todas las empresas)
        st_debt_value = 0.0
        for _st_concept in ("ShortTermBorrowings", "LongTermDebtCurrent",
                             "NotesPayableCurrent", "DebtCurrent"):
            if _st_concept in bs_df.index:
                try:
                    st_debt_value += _get_point(bs_df, _st_concept, fy_label)["value"]
                except DataUnavailableError:
                    pass

        # Cash and equivalents
        cash = None
        for _cash_concept in ("CashAndCashEquivalentsAtCarryingValue",
                               "CashAndCashEquivalents", "Cash"):
            if _cash_concept in bs_df.index:
                try:
                    cash = _get_point(bs_df, _cash_concept, fy_label)
                    break
                except DataUnavailableError:
                    pass
        if cash is None:
            cash = {"value": 0.0, "period": fy_label}

        # STI (marketable securities a corto plazo): no aparece en el BS normalizado
        # de edgartools 5.x para todas las empresas — defaulta a 0
        sti_value = 0.0

        raw = {
            "ticker": ticker.upper(),
            "cik": str(company.cik).zfill(10),
            "as_of_date": fy_end,
            "source_filing": f"10-K {fy_label}",
            "annual": {
                "revenue": revenue,
                "gross_profit": _get_annual(inc_df, "GrossProfit"),
                "operating_income": _get_annual(inc_df, "OperatingIncomeLoss"),
                "net_income": _get_annual(inc_df, "NetIncomeLoss"),
                "operating_cash_flow": _get_annual(
                    cf_df, "NetCashProvidedByUsedInOperatingActivities"
                ),
                "capex": _get_annual(cf_df, "PaymentsToAcquirePropertyPlantAndEquipment"),
                # Para ROIC: impuesto + pretax income permiten calcular effective_tax_rate
                "income_tax": _get_annual(inc_df, "IncomeTaxExpenseBenefit"),
                "pretax_income": _get_annual(
                    inc_df,
                    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                ),
            },
            "balance_sheet": {
                "total_assets": _get_point(bs_df, "Assets", fy_label),
                "long_term_debt": lt_debt,
                "short_term_debt": {"value": st_debt_value, "period": fy_label},
                "cash_and_equivalents": cash,
                "short_term_investments": {"value": sti_value, "period": fy_label},
                # Para ROIC: equity es el componente que completa IC = Equity + Debt - Cash
                "stockholders_equity": (
                    _get_point(bs_df, "StockholdersEquity", fy_label)
                    if "StockholdersEquity" in bs_df.index else None
                ),
            },
            "shares": {
                # facts.shares_outstanding: propiedad directa en edgartools 5.x
                "shares_outstanding": {
                    "value": float(facts.shares_outstanding),
                    "period": fy_label,
                },
            },
        }

        log.info("edgar_fetch_complete", ticker=ticker, fy=fy_label)
        return raw

    @staticmethod
    def parse_financials(raw: dict[str, Any]) -> FinancialsReport:
        """Pure function — transforms raw dict into validated FinancialsReport."""
        as_of = date.fromisoformat(raw["as_of_date"])

        def _mv_list(items: list[dict], source: str = "XBRL") -> list[MetricValue]:
            return [
                MetricValue(value=i["value"], unit="USD", period=i["period"], source=source)
                for i in items
            ]

        def _mv(item: dict, source: str = "XBRL") -> MetricValue:
            return MetricValue(
                value=item["value"], unit="USD", period=item["period"], source=source
            )

        annual = raw["annual"]
        bs = raw["balance_sheet"]
        shares = raw["shares"]

        ocf_list = _mv_list(annual["operating_cash_flow"])
        capex_list = _mv_list(annual["capex"])
        fcf_list = [
            MetricValue(
                value=ocf.value - cap.value,
                unit="USD",
                period=ocf.period,
                source="calculated",
            )
            for ocf, cap in zip(ocf_list, capex_list)
        ]

        total_debt_value = bs["long_term_debt"]["value"] + bs["short_term_debt"]["value"]
        cash_value = (
            bs["cash_and_equivalents"]["value"] + bs["short_term_investments"]["value"]
        )
        period = bs["total_assets"]["period"]

        # --- ROIC calculation ---
        # effective_tax_rate = IncomeTaxExpenseBenefit / PreTaxIncome (most recent year)
        # Clamped to [0, 0.5] para evitar distorsiones por DTA/DTL o años con impuesto negativo.
        income_tax_list = _mv_list(annual.get("income_tax", []))
        pretax_list = _mv_list(annual.get("pretax_income", []))
        effective_tax_rate = 0.25  # fallback estándar si faltan datos
        if income_tax_list and pretax_list and pretax_list[0].value != 0:
            t = income_tax_list[0].value / pretax_list[0].value
            effective_tax_rate = max(0.0, min(t, 0.5))

        # Invested Capital = Equity + Debt − Cash (más reciente)
        # Nota: usamos el IC del año más reciente para toda la serie ROIC — aproximación
        # válida para análisis de tendencia, no para cálculos de precision point-in-time.
        se_raw = bs.get("stockholders_equity")
        equity_value = se_raw["value"] if se_raw else 0.0
        ic_value = equity_value + total_debt_value - cash_value

        stockholders_equity_mv = (
            MetricValue(value=equity_value, unit="USD", period=period, source="XBRL")
            if se_raw else None
        )
        invested_capital_mv = (
            MetricValue(value=ic_value, unit="USD", period=period, source="calculated")
            if ic_value > 0 else None
        )

        # ROIC series: NOPAT_year / IC_latest para cada año con operating_income
        op_inc_list = _mv_list(annual["operating_income"])
        roic_series: list[MetricValue] = []
        if ic_value > 0:
            for oi in op_inc_list:
                nopat = oi.value * (1.0 - effective_tax_rate)
                roic_series.append(MetricValue(
                    value=round(nopat / ic_value, 4),
                    unit="ratio",
                    period=oi.period,
                    source="calculated",
                ))

        return FinancialsReport(
            ticker=raw["ticker"],
            cik=raw["cik"],
            as_of_date=as_of,
            revenue=_mv_list(annual["revenue"]),
            gross_profit=_mv_list(annual["gross_profit"]),
            operating_income=_mv_list(annual["operating_income"]),
            net_income=_mv_list(annual["net_income"]),
            operating_cash_flow=ocf_list,
            capex=capex_list,
            free_cash_flow=fcf_list,
            total_assets=_mv(bs["total_assets"]),
            total_debt=MetricValue(
                value=total_debt_value, unit="USD", period=period, source="XBRL"
            ),
            cash_and_equivalents=MetricValue(
                value=cash_value, unit="USD", period=period, source="XBRL"
            ),
            shares_outstanding=MetricValue(
                value=shares["shares_outstanding"]["value"],
                unit="shares",
                period=shares["shares_outstanding"]["period"],
                source="XBRL",
            ),
            roic_series=roic_series,
            invested_capital=invested_capital_mv,
            stockholders_equity=stockholders_equity_mv,
            source_filing=raw["source_filing"],
        )


def get_financials(ticker: str) -> FinancialsReport:
    raw = EdgarAdapter.fetch_raw(ticker)
    return EdgarAdapter.parse_financials(raw)


class EdgarManagementAdapter:
    """
    Adapter especializado para obtener datos cualitativos de management desde EDGAR.

    Cubre las tres fuentes del EDGAR trifecta (ADR-012):
    - DEF 14A: proxy statement anual con compensación y ownership
    - Form 4: transacciones de insiders (compras/ventas de acciones)
    - 10-K Item 10: bios y perfiles de directores y officers

    Todos los métodos devuelven texto crudo (str) o listas de dicts — no Pydantic.
    La conversión a ManagementReport y Citations ocurre en el agente LLM consumidor.
    Los métodos son separables para poder mockearlos individualmente en tests.
    """

    @staticmethod
    def fetch_def14a_text(ticker: str, max_chars: int = 12000) -> str:
        """
        Obtiene el texto del proxy statement (DEF 14A) más reciente.

        DEF 14A contiene: compensación ejecutiva, % de ownership de insiders,
        composición del board, say-on-pay votes, y transacciones con partes
        relacionadas. Es la fuente primaria para §3 del checklist.

        Devuelve texto truncado a max_chars para no saturar el context window.
        Devuelve "" si no hay filings disponibles (falla silent para DEF 14A
        porque algunas empresas pequeñas pueden no tenerlo).
        """
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        filings = company.get_filings(form="DEF 14A")

        if not filings:
            log.warning("def14a_not_found", ticker=ticker)
            return ""

        filing = filings.latest()
        log.info("def14a_fetch", ticker=ticker, period=str(filing.period_of_report))

        try:
            # edgartools 5.x: filing.document es el documento primario del filing.
            # .text() extrae el contenido como texto plano (sin HTML).
            text = filing.document.text()
        except Exception as e:
            log.warning("def14a_text_extraction_failed", ticker=ticker, error=str(e))
            # Fallback: convertir el documento a string directamente
            try:
                text = str(filing.document)
            except Exception:
                return ""

        return text[:max_chars]

    @staticmethod
    def fetch_form4_transactions(ticker: str, months: int = 12) -> list[dict]:
        """
        Obtiene transacciones reales de compra/venta de insiders (Form 4) últimos N meses.

        Form 4 se archiva en la SEC dentro de los 2 días hábiles de cada transacción.
        Solo incluye operaciones de mercado abierto (código P=purchase, S=sale) —
        excluye ejercicios de opciones (M), retenciones fiscales (F) y otros códigos
        que no reflejan convicción del insider sobre el precio de la acción.

        La API real de edgartools 5.x:
            obj = filing.obj()        # → Form4 object
            df  = obj.to_dataframe()  # → DataFrame con columnas: Insider, Position,
                                      #   Transaction Type, Code, Shares, Price, Date, ...
        Filtrar Code in ['P', 'S'] da solo las compras/ventas reales en mercado abierto.

        Devuelve lista de dicts homogénea. Lista vacía si no hay transacciones P/S.
        """
        from datetime import date, timedelta
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)

        start_date = (date.today() - timedelta(days=months * 30)).isoformat()
        filings = company.get_filings(form="4", date=f"{start_date}:")

        if not filings:
            log.info("form4_no_filings", ticker=ticker, months=months)
            return []

        transactions = []
        for filing in list(filings[:20]):
            try:
                obj = filing.obj()
                df = obj.to_dataframe()

                if df.empty:
                    continue

                # Solo compras (P) y ventas (S) en mercado abierto — excluir M, F, etc.
                real_trades = df[df["Code"].isin(["P", "S"])]
                if real_trades.empty:
                    continue

                for _, row in real_trades.iterrows():
                    transactions.append({
                        "filer_name": str(row.get("Insider", "")),
                        "position": str(row.get("Position", "")),
                        "transaction_date": str(row.get("Date", ""))[:10],  # solo YYYY-MM-DD
                        "shares": float(row.get("Shares", 0) or 0),
                        "price_per_share": float(row.get("Price", 0) or 0),
                        "transaction_type": "buy" if row.get("Code") == "P" else "sell",
                    })

            except Exception as e:
                log.debug("form4_parse_failed", ticker=ticker, error=str(e))
                continue

        log.info("form4_fetch_complete", ticker=ticker, transactions=len(transactions))
        return transactions

    @staticmethod
    def fetch_10k_item10(ticker: str, max_chars: int = 8000) -> str:
        """
        Obtiene el Item 10 del 10-K (Directors, Executive Officers and Governance).

        En edgartools 5.x el TenK object expone Item 10 como el atributo
        `directors_officers_and_governance` (nombre semántico, no numérico).
        Nota: muchas large caps (ej. Apple) incorporan Item 10 por referencia a la
        DEF 14A, por lo que este campo devuelve solo el texto del cross-reference.
        En esos casos el contenido real está en fetch_def14a_text().

        Devuelve texto truncado a max_chars.
        """
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        filings = company.get_filings(form="10-K")

        if not filings:
            log.warning("10k_not_found_for_item10", ticker=ticker)
            return ""

        filing = filings.latest()
        log.info("10k_item10_fetch", ticker=ticker, period=str(filing.period_of_report))

        try:
            tenk = filing.obj()
            # edgartools 5.x: el atributo semántico para Item 10 es
            # directors_officers_and_governance (no item_10, que no existe)
            item10_text = str(tenk.directors_officers_and_governance or "")
        except Exception as e:
            log.warning("10k_item10_extraction_failed", ticker=ticker, error=str(e))
            return ""

        return item10_text[:max_chars]


class EdgarFilingsAdapter:
    """
    Adapter para obtener las secciones cualitativas del 10-K anual.

    Cubre las fuentes para §1 (Business) y §7 (Risks & Red Flags):
    - Item 1: Business — descripción del negocio, segmentos, fuentes de revenue.
    - Item 1A: Risk Factors — riesgos materiales declarados por la empresa.
    - Item 7: MD&A — análisis de la dirección sobre resultados y tendencias.

    Los tres métodos comparten el mismo patrón: obtener el 10-K más reciente,
    extraer la sección semántica via edgartools 5.x, y truncar para el contexto LLM.
    Se mockean individualmente en tests — ver tests/test_agents_1b.py.
    """

    @staticmethod
    def fetch_10k_business(ticker: str, max_chars: int = 8000) -> str:
        """
        Obtiene el Item 1 del 10-K (Business Description).

        Contiene: descripción del negocio, segmentos de producto/geografía,
        estrategia, competidores y propiedades. Es la fuente primaria para
        §1.1 (Two-minute story) y §1.2 (Revenue decomposition) del checklist.

        En edgartools 5.x: `tenk.business` devuelve el texto del Item 1.
        """
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        filings = company.get_filings(form="10-K")

        if not filings:
            log.warning("10k_not_found_for_business", ticker=ticker)
            return ""

        filing = filings.latest()
        log.info("10k_business_fetch", ticker=ticker, period=str(filing.period_of_report))

        try:
            tenk = filing.obj()
            text = str(tenk.business or "")
        except Exception as e:
            log.warning("10k_business_extraction_failed", ticker=ticker, error=str(e))
            return ""

        return text[:max_chars]

    @staticmethod
    def fetch_10k_risk_factors(ticker: str, max_chars: int = 10000) -> str:
        """
        Obtiene el Item 1A del 10-K (Risk Factors).

        Contiene los riesgos materiales que la empresa reconoce formalmente:
        regulatorios, competitivos, tecnológicos, macroeconómicos. Es la
        fuente primaria para §7.2 (Regulatory exposure) y §7.3 (Disruption)
        del checklist. Riesgos declarados aquí tienen peso legal — omitirlos
        expone a la empresa a responsabilidad civil.

        En edgartools 5.x: `tenk.risk_factors` devuelve el texto del Item 1A.
        """
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        filings = company.get_filings(form="10-K")

        if not filings:
            log.warning("10k_not_found_for_risk_factors", ticker=ticker)
            return ""

        filing = filings.latest()
        log.info("10k_risk_factors_fetch", ticker=ticker, period=str(filing.period_of_report))

        try:
            tenk = filing.obj()
            text = str(tenk.risk_factors or "")
        except Exception as e:
            log.warning("10k_risk_factors_extraction_failed", ticker=ticker, error=str(e))
            return ""

        return text[:max_chars]

    @staticmethod
    def fetch_10k_mda(ticker: str, max_chars: int = 10000) -> str:
        """
        Obtiene el Item 7 del 10-K (Management Discussion & Analysis).

        Contiene la narrativa de la dirección sobre resultados del año: qué
        impulsó los cambios en revenue/márgenes, guidance implícito, cambios
        en el balance, comparativa con año anterior. Es la fuente para §7.4
        (Accounting red flags) y §7.8 (Cycle position) del checklist.

        En edgartools 5.x: `tenk.management_discussion` devuelve el Item 7.
        """
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        filings = company.get_filings(form="10-K")

        if not filings:
            log.warning("10k_not_found_for_mda", ticker=ticker)
            return ""

        filing = filings.latest()
        log.info("10k_mda_fetch", ticker=ticker, period=str(filing.period_of_report))

        try:
            tenk = filing.obj()
            text = str(tenk.management_discussion or "")
        except Exception as e:
            log.warning("10k_mda_extraction_failed", ticker=ticker, error=str(e))
            return ""

        return text[:max_chars]


def format_form4_for_prompt(transactions: list[dict]) -> str:
    """
    Formatea transacciones Form 4 como tabla texto para inyectar en el prompt LLM.

    Solo incluye compras/ventas reales (P/S) — ejercicios y retenciones ya fueron
    filtrados en fetch_form4_transactions. Formato: insider | posición | acción |
    shares | precio | fecha. Legible para el LLM sin procesar JSON crudo.
    """
    if not transactions:
        return "No open-market insider purchases or sales found in the last 12 months."

    lines = ["Open-market insider transactions (last 12 months — P=buy, S=sell):"]
    for t in transactions:
        action = t["transaction_type"].upper()
        price_str = f"@ ${t['price_per_share']:.2f}" if t["price_per_share"] else "(price N/A)"
        lines.append(
            f"  {t['filer_name']} ({t['position']}) | {action} | "
            f"{t['shares']:,.0f} shares {price_str} | {t['transaction_date']}"
        )
    return "\n".join(lines)
