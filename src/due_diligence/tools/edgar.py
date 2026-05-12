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

        # Detect-and-fail-loud para empresas financieras (EC-35).
        # SIC 6000-6799: bancos, non-depository credit, brokers, insurance, RE, REITs, holding cos.
        # No usan "Revenue" — usan Net Interest Income, fees, premiums. Su framework de
        # valoración (ROE, P/Tangible Book, NIM, dividend discount) es estructuralmente
        # distinto al del sistema (DCF, EV/EBIT, ROIC). Mapear NII como "revenue" produciría
        # output con apariencia de validez pero indefendible — viola ADR-005 (fail loud).
        # Soporte de financials diferido a Phase 3+ (ver mce-evolution.md).
        sic = str(getattr(company, "sic", "") or "")
        industry = str(getattr(company, "industry", "") or "Unknown")
        if sic and sic[:1] == "6" and len(sic) >= 4:
            try:
                sic_int = int(sic[:4])
            except ValueError:
                sic_int = 0
            if 6000 <= sic_int <= 6799:
                raise DataUnavailableError(
                    f"{ticker} appears to be a financial firm (SIC: {sic} — {industry}). "
                    f"This system's analysis framework (DCF, EV/EBIT, ROIC) does NOT apply to "
                    f"banks, mortgage companies, insurers, brokers, or REITs — they require "
                    f"ROE-based valuation, Net Interest Margin analysis, and tangible book "
                    f"multiples. Producing output for AGM-like firms would be misleading "
                    f"(NII is not revenue, loans are not 'inventory'). Financial firms support "
                    f"is deferred to Phase 3+. Use traditional value investing analysis for now."
                )

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

        def _subtract_aligned(a_list: list[dict], b_list: list[dict]) -> list[dict]:
            """Resta b de a alineando por periodo. Solo emite periodos presentes en ambos.

            Usado para derivar EBIT cuando no hay concepto directo `OperatingIncomeLoss`:
            EBIT ≈ GrossProfit - SG&A, o EBIT ≈ Pretax - Interest.
            """
            b_by_period = {item["period"]: item["value"] for item in b_list}
            return [
                {"value": a["value"] - b_by_period[a["period"]], "period": a["period"]}
                for a in a_list
                if a["period"] in b_by_period
            ]

        def _compute_operating_income(inc_df: Any) -> list[dict]:
            """Extrae operating income (EBIT) con fallbacks múltiples (EC-21).

            Muchas small/mid caps no reportan `OperatingIncomeLoss` directamente —
            su income statement es Revenue → COGS → GrossProfit → SG&A → PretaxIncome
            → Tax → NetIncome. Sin EBIT, ROIC y EV/EBIT no se calculan, excluyendo
            silenciosamente esa clase de empresas del análisis.

            Cadena de fallbacks (por prevalencia):
                1. `OperatingIncomeLoss`              — concepto estándar (AAPL, large caps)
                2. `GrossProfit - SG&A`               — derivado para retailers/services (BBW)
                3. `OperatingIncomeLoss` con suma de    — variante alternativa que algunas empresas
                   `OperatingExpenses` y otros           usan; cubierto por la cadena
                4. `Pretax - InterestExpenseNet`      — fallback para empresas con interest separado
                5. `Pretax` (proxy)                   — última opción; sobreestima EBIT por interest
            """
            # Primary
            op_inc = _get_annual(inc_df, "OperatingIncomeLoss")
            if op_inc:
                return op_inc

            # Fallback 1: GrossProfit - SG&A (retailers/services con income statement simplificado)
            gp = _get_annual(inc_df, "GrossProfit")
            sga = _get_annual(inc_df, "SellingGeneralAndAdministrativeExpense")
            if gp and sga:
                derived = _subtract_aligned(gp, sga)
                if derived:
                    return derived

            # Fallback 2: PretaxIncome - InterestExpenseNonoperatingNet
            pretax = _get_annual(
                inc_df,
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            )
            interest = _get_annual(inc_df, "InterestIncomeExpenseNonoperatingNet")
            if pretax and interest:
                # InterestIncomeExpenseNonoperatingNet: positivo = neto income; negativo = neto expense
                # EBIT = Pretax - InterestIncomeNet (suma interest expense, resta interest income)
                derived = _subtract_aligned(pretax, interest)
                if derived:
                    return derived

            # Fallback 3: PretaxIncome solo (proxy — sobreestima EBIT por interest expense neto)
            if pretax:
                return pretax

            return []

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
                "operating_income": _compute_operating_income(inc_df),
                "net_income": _get_annual(inc_df, "NetIncomeLoss"),
                # Operating cash flow — intentar múltiples conceptos XBRL en orden de prevalencia.
                # Algunas empresas (educación, servicios, continuingOperations reporters)
                # usan variantes distintas del concepto estándar.
                "operating_cash_flow": (
                    _get_annual(cf_df, "NetCashProvidedByUsedInOperatingActivities")
                    or _get_annual(cf_df, "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")
                    or _get_annual(cf_df, "NetCashProvidedByOperatingActivities")
                    or _get_annual(cf_df, "CashGeneratedFromOperatingActivities")
                ),
                # Capex — distintas empresas usan distintos conceptos.
                # Empresas asset-light pueden no reportar capex como línea separada;
                # en ese caso la lista queda vacía y FCF = OCF (aproximación válida).
                "capex": (
                    _get_annual(cf_df, "PaymentsToAcquirePropertyPlantAndEquipment")
                    or _get_annual(cf_df, "PaymentsForCapitalExpenditures")
                    or _get_annual(cf_df, "CapitalExpendituresIncurringNoCapitalLeases")
                    or _get_annual(cf_df, "PurchaseOfPropertyPlantAndEquipment")
                    or []  # asset-light con zero capex reportado: FCF ≈ OCF
                ),
                # Para ROIC: impuesto + pretax income permiten calcular effective_tax_rate
                "income_tax": _get_annual(inc_df, "IncomeTaxExpenseBenefit"),
                "pretax_income": _get_annual(
                    inc_df,
                    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                ),
                # Capital returns (ADR-023, EC-19) — central para tesis de value investing.
                # Ambos vienen del cash flow statement (Financing Activities). XBRL los reporta
                # como negativos (salidas de caja); aquí los pasamos a positivos en _mv_list_abs.
                "buybacks": (
                    _get_annual(cf_df, "PaymentsForRepurchaseOfCommonStock")
                    or _get_annual(cf_df, "PaymentsForRepurchaseOfEquity")
                    or []
                ),
                "dividends": (
                    _get_annual(cf_df, "PaymentsOfDividendsCommonStock")
                    or _get_annual(cf_df, "PaymentsOfDividends")
                    or []
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
                    "value": float(facts.shares_outstanding) if facts.shares_outstanding is not None else None,
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

        def _mv_list_abs(items: list[dict], source: str = "XBRL") -> list[MetricValue]:
            """Variante de _mv_list que normaliza valores a positivo.
            Usado para buybacks/dividends: XBRL los reporta como negativos
            (salidas de caja en el cash flow) — los almacenamos como flujos
            de capital devuelto en magnitud absoluta para legibilidad."""
            return [
                MetricValue(value=abs(i["value"]), unit="USD", period=i["period"], source=source)
                for i in items
            ]

        ocf_list = _mv_list(annual["operating_cash_flow"])
        capex_list = _mv_list(annual["capex"])
        buybacks_list = _mv_list_abs(annual.get("buybacks", []))
        dividends_list = _mv_list_abs(annual.get("dividends", []))

        if ocf_list and capex_list:
            # Caso normal: ambos disponibles — FCF = OCF - Capex
            # zip trunca al más corto si difieren en longitud (periodos distintos)
            fcf_list = [
                MetricValue(
                    value=ocf.value - cap.value,
                    unit="USD",
                    period=ocf.period,
                    source="calculated",
                )
                for ocf, cap in zip(ocf_list, capex_list)
            ]
        elif ocf_list:
            # Capex no disponible en XBRL (empresas asset-light / servicios puros).
            # FCF ≈ OCF es una aproximación válida cuando capex es negligible.
            # Se marca source="calculated-no-capex" para que sea auditable.
            log.debug("fcf_no_capex_data", ticker=raw.get("ticker"), note="FCF ≈ OCF (capex not in XBRL)")
            fcf_list = [
                MetricValue(
                    value=ocf.value,
                    unit="USD",
                    period=ocf.period,
                    source="calculated-no-capex",
                )
                for ocf in ocf_list
            ]
        else:
            fcf_list = []

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

        # Invested Capital — fórmula con fallback (ADR-023, EC-18).
        # Primaria: Equity + Debt − Cash (balance sheet view, capital aportado por financiadores).
        # Fallback: Total Assets − Cash (operating asset view, robusto a buybacks agresivos).
        #
        # Por qué el fallback: empresas que devuelven mucho capital al accionista
        # (Domino's, AutoZone, BBW post-buybacks) acaban con equity reducido o negativo
        # por treasury stock acumulado. La fórmula primaria entonces da un IC
        # cercano a cero → ROIC explota o no se calcula. Total Assets − Cash representa
        # la base de activos operativos real, que persiste independiente del capital structure.
        se_raw = bs.get("stockholders_equity")
        equity_value = se_raw["value"] if se_raw else 0.0
        total_assets_value = bs["total_assets"]["value"] if bs.get("total_assets") else 0.0

        ic_primary = equity_value + total_debt_value - cash_value
        ic_fallback = total_assets_value - cash_value if total_assets_value > 0 else 0.0

        # Threshold: la primaria es "degenerada" si < 10% del operating capital.
        # Esto detecta el patrón buyback-heavy donde equity queda artificialmente bajo.
        threshold = 0.10 * ic_fallback if ic_fallback > 0 else 0.0

        if ic_primary > threshold and ic_primary > 0:
            ic_value = ic_primary
            ic_method = "equity+debt-cash"
        elif ic_fallback > 0:
            ic_value = ic_fallback
            ic_method = "total_assets-cash (fallback for low/negative book equity)"
        else:
            ic_value = 0.0
            ic_method = None

        stockholders_equity_mv = (
            MetricValue(value=equity_value, unit="USD", period=period, source="XBRL")
            if se_raw else None
        )
        invested_capital_mv = (
            MetricValue(value=ic_value, unit="USD", period=period, source=ic_method or "calculated")
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
            buybacks=buybacks_list,
            dividends=dividends_list,
            total_assets=_mv(bs["total_assets"]),
            total_debt=MetricValue(
                value=total_debt_value, unit="USD", period=period, source="XBRL"
            ),
            cash_and_equivalents=MetricValue(
                value=cash_value, unit="USD", period=period, source="XBRL"
            ),
            shares_outstanding=(
                # Construcción condicional (EC-34): NO crear MetricValue con value=None.
                # Si XBRL no reporta shares (small caps con dual-class como CRD.A, ADRs),
                # devolver None y dejar que valuation_agent derive shares de market_cap/price.
                MetricValue(
                    value=shares["shares_outstanding"]["value"],
                    unit="shares",
                    period=shares["shares_outstanding"]["period"],
                    source="XBRL",
                )
                if shares.get("shares_outstanding")
                and shares["shares_outstanding"].get("value") is not None
                else None
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
    def fetch_def14a_text(ticker: str, max_chars: int = 24000) -> str:
        """
        Obtiene el texto del proxy statement (DEF 14A) más reciente con extracción
        targeting de secciones críticas (EC-25).

        Por qué la extracción targeting:
            DEF 14A típico de Russell 2000 = 50-80 páginas. Solo truncar a
            max_chars fallaba porque la tabla "Security Ownership of Certain
            Beneficial Owners and Management" aparece típicamente en páginas 30-40
            (después de compensación, antes de auditoría). El inicio del proxy
            cubre intro + elección directores + governance — útil pero no contiene
            insider ownership %.

            Esta versión extrae:
              1. Primeros 6K chars (intro, board composition, governance highlights)
              2. Sección "Security Ownership..." o "Beneficial Ownership" + 4K chars
                 alrededor (la tabla con %)
              3. Sección "Compensation Discussion and Analysis" o "Executive
                 Compensation" + 4K chars (estructura comp + pay alignment)
            Si no se encuentran las secciones, fallback al primer max_chars.

        Returns: texto extraído, máximo max_chars (default 18K para acomodar
            las 3 secciones sin saturar context window).
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
            text = filing.document.text()
        except Exception as e:
            log.warning("def14a_text_extraction_failed", ticker=ticker, error=str(e))
            try:
                text = str(filing.document)
            except Exception:
                return ""

        # Extracción targeting de secciones críticas
        sections: list[str] = []

        # Sección 1: inicio (intro + governance + board)
        sections.append(text[:6000])

        # Sección 2: tabla de ownership (multiples encabezados posibles).
        # Buscar TODAS las ocurrencias y usar la ÚLTIMA (la primera suele ser el TOC).
        # EC-31: el bug detectado en BBW fue que el extractor capturaba el TOC en lugar
        # de la tabla real porque usaba .find() (primer match).
        ownership_markers = [
            "Security Ownership of Certain Beneficial Owners",
            "Security Ownership of Management",
            "Beneficial Ownership",
            "Stock Ownership",
            "Shares Beneficially Owned",
        ]
        text_lower = text.lower()
        for marker in ownership_markers:
            marker_lower = marker.lower()
            # find_all manual: localizar todas las posiciones
            positions = []
            start = 0
            while True:
                idx = text_lower.find(marker_lower, start)
                if idx < 0:
                    break
                positions.append(idx)
                start = idx + 1
            if positions:
                # Última ocurrencia = sección real (no TOC)
                last_idx = positions[-1]
                section_start = max(0, last_idx - 500)
                # 8000 chars window captura el header + intro + tabla completa
                # (las tablas de ownership pueden ocupar 3-5 páginas con rows por director/exec)
                section_end = min(len(text), last_idx + 8000)
                sections.append(f"\n\n[OWNERSHIP SECTION]\n{text[section_start:section_end]}")
                break

        # Sección 3: compensación — mismo patrón (última ocurrencia)
        comp_markers = [
            "Compensation Discussion and Analysis",
            "Executive Compensation",
            "Summary Compensation Table",
        ]
        for marker in comp_markers:
            marker_lower = marker.lower()
            positions = []
            start = 0
            while True:
                idx = text_lower.find(marker_lower, start)
                if idx < 0:
                    break
                positions.append(idx)
                start = idx + 1
            if positions:
                last_idx = positions[-1]
                section_start = max(0, last_idx - 500)
                section_end = min(len(text), last_idx + 4500)
                sections.append(f"\n\n[COMPENSATION SECTION]\n{text[section_start:section_end]}")
                break

        combined = "".join(sections)
        # Si la combinación es < max_chars, devolver tal cual.
        # Si excede, truncar respetando el orden (intro → ownership → comp).
        return combined[:max_chars] if combined else text[:max_chars]

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
        filing = _get_latest_full_10k(company)  # excluye 10-K/A (EC-27)

        if not filing:
            log.warning("10k_not_found_for_item10", ticker=ticker)
            return ""

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


def _get_latest_full_10k(company):
    """
    Devuelve el último 10-K **original** (no amendment), excluyendo 10-K/A.

    Por qué este helper (EC-27): cuando una empresa enmienda su 10-K, presenta un
    "10-K/A" (amendment). Estos filings típicamente contienen solo las secciones
    enmendadas (e.g., Part IV exhibits), NO el 10-K completo con Items 1A/7. Si
    el sistema usa `filings.latest()` con `form="10-K"`, edgartools incluye 10-K/A
    en los resultados — y `tenk.risk_factors`, `tenk.business`, `tenk.mda` devuelven
    vacío silenciosamente.

    Verificado en vivo con BBW (2026-05-10): último filing era un 10-K/A con solo
    Part IV; Item 1A inaccesible vía cualquier path.

    Returns: el filing 10-K original más reciente, o None si no hay.
    """
    filings = company.get_filings(form="10-K")
    if not filings:
        return None
    # Excluir 10-K/A (amendments) — sus secciones suelen estar incompletas.
    # edgartools devuelve el form como "10-K" o "10-K/A".
    for f in filings:
        if str(f.form).strip().upper() == "10-K":
            return f
    # Si todos son /A (caso extremo), devolver el más reciente — degraded mode.
    log.warning("only_10ka_amendments_found", count=len(list(filings)))
    return filings.latest()


class EdgarFilingsAdapter:
    """
    Adapter para obtener las secciones cualitativas del 10-K anual.

    Cubre las fuentes para §1 (Business) y §7 (Risks & Red Flags):
    - Item 1: Business — descripción del negocio, segmentos, fuentes de revenue.
    - Item 1A: Risk Factors — riesgos materiales declarados por la empresa.
    - Item 7: MD&A — análisis de la dirección sobre resultados y tendencias.

    Los tres métodos comparten el mismo patrón: obtener el 10-K original más reciente
    (excluyendo 10-K/A — ver `_get_latest_full_10k`), extraer la sección semántica
    via edgartools 5.x, y truncar para el contexto LLM. Se mockean individualmente
    en tests — ver tests/test_agents_1b.py.
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
        filing = _get_latest_full_10k(company)  # excluye 10-K/A (EC-27)

        if not filing:
            log.warning("10k_not_found_for_business", ticker=ticker)
            return ""

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
        Obtiene el Item 1A del 10-K (Risk Factors) con fallback regex (EC-27).

        Contiene los riesgos materiales que la empresa reconoce formalmente:
        regulatorios, competitivos, tecnológicos, macroeconómicos. Es la
        fuente primaria para §7.2 (Regulatory exposure) y §7.3 (Disruption)
        del checklist.

        Estrategia con dos pasos:
            1. Primary: `tenk.risk_factors` de edgartools (rápido cuando funciona).
            2. Fallback: regex sobre el texto completo del filing — busca encabezado
               "Item 1A" y extrae hasta "Item 1B" o "Item 2". Necesario porque
               edgartools 5.x falla en parsear Item 1A para muchos 10-K (BBW,
               retailers, small caps con markup no estándar — verificado en vivo).

        Esto sigue el mismo patrón que EC-21 (operating income con fallbacks):
        no asumir que un parser único funciona para todo el universo de filers.
        """
        import re
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        filing = _get_latest_full_10k(company)  # excluye 10-K/A (EC-27)

        if not filing:
            log.warning("10k_not_found_for_risk_factors", ticker=ticker)
            return ""

        log.info("10k_risk_factors_fetch", ticker=ticker, period=str(filing.period_of_report))

        # Step 1: primary — parser de edgartools
        try:
            tenk = filing.obj()
            text = str(tenk.risk_factors or "")
            if text and len(text) > 500:  # threshold mínimo para considerar válido
                return text[:max_chars]
        except Exception as e:
            log.warning("10k_risk_factors_primary_failed", ticker=ticker, error=str(e))

        # Step 2: fallback — regex sobre el texto completo del filing
        try:
            full_text = filing.document.text() if hasattr(filing, "document") else ""
            if not full_text:
                full_text = str(filing.text() if hasattr(filing, "text") else "")
            if not full_text:
                log.warning("10k_risk_factors_no_full_text", ticker=ticker)
                return ""

            # Buscar el header "Item 1A" (con variantes de capitalización y separadores).
            # Un 10-K típico tiene varios "Item 1A" matches (índice + cuerpo); tomar el
            # primero que esté seguido por "Risk Factors" en la siguiente línea o cerca.
            # Patrón: "Item 1A" + opcional ". " + "Risk Factors" en los siguientes 100 chars
            start_pattern = re.compile(
                r"Item\s*1A\.?\s*[\.\:\s—-]*\s*Risk\s*Factors",
                re.IGNORECASE,
            )
            # Extraer la última ocurrencia (la primera suele ser TOC/índice).
            matches = list(start_pattern.finditer(full_text))
            if not matches:
                log.warning("10k_risk_factors_regex_no_match", ticker=ticker)
                return ""
            start_idx = matches[-1].end()  # última = cuerpo, no índice

            # Buscar el final: próximo "Item 1B" o "Item 2" después de start_idx
            end_pattern = re.compile(
                r"Item\s*(1B|2)\.?\s",
                re.IGNORECASE,
            )
            end_match = end_pattern.search(full_text, start_idx)
            end_idx = end_match.start() if end_match else min(start_idx + 50000, len(full_text))

            extracted = full_text[start_idx:end_idx].strip()
            log.info(
                "10k_risk_factors_regex_extracted",
                ticker=ticker,
                length=len(extracted),
            )
            return extracted[:max_chars]

        except Exception as e:
            log.warning("10k_risk_factors_fallback_failed", ticker=ticker, error=str(e))
            return ""

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
        filing = _get_latest_full_10k(company)  # excluye 10-K/A (EC-27)

        if not filing:
            log.warning("10k_not_found_for_mda", ticker=ticker)
            return ""

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


class EdgarEventsAdapter:
    """
    Adapter para obtener eventos materiales 8-K de los últimos N meses.

    Los 8-K son filings ad-hoc que las empresas presentan ante la SEC cuando
    ocurre un evento material entre los filings periódicos (10-K, 10-Q). Son
    la fuente primary-source para detectar eventos críticos sin depender de APIs
    de noticias comerciales. ADR-017.

    Items cubiertos por defecto:
        - 2.02: Results of Operations (earnings release)
        - 4.02: Non-Reliance on Previously Issued Financial Statements (RESTATEMENT)
        - 5.02: Departure/Appointment of Directors and Officers

    Item 4.02 es señal roja crítica — indica restatement contable. Si aparece
    en el lookback window, el Value Expert debe degradar el VERDICT hacia SELL/HOLD.
    """

    @staticmethod
    def fetch_8k_events(
        ticker: str,
        items: list[str] | None = None,
        months: int = 12,
    ) -> list[dict]:
        """
        Obtiene eventos 8-K materiales del ticker en la ventana especificada.

        Args:
            ticker: Ticker de la empresa (e.g. "AAPL").
            items: Items 8-K a filtrar. Default: ["2.02", "4.02", "5.02"].
            months: Ventana de búsqueda en meses. Default: 12 (año fiscal completo).

        Returns:
            list[dict] con campos: filing_date, item_numbers, summary_text, filing_url.
            Lista vacía si no hay eventos relevantes o todos los filings fallan.
        """
        if items is None:
            items = ["2.02", "4.02", "5.02"]

        from datetime import date, timedelta
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        start_date = (date.today() - timedelta(days=months * 30)).isoformat()

        try:
            filings = list(company.get_filings(form="8-K", date=f"{start_date}:"))
        except Exception as e:
            log.warning("edgar_events_filings_fetch_failed", ticker=ticker, error=str(e))
            return []

        # Normalizar el filter: edgartools devuelve "Item X.YY" pero el filter recibe
        # "X.YY". Format mismatch silencioso = todos los filings filtrados out (EC-30).
        # Aceptamos ambos formatos en el filter.
        items_normalized = {i.replace("Item ", "").strip().lower() for i in items}

        events: list[dict] = []
        for f in filings:
            try:
                obj = f.obj()
                filed_items_raw = list(getattr(obj, "items", []) or [])
                # Normalizar items del filing: strip "Item " prefix para comparar
                matching = [
                    i for i in filed_items_raw
                    if i.replace("Item ", "").strip().lower() in items_normalized
                ]
                if not matching:
                    continue
                summary = str(getattr(obj, "summary", "") or "")[:600]
                events.append({
                    "filing_date": str(f.filing_date),
                    "item_numbers": matching,
                    "summary_text": summary,
                    "filing_url": getattr(f, "filing_url", ""),
                })
            except Exception as e:
                log.debug(
                    "edgar_events_filing_parse_failed",
                    ticker=ticker,
                    error=str(e),
                )
                continue

        log.info("edgar_events_fetched", ticker=ticker, count=len(events), months=months)
        return events
