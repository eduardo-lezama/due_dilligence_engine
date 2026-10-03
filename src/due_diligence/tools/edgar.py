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
from due_diligence.schemas.reports import QuarterlySnapshot

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

        def _get_from_facts(concept: str, period: str) -> dict | None:
            """Raw facts fallback bypassing edgartools' curated views (EC-37).

            edgartools `balance_sheet().to_dataframe()` y `income_statement().to_dataframe()`
            son vistas curadas — algunas empresas (MOS verificado en producción) tienen
            conceptos us-gaap estándar en sus raw `_facts` pero excluidos del DataFrame
            curado. Cuando los extractores normales fallan, este helper consulta directo
            al raw fact con `facts.get_annual_fact()` usando el prefijo `us-gaap:`.

            Valida que el fiscal_year del fact retornado coincida con el target
            (get_annual_fact devuelve la última anual conocida — para conceptos
            descontinuados puede ser stale).

            Args:
                concept: Concept name SIN prefijo (ej. "LongTermDebtAndCapitalLeaseObligations")
                period: Target FY label (ej. "FY2025")

            Returns:
                {"value": float, "period": str} o None si no encontrado / año stale.
            """
            target_year = period.replace("FY", "")  # "FY2025" → "2025"
            full_concept = f"us-gaap:{concept}"
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")  # silenciar "Unknown concept" warnings
                    fact = facts.get_annual_fact(full_concept)
            except Exception:
                return None
            if fact is None:
                return None
            # Validar fiscal year — get_annual_fact devuelve el último anual conocido,
            # que puede ser stale (e.g. LongTermDebt FY2015 para una empresa que ahora
            # usa LongTermDebtAndCapitalLeaseObligations).
            if str(getattr(fact, "fiscal_year", "")) != target_year:
                return None
            value = getattr(fact, "numeric_value", None)
            if value is None:
                return None
            return {"value": float(value), "period": period}

        def _extract_balance_concept(
            concepts: tuple[str, ...], period: str, kind: str
        ) -> dict | None:
            """Extrae un concepto del balance sheet con cadena de fallback (EC-37).

            Estrategia en dos pasos:
                1. Vista curada `bs_df` (rápido, funciona para la mayoría)
                2. Raw facts via `_get_from_facts` (fallback para filers con XBRL atípico)

            Loguea cuándo se usa el fallback — útil para diagnosticar empresas problemáticas.

            Args:
                concepts: Tupla de concepts a probar en orden de prevalencia.
                period: Target FY label.
                kind: Etiqueta para logging (e.g. "lt_debt", "st_debt").

            Returns:
                Primer match (dict) o None si ningún concept devuelve dato.
            """
            # Step 1: curated view
            for c in concepts:
                if c in bs_df.index:
                    try:
                        return _get_point(bs_df, c, period)
                    except DataUnavailableError:
                        pass
            # Step 2: raw facts fallback
            for c in concepts:
                result = _get_from_facts(c, period)
                if result is not None:
                    log.info(
                        "balance_raw_facts_fallback_used",
                        concept=c, ticker=ticker, kind=kind,
                    )
                    return result
            return None

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
        # Cadena de fallback XBRL para revenue — distintas empresas usan distintos conceptos
        # según su industry y cuándo adoptaron ASC 606 (EC-67: HRL usa SalesRevenueNet).
        # La cadena quarterly (línea ~701) ya tenía más fallbacks; se iguala aquí.
        revenue = (
            _get_annual(inc_df, "RevenueFromContractWithCustomerExcludingAssessedTax")
            or _get_annual(inc_df, "Revenues")
            or _get_annual(inc_df, "SalesRevenueNet")                                    # legacy pre-ASC 606 (HRL, CPG)
            or _get_annual(inc_df, "RevenueFromContractWithCustomerIncludingAssessedTax") # ASC 606 con IVA incluido
            or _get_annual(inc_df, "SalesRevenueGoodsNet")                               # product-only (algunos CPG/retail)
            or _get_annual(inc_df, "NetSales")                                           # algunos industriales/food
        )
        if not revenue:
            raise DataUnavailableError(f"{ticker}: revenue concept not found in income statement")

        # --- Balance sheet ---
        # LongTermDebt: cadena de fallback de conceptos XBRL (EC-37).
        # MOS (verificado en producción) usa `LongTermDebtAndCapitalLeaseObligations`
        # que NO está en el bs_df curado de edgartools aunque SÍ está en raw _facts.
        # _extract_balance_concept aplica fallback en 2 niveles: curated view → raw facts.
        _LT_DEBT_CONCEPTS = (
            "LongTermDebt",
            "LongTermDebtNoncurrent",
            "LongTermDebtAndCapitalLeaseObligations",
            "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
            "LongTermNotesPayable",
        )
        lt_debt = _extract_balance_concept(_LT_DEBT_CONCEPTS, fy_label, "lt_debt")
        if lt_debt is None:
            lt_debt = {"value": 0.0, "period": fy_label}

        # ShortTermBorrowings + porción corriente de LTD (opcional — no todas las empresas).
        # Sumar concepts mutually-exclusive en orden de relevancia. Para cada concept:
        # primero curated view, luego raw facts fallback.
        _ST_DEBT_CONCEPTS = (
            "ShortTermBorrowings",
            "LongTermDebtCurrent",
            "LongTermDebtAndCapitalLeaseObligationsCurrent",
            "NotesPayableCurrent",
            "DebtCurrent",
        )
        st_debt_value = 0.0
        for _st_concept in _ST_DEBT_CONCEPTS:
            val = None
            if _st_concept in bs_df.index:
                try:
                    val = _get_point(bs_df, _st_concept, fy_label)["value"]
                except DataUnavailableError:
                    pass
            if val is None:
                # Raw facts fallback (EC-37)
                result = _get_from_facts(_st_concept, fy_label)
                if result is not None:
                    val = result["value"]
                    log.info(
                        "balance_raw_facts_fallback_used",
                        concept=_st_concept, ticker=ticker, kind="st_debt_component",
                    )
            if val:
                st_debt_value += val

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


    @staticmethod
    def parse_quarterly_snapshot(raw: dict[str, Any]) -> QuarterlySnapshot:
        """
        Pure function — transforma raw dict en QuarterlySnapshot validado.

        Separada de fetch_quarterly_snapshot para seguir el patrón fetch_raw/parse_*
        del proyecto: la lógica de parsing es determinista y testeable con fixtures
        sin llamar a EDGAR (igual que parse_financials para el 10-K anual).

        Args:
            raw: Dict con keys {period, revenue, operating_income?, gross_profit?, filing_date?}

        Returns:
            QuarterlySnapshot con contrato Pydantic validado.
        """
        return QuarterlySnapshot(
            period=raw["period"],
            revenue=raw["revenue"],
            operating_income=raw.get("operating_income"),
            gross_profit=raw.get("gross_profit"),
            filing_date=raw.get("filing_date"),
        )

    @staticmethod
    def fetch_quarterly_snapshot(
        ticker: str,
        tenk_as_of: date | None = None,
        staleness_threshold_days: int = 45,
    ) -> QuarterlySnapshot | None:
        """
        Obtiene métricas clave del 10-Q más reciente si el 10-K está stale (EC-44/MCE-24).

        Solo busca 10-Q si el 10-K tiene >staleness_threshold_days de antigüedad.
        Extrae revenue, operating_income, gross_profit del quarter más reciente.

        45 días (vs el anterior 90): con 90 días una empresa que publica el 10-K en
        marzo quedaba "fresca" hasta junio, saltándose Q1 earnings (publicados en
        abril/mayo). 45 días fuerza la verificación tras cada quarter regular.

        Args:
            ticker: Ticker de la empresa.
            tenk_as_of: Fecha del 10-K más reciente. Si None, siempre intenta.
            staleness_threshold_days: Días de antigüedad del 10-K para activar fetch.

        Returns:
            dict con {period, revenue, operating_income, gross_profit, filing_date} o None.
        """
        if tenk_as_of is not None:
            age_days = (date.today() - tenk_as_of).days
            if age_days <= staleness_threshold_days:
                log.debug(
                    "quarterly_snapshot_skipped_fresh_10k",
                    ticker=ticker,
                    tenk_age_days=age_days,
                )
                return None

        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)

        try:
            q_fin = company.get_quarterly_financials()
            inc = q_fin.income_statement()
            inc_df = inc.to_dataframe()
        except Exception as e:
            log.warning("quarterly_snapshot_fetch_failed", ticker=ticker, error=str(e))
            return None

        # Columnas trimestrales: 'Q1 2026', 'Q4 2025', etc. — tomar la más reciente
        q_cols = sorted(
            [c for c in inc_df.columns if c.startswith("Q")],
            reverse=True,
        )
        if not q_cols:
            log.info("quarterly_snapshot_no_quarters", ticker=ticker)
            return None

        latest_q = q_cols[0]

        def _get_val(df: Any, concepts: list[str]) -> float | None:
            """Extrae valor del primer concepto que exista en el DataFrame."""
            for concept in concepts:
                if concept not in df.index:
                    continue
                row = df.loc[concept]
                if row.ndim == 2:
                    row = row.iloc[0]
                val = row.get(latest_q)
                if val is not None:
                    import pandas as pd
                    if pd.notna(val):
                        return float(val)
            return None

        snapshot = {
            "period": latest_q,
            "revenue": _get_val(inc_df, [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",                                     # legacy pre-ASC 606 (EC-67)
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueGoodsNet",                                # product-only CPG/retail
                "NetSales",                                            # algunos industriales/food
            ]),
            "operating_income": _get_val(inc_df, [
                "OperatingIncomeLoss",
            ]),
            "gross_profit": _get_val(inc_df, [
                "GrossProfit",
            ]),
        }

        # Solo devolver si al menos revenue está presente
        if snapshot["revenue"] is None:
            log.info("quarterly_snapshot_no_revenue", ticker=ticker, period=latest_q)
            return None

        log.info(
            "quarterly_snapshot_fetched",
            ticker=ticker,
            period=latest_q,
            revenue=snapshot["revenue"],
        )
        return EdgarAdapter.parse_quarterly_snapshot(snapshot)


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

        Cada transacción incluye `is_institutional` (bool) que indica si el filer es
        un beneficial owner institucional (>10% holder tipo LP, LLC, Fund, hedge fund)
        vs un officer/director legítimo. Esto permite al downstream separar "insider
        sentiment" (officers/directors con skin in the game) de "institutional flow"
        (fondos tomando profits o rebalanceando). EC-43.

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

        # Ordenar newest-first antes del cap: garantiza que el límite de 50 no corte
        # actividad reciente si EDGAR devuelve filings en orden variable (EC-61).
        # Cap aumentado de 20 → 50: large caps con múltiples insiders activos pueden
        # superar 20 Form 4s en 12 meses (PATH: CEO + varios directores).
        all_filings = list(filings[:50])
        try:
            all_filings.sort(key=lambda f: f.filing_date, reverse=True)
        except Exception:
            pass  # Si filing_date no disponible, usar el orden original de EDGAR

        transactions = []
        for filing in all_filings:
            try:
                obj = filing.obj()
                df = obj.to_dataframe()

                if df.empty:
                    continue

                # Solo compras (P) y ventas (S) en mercado abierto — excluir M, F, etc.
                real_trades = df[df["Code"].isin(["P", "S"])]
                if real_trades.empty:
                    continue

                # 10b5-1 plan detection: si el filing menciona "10b5-1" en footnotes
                # o body, todas las transacciones de este filing son pre-arranged y
                # NO informativas sobre insider sentiment. (EC-50)
                is_10b5_1 = _detect_10b5_1_plan(filing, obj)

                for _, row in real_trades.iterrows():
                    filer_name = str(row.get("Insider", ""))
                    position = str(row.get("Position", ""))
                    transactions.append({
                        "filer_name": filer_name,
                        "position": position,
                        "transaction_date": str(row.get("Date", ""))[:10],  # solo YYYY-MM-DD
                        "shares": float(row.get("Shares", 0) or 0),
                        "price_per_share": float(row.get("Price", 0) or 0),
                        "transaction_type": "buy" if row.get("Code") == "P" else "sell",
                        "is_institutional": _is_institutional_holder(filer_name, position),
                        "is_10b5_1_plan": is_10b5_1,
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


# ---------- Form 4 institutional holder detection (EC-43) ---------- #

# Palabras clave en nombre o posición que indican beneficial owner institucional
# (hedge fund, LP, activist, etc.) vs officer/director legítimo.
_INSTITUTIONAL_NAME_KEYWORDS = {
    "partners", "capital", "management", "advisors", "fund", "holdings",
    "investments", "asset", "ventures", "group", "associates",
}
_INSTITUTIONAL_SUFFIX_KEYWORDS = {"lp", "llc", "llp", "ltd", "inc", "corp"}
_OFFICER_POSITION_KEYWORDS = {
    "ceo", "cfo", "coo", "cto", "cio", "president", "vp", "vice president",
    "director", "officer", "general counsel", "secretary", "treasurer",
    "controller", "svp", "evp", "chief",
}


def _detect_10b5_1_plan(filing: object, obj: object) -> bool:
    """
    Detecta si un Form 4 filing reporta transacciones bajo un plan 10b5-1 (EC-50).

    Las ventas bajo planes 10b5-1 son pre-programadas y NO reflejan la visión actual
    del insider sobre la empresa. La decisión de vender se tomó meses antes.

    Estrategia de detección (3 layers):
    1. Footnotes del objeto Form 4 (obj.footnotes si existe)
    2. Texto completo del filing (filing.text() o filing primary document)
    3. Campo 'TransactionTimeliness' o 'isDirectorTenPercentOwner' hints

    Returns True si hay evidencia de plan 10b5-1.
    """
    _10B5_PATTERNS = (
        "10b5-1", "rule 10b5", "10b-5-1", "trading plan", "pre-arranged",
        # Variantes observadas en Form 4 modernos post-2023 SEC amendment (EC-61):
        # UiPath y otros large caps usan estas frases alternativas en footnotes/body.
        "10b5(1)", "rule 10b-5", "prearranged", "predetermined plan", "automatic plan",
    )

    # Layer 1: footnotes del parsed object
    try:
        footnotes = getattr(obj, "footnotes", None)
        if footnotes:
            fn_text = str(footnotes).lower() if footnotes else ""
            if any(p in fn_text for p in _10B5_PATTERNS):
                return True
    except Exception:
        pass

    # Layer 2: texto del filing (HTML/XML) — buscar la cadena directamente
    try:
        # edgartools: filing tiene .text() o acceso al documento primario
        filing_text = ""
        if callable(getattr(filing, "text", None)):
            filing_text = (filing.text() or "")[:10000].lower()
        elif hasattr(filing, "attachments"):
            primary = getattr(filing.attachments, "primary_document", None)
            if primary and callable(getattr(primary, "text", None)):
                filing_text = (primary.text() or "")[:10000].lower()

        if filing_text and any(p in filing_text for p in _10B5_PATTERNS):
            return True
    except Exception:
        pass

    return False


def _is_institutional_holder(filer_name: str, position: str) -> bool:
    """
    Detecta si un filer de Form 4 es un beneficial owner institucional (>10% holder)
    vs un officer/director legítimo con skin in the game.

    Heurística:
    1. Si la posición contiene un título de officer/director → NO institucional.
    2. Si la posición dice "10% Owner" y el nombre contiene keywords institucionales → SÍ.
    3. Si el nombre contiene sufijos corporativos (LP, LLC, Ltd) → SÍ.

    Esto NO excluye las transacciones — solo las marca para que el LLM las interprete
    correctamente como "institutional flow" vs "insider sentiment".
    """
    pos_lower = position.lower()
    name_lower = filer_name.lower()

    # Si tiene título ejecutivo/director → insider legítimo
    if any(kw in pos_lower for kw in _OFFICER_POSITION_KEYWORDS):
        return False

    # Sufijos corporativos en nombre → institucional
    name_tokens = set(name_lower.replace(",", "").replace(".", "").split())
    if name_tokens & _INSTITUTIONAL_SUFFIX_KEYWORDS:
        return True

    # "10% owner" + keywords de fondo en nombre → institucional
    if "10%" in pos_lower or "owner" in pos_lower:
        if any(kw in name_lower for kw in _INSTITUTIONAL_NAME_KEYWORDS):
            return True

    return False


def format_form4_for_prompt(transactions: list[dict]) -> str:
    """
    Formatea transacciones Form 4 como tabla texto para inyectar en el prompt LLM.

    Solo incluye compras/ventas reales (P/S) — ejercicios y retenciones ya fueron
    filtrados en fetch_form4_transactions. Formato: insider | posición | acción |
    shares | precio | fecha. Legible para el LLM sin procesar JSON crudo.

    Tres categorías separadas:
    1. Discretionary officer/director trades (insider sentiment — informative)
    2. 10b5-1 pre-arranged plan trades (NOT informative about current sentiment) — EC-50
    3. Institutional >10% holder trades (portfolio flow — NOT insider sentiment) — EC-43
    """
    if not transactions:
        return "No open-market insider purchases or sales found in the last 12 months."

    # Separar por categoría
    non_institutional = [t for t in transactions if not t.get("is_institutional", False)]
    institutional = [t for t in transactions if t.get("is_institutional", False)]

    # Dentro de non-institutional, separar 10b5-1 de discretionary
    discretionary = [t for t in non_institutional if not t.get("is_10b5_1_plan", False)]
    planned_10b5 = [t for t in non_institutional if t.get("is_10b5_1_plan", False)]

    def _fmt(txns: list[dict]) -> list[str]:
        lines = []
        for t in txns:
            action = t["transaction_type"].upper()
            price_str = f"@ ${t['price_per_share']:.2f}" if t["price_per_share"] else "(price N/A)"
            lines.append(
                f"  {t['filer_name']} ({t['position']}) | {action} | "
                f"{t['shares']:,.0f} shares {price_str} | {t['transaction_date']}"
            )
        return lines

    parts: list[str] = []

    if discretionary:
        parts.append("OFFICER/DIRECTOR discretionary transactions (insider sentiment — skin in the game):")
        parts.extend(_fmt(discretionary))
    else:
        parts.append("No discretionary officer/director open-market transactions in the last 12 months.")

    if planned_10b5:
        parts.append("")
        parts.append("10b5-1 PRE-ARRANGED PLAN transactions (NOT informative about current sentiment):")
        parts.extend(_fmt(planned_10b5))
        parts.append(
            "  [NOTE: 10b5-1 plan sales are pre-programmed months in advance. The decision to sell "
            "was made when the plan was adopted, NOT at the execution date. Do NOT use these as "
            "bearish signals. They reflect estate/tax/diversification planning, not conviction.]"
        )

    if institutional:
        parts.append("")
        parts.append(
            "INSTITUTIONAL >10% HOLDER transactions (portfolio flow — NOT insider sentiment):"
        )
        parts.extend(_fmt(institutional))
        parts.append(
            "  [NOTE: institutional holders trade for portfolio reasons, not insider conviction. "
            "Do NOT weight these as insider sentiment signals.]"
        )

    return "\n".join(parts)


def _extract_8k_item_text(obj: object, item_numbers: list[str], max_chars: int = 800) -> str:
    """
    Extrae texto real de items específicos de un 8-K filing object (EC-44).

    edgartools 5.x soporta acceso por item: eightk["2.02"] → texto del item.
    Si no funciona, intenta body/summary como fallback.

    Args:
        obj: Objeto EightK de edgartools.
        item_numbers: Lista de item numbers a extraer (e.g. ["2.02", "5.02"]).
        max_chars: Máximo de caracteres totales a devolver.

    Returns:
        Texto concatenado de los items, o "" si no se pudo extraer nada.
    """
    parts: list[str] = []
    chars_remaining = max_chars

    for item_num in item_numbers:
        if chars_remaining <= 0:
            break
        # Normalizar: strip "Item " prefix si viene con él
        key = item_num.replace("Item ", "").strip()
        try:
            # edgartools: eightk["2.02"] → str con texto del item
            text = obj[key]  # type: ignore[index]
            if text and isinstance(text, str) and len(text.strip()) > 20:
                chunk = text.strip()[:chars_remaining]
                parts.append(f"[Item {key}] {chunk}")
                chars_remaining -= len(chunk)
                continue
        except (KeyError, TypeError, IndexError, AttributeError):
            pass
        # Fallback: intentar acceso como atributo
        try:
            text = str(getattr(obj, f"item_{key.replace('.', '_')}", "") or "")
            if text and len(text.strip()) > 20:
                chunk = text.strip()[:chars_remaining]
                parts.append(f"[Item {key}] {chunk}")
                chars_remaining -= len(chunk)
        except Exception:
            pass

    return "\n".join(parts)


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
        restatement_months: int = 24,
    ) -> list[dict]:
        """
        Obtiene eventos 8-K materiales del ticker en la ventana especificada.

        Item 4.02 (Non-Reliance / Restatement) usa una ventana separada y más larga
        (restatement_months, default 24) porque una restatement de hace 18 meses sigue
        siendo material para la tesis de inversión. Los otros items (2.02, 5.02) usan
        la ventana estándar `months`. La query a EDGAR usa el máximo de ambos para un
        solo round-trip; el filtrado por fecha se aplica por item individualmente (EC-66).

        Args:
            ticker: Ticker de la empresa (e.g. "AAPL").
            items: Items 8-K a filtrar. Default: ["2.02", "4.02", "5.02"].
            months: Ventana estándar en meses. Default: 12 (año fiscal completo).
            restatement_months: Ventana para Item 4.02. Default: 24 (2 años).

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

        # Usar la ventana más larga para la query si 4.02 está en el filter —
        # luego aplicamos cutoff por item en el loop para no surfacear eventos
        # 2.02/5.02 de hace 2 años que ya no son relevantes.
        items_normalized = {i.replace("Item ", "").strip().lower() for i in items}
        has_restatement_item = "4.02" in items_normalized
        query_months = max(months, restatement_months) if has_restatement_item else months
        start_date = (date.today() - timedelta(days=query_months * 30)).isoformat()
        standard_cutoff = (date.today() - timedelta(days=months * 30)).isoformat()

        try:
            filings = list(company.get_filings(form="8-K", date=f"{start_date}:"))
        except Exception as e:
            log.warning("edgar_events_filings_fetch_failed", ticker=ticker, error=str(e))
            return []

        events: list[dict] = []
        for f in filings:
            try:
                filing_date_str = str(f.filing_date)
                obj = f.obj()
                filed_items_raw = list(getattr(obj, "items", []) or [])
                # Normalizar items del filing: strip "Item " prefix para comparar (EC-30)
                filed_normalized = {i.replace("Item ", "").strip().lower() for i in filed_items_raw}

                # Aplicar cutoff por item: 4.02 usa restatement_months, resto usa months.
                # Esto evita surfacear 2.02/5.02 de hace 23 meses como "noticias recientes".
                matching = []
                for raw_item in filed_items_raw:
                    item_key = raw_item.replace("Item ", "").strip().lower()
                    if item_key not in items_normalized:
                        continue
                    if item_key == "4.02":
                        # Siempre incluir si está dentro de restatement_months (ya cubierto por query)
                        matching.append(raw_item)
                    elif filing_date_str >= standard_cutoff:
                        matching.append(raw_item)

                if not matching:
                    continue

                # --- Extracción mejorada de texto por item (EC-44) ---
                # Intentar extraer texto real del item vía eightk["X.YY"] (edgartools 5.x).
                # Esto da el body completo del item, mucho más rico que obj.summary.
                # Fallback a obj.summary si la extracción por item falla.
                summary = _extract_8k_item_text(obj, matching, max_chars=800)
                if not summary:
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

        log.info(
            "edgar_events_fetched",
            ticker=ticker,
            count=len(events),
            months=months,
            restatement_months=restatement_months if has_restatement_item else None,
        )
        return events

    @staticmethod
    def fetch_earnings_press_release(
        ticker: str,
        max_age_days: int = 120,
        max_chars: int = 8000,
    ) -> dict | None:
        """
        Obtiene el press release de earnings más reciente via 8-K Item 2.02.

        Los 8-K Item 2.02 ("Results of Operations and Financial Condition")
        contienen el press release completo que las empresas emiten con cada
        earnings call. Incluye revenue, EPS, guidance, y métricas operativas
        clave — datos trimestrales que el 10-K anual no tiene.

        Solo busca dentro de max_age_days para evitar surfacear datos obsoletos.
        Devuelve None si no hay 8-K 2.02 reciente.

        max_chars=8000: los press releases típicos ocupan 5-7KB (revenue, EPS,
        guidance, métricas operativas). Con 3000 chars el documento se cortaba
        a mitad perdiendo guidance y métricas clave.

        Args:
            ticker: Ticker de la empresa.
            max_age_days: Máximo número de días para buscar hacia atrás. Default 120
                (cubre ~1 quarter con margen).
            max_chars: Máximo de caracteres a extraer del press release.

        Returns:
            dict con {filing_date, text, filing_url} o None.
        """
        from datetime import date, timedelta
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        start_date = (date.today() - timedelta(days=max_age_days)).isoformat()

        try:
            filings = list(company.get_filings(form="8-K", date=f"{start_date}:"))
        except Exception as e:
            log.warning("earnings_pr_filings_fetch_failed", ticker=ticker, error=str(e))
            return None

        for f in filings:
            try:
                obj = f.obj()
                filed_items_raw = list(getattr(obj, "items", []) or [])
                # Buscar Item 2.02 con detección robusta: el formato varía por filer.
                # Variantes conocidas: "2.02", "2.02 Results of Operations...",
                # "Item 2.02". El `== "2.02"` fallaba en variantes con descripción.
                has_202 = any(
                    i.replace("Item ", "").strip().startswith("2.02")
                    for i in filed_items_raw
                )
                if not has_202:
                    continue

                # Extraer texto del press release.
                # Priority 1: Exhibit 99.1 (donde está el press release real con
                # revenue, EPS, guidance). ~90% de 8-K Item 2.02 usan este patrón.
                # Priority 2: Item 2.02 inline text (raro, pero algunos filers lo usan).
                # Priority 3: body completo del filing.
                text = ""
                try:
                    attachments = getattr(f, "attachments", None)
                    # edgartools: filing.attachments.exhibits → list of EX-* items
                    exhibits = (
                        getattr(attachments, "exhibits", None)
                        or attachments
                        or []
                    )
                    for exhibit in exhibits:
                        doc_type = getattr(exhibit, "document_type", "") or ""
                        if doc_type.upper().startswith("EX-99"):
                            exhibit_text = exhibit.text() if callable(getattr(exhibit, "text", None)) else ""
                            if exhibit_text and len(exhibit_text.strip()) > 200:
                                text = exhibit_text.strip()[:max_chars]
                                log.debug(
                                    "earnings_pr_exhibit_extracted",
                                    ticker=ticker,
                                    exhibit_type=doc_type,
                                    text_length=len(text),
                                )
                                break
                except Exception as ex:
                    log.debug("earnings_pr_exhibit_extract_failed", ticker=ticker, error=str(ex))

                if not text:
                    text = _extract_8k_item_text(obj, ["2.02"], max_chars=max_chars)
                if not text:
                    text = str(getattr(obj, "body", "") or "")[:max_chars]
                if not text or len(text.strip()) < 100:
                    continue

                log.info(
                    "earnings_press_release_found",
                    ticker=ticker,
                    filing_date=str(f.filing_date),
                    text_length=len(text),
                )
                return {
                    "filing_date": str(f.filing_date),
                    "text": text,
                    "filing_url": getattr(f, "filing_url", ""),
                }
            except Exception as e:
                log.debug("earnings_pr_parse_failed", ticker=ticker, error=str(e))
                continue

        log.info("no_recent_earnings_press_release", ticker=ticker, max_age_days=max_age_days)
        return None
