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
# DataUnavailableError vive en la capa pura (xbrl_extract) y se re-exporta aquí para
# mantener `from due_diligence.tools.edgar import DataUnavailableError` en todo el código.
# effective_tax_rate es la definición CANÓNICA (xbrl_extract); se importa, no se redefine
# (antes había una variable local homónima en parse_financials — revisión adversarial).
from due_diligence.tools.xbrl_extract import (
    REVENUE_CONCEPTS,
    DataUnavailableError,
    EdgarFactsLookup,
    build_raw,
    effective_tax_rate,
    latest_quarter_column,
    quarter_value,
    resolve_filing_shares,
    share_facts_from_xbrl_df,
)
from due_diligence.tools.accounting_scores import compute_accounting_scores, rows_from_raw
from due_diligence.utils.periods import align_by_period, fiscal_year

log = structlog.get_logger()


_IC_MIN_SHARE_OF_ASSETS = 0.05   # por debajo, el ROIC no es interpretable (n/m)


def invested_capital(*, equity: float, total_debt: float, cash: float, total_assets: float,
                     current_liabilities: float | None = None, short_term_debt: float = 0.0,
                     sti: float = 0.0, lti: float = 0.0, operating_rou_asset: float = 0.0,
                     force_operating: bool = False) -> tuple[float, str] | None:
    """IC (ADR-023, EC-18, revisión adversarial A4).

    Primaria (financiación): Equity + Debt − Cash − STI − LTI.
    Operativa (Koller): Assets − Cash − STI − LTI − ROU operativo − NIBCL. El ROU operativo se excluye
    porque el EBIT ya es neto del coste del arrendamiento (coherente con no restar el pasivo en el bridge).
    Regla: equity ≤ 0 o force_operating → operativa SIEMPRE (un solo método por empresa en toda la
    serie: mezclar métodos entre años crea saltos espurios en ROIC y en ΔIC).
    IC < 5 % de activos → None (ROIC n/m).

    Returns: (ic, método) o None si el IC no es interpretable.
    """
    non_op = cash + sti + lti
    if current_liabilities is not None:
        # NIBCL = pasivo corriente que no devenga intereses (proveedores, devengos...): lo
        # financian los proveedores, no los inversores → se resta del capital invertido.
        nibcl = max(current_liabilities - short_term_debt, 0.0)
        operating = total_assets - non_op - operating_rou_asset - nibcl
        op_method = "operating: assets-cash-STI-LTI-ROU-NIBCL"
    else:
        operating = total_assets - non_op - operating_rou_asset
        op_method = "operating: assets-cash-STI-LTI-ROU (no LiabilitiesCurrent)"
    primary = equity + total_debt - non_op
    chosen, method = (operating, op_method) if (equity <= 0 or force_operating) else (primary, "equity+debt-cash")
    if chosen <= 0 or (total_assets > 0 and chosen < _IC_MIN_SHARE_OF_ASSETS * total_assets):
        return None
    return chosen, method

class EdgarAdapter:
    @staticmethod
    def fetch_raw(ticker: str) -> dict[str, Any]:
        """
        Live EDGAR fetch via edgartools 5.x API. Use parse_financials(fixture) in tests.

        Solo hace red: descarga facts + los tres estados (MultiPeriodStatement con
        columnas 'FY 2025', ...) y delega TODA la selección de conceptos/periodos en
        xbrl_extract.build_raw (puro, testeado con DataFrames sintéticos). El dict
        devuelto mantiene el contrato de parse_financials; cada valor lleva "source".
        """
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

        # EC-27 aplicado también a números (EC-87): un 10-K/A suele traer solo Part III/IV
        # y su period_of_report puede no ser el del último 10-K completo.
        latest_10k = _get_latest_full_10k(company)
        if latest_10k is None:
            raise DataUnavailableError(f"{ticker}: no 10-K filings found")
        period_end = date.fromisoformat(str(latest_10k.period_of_report))

        # periods=10 (EC-79): el default de edgartools es 4 (entity_facts.py:1672) → el
        # "CAGR 5y" era en realidad de 3 años. Índice = concepto, columnas 'FY 2025'...
        inc_df = facts.income_statement(periods=10).to_dataframe()
        bs_df = facts.balance_sheet(periods=10).to_dataframe()
        cf_df = facts.cashflow_statement(periods=10).to_dataframe()

        shares = _filing_shares(ticker, latest_10k, period_end, facts)

        # Toda la lógica de selección vive en xbrl_extract (puro, testeable sin red).
        raw = build_raw(ticker=ticker, cik=str(company.cik).zfill(10), period_end=period_end,
                        inc_df=inc_df, bs_df=bs_df, cf_df=cf_df,
                        facts=EdgarFactsLookup(facts),
                        shares_outstanding=shares["shares_outstanding"],
                        as_converted_diluted=shares["as_converted_diluted"],
                        share_flags=[shares["flag"]] if shares["flag"] else None)
        log.info("edgar_fetch_complete", ticker=ticker, fy=raw["source_filing"])
        return raw

    @staticmethod
    def parse_financials(raw: dict[str, Any]) -> FinancialsReport:
        """Pure function — transforms raw dict into validated FinancialsReport."""
        as_of = date.fromisoformat(raw["as_of_date"])

        # Procedencia (T5): los raws nuevos traen "source" por valor (concepto XBRL,
        # "facts:<c>" o "derived:..."); los fixtures viejos no → se cae al default "XBRL".
        def _mv_list(items: list[dict], source: str = "XBRL") -> list[MetricValue]:
            return [
                MetricValue(value=i["value"], unit="USD", period=i["period"],
                            source=i.get("source", source))
                for i in items
            ]

        def _mv(item: dict, source: str = "XBRL") -> MetricValue:
            return MetricValue(
                value=item["value"], unit="USD", period=item["period"],
                source=item.get("source", source),
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
                MetricValue(value=abs(i["value"]), unit="USD", period=i["period"],
                            source=i.get("source", source))
                for i in items
            ]

        ocf_list = _mv_list(annual["operating_cash_flow"])
        # Capex en MAGNITUD (_mv_list_abs): edgartools 5.5x lo devuelve con signo y el FCFF
        # del Bloque C exige capex ≥ 0. Fixtures viejos (positivos) no cambian.
        capex_list = _mv_list_abs(annual["capex"])
        buybacks_list = _mv_list_abs(annual.get("buybacks", []))
        dividends_list = _mv_list_abs(annual.get("dividends", []))

        if ocf_list and capex_list:
            # Alineado por periodo (EC-82): zip posicional cruzaba años cuando una
            # serie tenía huecos. Periodos sin capex se omiten (no se inventa FCF).
            fcf_list = [
                MetricValue(value=o.value - abs(c.value), unit="USD", period=o.period,
                            source="calculated")
                for o, c in align_by_period(ocf_list, capex_list)
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

        def _mv_opt(item: dict | None, unit: str = "USD") -> MetricValue | None:
            """MetricValue opcional — nunca construir con value=None (EC-34).

            Un item con source "absent" es un 0.0 de relleno, no un dato: se omite (None).
            """
            if not item or item.get("value") is None or item.get("source") == "absent":
                return None
            return MetricValue(value=item["value"], unit=unit, period=item["period"],
                               source=item.get("source", "XBRL"))

        def _mv_list_unit(items: list[dict], unit: str) -> list[MetricValue]:
            """Como _mv_list pero con unidad explícita (p.ej. "shares" para medias ponderadas)."""
            return [MetricValue(value=i["value"], unit=unit, period=i["period"],
                                source=i.get("source", "XBRL")) for i in items]

        # Deuda total = LT + ST + leasing financiero (EC-80). Fixtures legacy sin
        # "finance_leases" cuentan 0. Los leasings OPERATIVOS no entran (ASC 842, memo en T8).
        lease = bs.get("finance_leases") or {"value": 0.0, "source": "absent"}  # legacy: sin clave → 0
        total_debt_value = (bs["long_term_debt"]["value"] + bs["short_term_debt"]["value"]
                            + lease["value"])
        # EC-81 / revisión adversarial: cash_and_equivalents = SOLO caja. Las STI van en su propio
        # campo y el bridge (T12) las resta UNA vez. Antes aquí se sumaban (cash + STI) y el
        # consumidor no podía saberlo. Fixtures legacy: STI=0 → mismo valor que antes.
        cash_value = bs["cash_and_equivalents"]["value"]
        sti_item = bs.get("short_term_investments")
        sti_value = sti_item["value"] if sti_item and sti_item.get("value") is not None else 0.0
        period = bs["total_assets"]["period"]

        # --- ROIC por año (T9, EC-85) ---
        # Antes: NOPAT_t / IC_último con la tasa del último año para TODOS los años → la serie
        # histórica mezclaba el capital de hoy con beneficios de hace años. Ahora:
        # ROIC_t = NOPAT_t / media(IC_t, IC_{t-1}) con la tasa efectiva DEL AÑO t
        # (effective_tax_rate canónica de xbrl_extract: clamp [0, 0.5], 0.25 si no calculable).
        tax_by = {m.period: m.value for m in _mv_list(annual.get("income_tax", []))}
        pretax_by = {m.period: m.value for m in _mv_list(annual.get("pretax_income", []))}
        op_inc_list = _mv_list(annual["operating_income"])
        nopat_by = {oi.period: oi.value * (1 - effective_tax_rate(tax_by.get(oi.period), pretax_by.get(oi.period)))
                    for oi in op_inc_list}

        history = raw.get("balance_history") or []
        # Un solo método de IC por empresa (revisión adversarial A4): si CUALQUIER año tiene
        # equity ≤ 0, toda la serie (y el snapshot) usa el IC operativo. Mezclar métodos
        # entre años crea saltos espurios en ROIC y en ΔIC.
        # Equity AUSENTE (None) no es equity ≤ 0: un año sin dato no puede cambiar el método de
        # toda la serie (revisión final H1, #6). Ese año simplemente no tiene IC primario.
        force_operating = any(h.get("stockholders_equity") is not None and h["stockholders_equity"] <= 0
                              for h in history)

        se_raw = bs.get("stockholders_equity")
        equity_value = se_raw["value"] if se_raw else 0.0
        total_assets_value = bs["total_assets"]["value"] if bs.get("total_assets") else 0.0

        def _bs_value(key: str) -> float:
            """Valor de un item opcional del balance (None/ausente → 0.0; solo para IC)."""
            item = bs.get(key)
            return item["value"] if item and item.get("value") is not None else 0.0

        # IC del último balance (snapshot) con la MISMA función que la serie (ADR-023, EC-18).
        cl_item = bs.get("current_liabilities")
        ic_res = invested_capital(
            equity=equity_value, total_debt=total_debt_value, cash=cash_value, sti=sti_value,
            lti=_bs_value("long_term_investments"), operating_rou_asset=_bs_value("operating_rou_asset"),
            total_assets=total_assets_value,
            current_liabilities=cl_item["value"] if cl_item and cl_item.get("value") is not None else None,
            short_term_debt=bs["short_term_debt"]["value"], force_operating=force_operating,
        )
        ic_value, ic_method = ic_res if ic_res else (0.0, None)

        stockholders_equity_mv = (
            MetricValue(value=equity_value, unit="USD", period=period, source="XBRL")
            if se_raw else None
        )
        invested_capital_mv = (
            MetricValue(value=ic_value, unit="USD", period=period, source=ic_method or "calculated")
            if ic_value > 0 else None
        )

        # IC por año desde balance_history (raws nuevos). Años sin activos/caja se omiten.
        ic_by: dict[str, float] = {}
        for h in history:
            if h.get("total_assets") is None or h.get("cash") is None:
                continue
            if h.get("stockholders_equity") is None and not force_operating:
                continue   # sin equity no hay IC primario; contarlo como 0 forzaría el operativo
            res = invested_capital(equity=h.get("stockholders_equity") or 0.0, total_debt=h["total_debt"],
                                   cash=h["cash"], sti=h.get("short_term_investments") or 0.0,
                                   lti=h.get("long_term_investments") or 0.0,
                                   operating_rou_asset=h.get("operating_rou_asset") or 0.0,
                                   total_assets=h["total_assets"], current_liabilities=h.get("current_liabilities"),
                                   short_term_debt=h.get("short_term_debt") or 0.0,
                                   force_operating=force_operating)   # un solo método para toda la serie
            if res:
                ic_by[h["period"]] = res[0]

        roic_series: list[MetricValue] = []
        for oi in op_inc_list:
            if ic_by:
                ic_t = ic_by.get(oi.period)
                if ic_t is None:
                    continue
                ic_prev = ic_by.get(f"FY{fiscal_year(oi.period) - 1}")
                denom, src = ((ic_t + ic_prev) / 2, "calculated-avg-ic") if ic_prev else (ic_t, "calculated-end-ic")
            elif ic_value > 0:  # legacy fixtures without balance_history (hasta el re-fetch de T14)
                denom, src = ic_value, "calculated-latest-ic"
            else:
                continue
            roic_series.append(MetricValue(value=round(nopat_by[oi.period] / denom, 4), unit="ratio",
                                           period=oi.period, source=src))

        # ROIC incremental (Mauboussin): ΔNOPAT/ΔIC entre el último año y el más antiguo
        # dentro de 3 años fiscales. None si ΔIC ≤ 5 % del IC inicial (ruido, no reinversión).
        incremental = None
        if ic_by and op_inc_list:
            end = op_inc_list[0].period
            y_end = fiscal_year(end)
            starts = sorted(p for p in ic_by if p in nopat_by and 0 < y_end - fiscal_year(p) <= 3)
            if starts and end in ic_by:
                start = starts[0]  # oldest within 3 years
                d_ic = ic_by[end] - ic_by[start]
                if d_ic > 0.05 * abs(ic_by[start]):
                    incremental = MetricValue(value=round((nopat_by[end] - nopat_by[start]) / d_ic, 4),
                                              unit="ratio", period=f"{start}→{end}", source="calculated")

        def _hist_series(key_fn, unit: str = "USD") -> list[MetricValue]:
            """Serie anual de balance desde balance_history (index 0 = latest, etiquetas FY)."""
            return [MetricValue(value=v, unit=unit, period=h["period"], source="XBRL")
                    for h in history if (v := key_fn(h)) is not None]

        total_debt_series = _hist_series(lambda h: h.get("total_debt"))
        # cash_series = caja + STI (liquidez financiera total del año; la caja sola es cash_and_equivalents)
        cash_series = _hist_series(lambda h: None if h.get("cash") is None
                                   else h["cash"] + (h.get("short_term_investments") or 0.0))
        equity_series = _hist_series(lambda h: h.get("stockholders_equity"))

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
                value=cash_value, unit="USD", period=period,
                source=bs["cash_and_equivalents"].get("source", "XBRL"),
            ),
            short_term_investments=_mv_opt(sti_item),
            long_term_investments=_mv_opt(bs.get("long_term_investments")),
            data_quality_flags=list(raw.get("data_quality_flags", [])),
            # --- FCFF inputs (T8). Fixtures legacy sin estas claves → [] / None. ---
            # Magnitud (_mv_list_abs): el signo de intereses/SBC/D&A/principal varía por filer y
            # por versión de edgartools (5.5x devuelve flujos de caja con signo); el FCFF los
            # resta como costes ≥ 0. Intereses COBRADOS conservan su signo.
            interest_expense=_mv_list_abs(annual.get("interest_expense", [])),
            interest_income=_mv_list(annual.get("interest_income", [])),
            sbc=_mv_list_abs(annual.get("sbc", [])),
            depreciation_amortization=_mv_list_abs(annual.get("depreciation_amortization", [])),
            diluted_shares=_mv_list_unit(annual.get("diluted_shares", []), "shares"),
            basic_shares_weighted=_mv_opt((annual.get("basic_shares_weighted") or [None])[0], "shares"),
            income_tax=_mv_list(annual.get("income_tax", [])),
            pretax_income=_mv_list(annual.get("pretax_income", [])),
            finance_lease_principal_payments=_mv_list_abs(annual.get("finance_lease_principal_payments", [])),
            operating_lease_liabilities=_mv_opt(bs.get("operating_lease_liabilities")),
            operating_rou_asset=_mv_opt(bs.get("operating_rou_asset")),
            equity_method_investments=_mv_opt(bs.get("equity_method_investments")),
            minority_interest=_mv_opt(bs.get("minority_interest")),
            preferred_stock=_mv_opt(bs.get("preferred_stock")),
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
            # A2 (ADR-043): Piotroski, Altman Z'', Beneish y Sloan sobre los mismos estados XBRL.
            accounting_scores=compute_accounting_scores(rows_from_raw(raw)),
            invested_capital=invested_capital_mv,
            stockholders_equity=stockholders_equity_mv,
            incremental_roic=incremental,
            total_debt_series=total_debt_series,
            cash_series=cash_series,
            equity_series=equity_series,
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

        # EC-84: el DataFrame XBRL del 10-Q tiene columnas '2026-06-27 (Q3)' / '(YTD)' y
        # 'concept' como COLUMNA con prefijo (verificado en vivo). El código viejo filtraba
        # c.startswith("Q") sobre el índice → siempre vacío → el snapshot nunca se producía.
        latest_q = latest_quarter_column(list(inc_df.columns))
        if latest_q is None:
            log.info("quarterly_snapshot_no_quarters", ticker=ticker)
            return None

        snapshot = {
            "period": latest_q,
            # Misma cadena de revenue que el anual (EC-67), una sola definición.
            "revenue": quarter_value(inc_df, REVENUE_CONCEPTS, latest_q),
            "operating_income": quarter_value(inc_df, ("OperatingIncomeLoss",), latest_q),
            "gross_profit": quarter_value(inc_df, ("GrossProfit",), latest_q),
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
    def fetch_def14a_html(ticker: str) -> tuple[str, str | None]:
        """
        (HTML completo del DEF 14A más reciente, fecha de presentación) — ("", None) si no hay (EC-93).

        La fecha viaja con el HTML porque el último DEF 14A puede ser de hace años (EPD: 2022) y
        la participación de directivos se presentaría como actual sin aviso.

        Lo consume tools/proxy_ownership.py para leer la tabla de participaciones por su
        ESTRUCTURA (filas y celdas). El texto plano (fetch_def14a_text) no sirve para eso:
        la ventana por títulos caía después de la tabla (CALM, PRDO) y la conversión a texto
        trocea las tablas estrechas (AZO: "Goodsp…", "Percent…").

        Por qué la cadena de intentos: en edgartools 5.59.1 `Filing.html()` devuelve None
        para los proxies cuyo documento principal es inline XBRL (empieza por `<?xml` y
        DEF 14A no está en XML_FILING_FORMS, _filings.py:1603-1640). `sgml().html()` lee el
        documento principal del paquete SGML (sgml_common.py:314) y sí lo devuelve.
        """
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        filings = Company(ticker).get_filings(form="DEF 14A")
        if not filings:
            log.warning("def14a_not_found", ticker=ticker)
            return "", None
        filing = filings.latest()  # patrón latest() aceptado: DEF 14A/A es excepcional (MCE-26, T5)
        filing_date = str(filing.filing_date) if getattr(filing, "filing_date", None) else None
        html = filing.html()
        if not html:
            try:
                html = filing.sgml().html()
            except Exception as e:  # red/parseo SGML: se registra y se devuelve "" (el llamador avisa)
                log.warning("def14a_html_sgml_failed", ticker=ticker, error=str(e))
                html = None
        if not html:
            log.warning("def14a_html_unavailable", ticker=ticker)
            return "", filing_date
        return html, filing_date

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

        filing = filings.latest()  # patrón latest() aceptado: DEF 14A/A es excepcional (MCE-26, T5)
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


def _filing_shares(ticker: str, latest_10k, period_end: date, facts) -> dict:
    """Recuento de acciones fresco, multiclase incluidas (EC-101). Red; la lógica es pura.

    Orden:
      1. Portada de companyfacts SI es posterior al cierre del último 10-K: es el dato más actual
         (puede venir de un 10-Q posterior, ya con las recompras) en empresas de una sola clase.
      2. Si no, el XBRL del propio 10-K (resolve_filing_shares). Es el caso multiclase:
         companyfacts descarta los hechos con dimensión de clase y su último valor sin dimensión
         puede tener 15 años (V: enero de 2010), lo que inflaba el valor por acción 4-7×.
      3. Si tampoco, None + aviso: la valoración usa las diluidas medias (resolve_share_count).
    """
    fact = facts.shares_outstanding_fact
    if fact is not None and fact.period_end is not None and fact.period_end >= period_end:
        return {"shares_outstanding": float(fact.numeric_value), "as_converted_diluted": None,
                "flag": None}
    try:
        cover, diluted = share_facts_from_xbrl_df(latest_10k.xbrl().facts.to_dataframe(), period_end)
        return resolve_filing_shares(cover, diluted)
    except Exception as e:  # XBRL externo: se degrada a None con aviso visible, nunca a un dato viejo
        log.warning("filing_shares_unavailable", ticker=ticker, error=f"{type(e).__name__}: {e}")
    return {"shares_outstanding": None, "as_converted_diluted": None,
            "flag": "share count: no fresh cover-page count (companyfacts stale, 10-K XBRL unavailable); "
                    "per-share values use diluted weighted average shares"}


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
    return filings.latest()  # patrón latest() aceptado: degraded mode, ya logueado arriba


# Límite por apartado del 10-K (Items 1, 1A, 7) enviado al Filings agent (EC-94, EC-106).
# Medido el 2026-10-17: AAPL 16K / 68K / 18K, CALM 51K / 69K / 50K caracteres. Con 25 000 el 1A de
# AAPL llegaba recortado a un tercio sin aviso. 60 000 cubre la sección entera en casi todos los
# casos (≈ 45K tokens los tres); si aun así se recorta, el texto lleva una marca visible.
TENK_ITEM_MAX_CHARS = 60_000
# Por debajo de esto el parser primario no ha encontrado la sección real (índice o referencia suelta).
_MIN_PRIMARY_SECTION_CHARS = 500


def _section_text(filing, ticker: str, item: str, primary: str, max_chars: int) -> str:
    """Texto de la sección del 10-K: parser de edgartools; si falla, encabezados del texto completo.

    EC-106: edgartools devuelve las secciones vacías en algunos 10-K (KO, JNJ en 2025) aunque el
    texto completo existe; antes el Filings agent recibía "" sin que se viera en el informe. El
    resultado se recorta con marca visible (tenk_sections.truncate_visibly) y se registra.
    """
    from due_diligence.tools.tenk_sections import extract_section, truncate_visibly

    text, source = primary or "", "edgartools"
    if len(text) < _MIN_PRIMARY_SECTION_CHARS:
        try:
            full_text = filing.document.text() if hasattr(filing, "document") else ""
        except Exception as e:  # el texto completo es el fallback: si falla, se devuelve lo que haya
            log.warning("10k_full_text_failed", ticker=ticker, item=item, error=str(e))
            full_text = ""
        fallback = extract_section(full_text, item)
        if fallback:
            text, source = fallback, "full-text headers"
    if not text:
        log.warning("10k_section_not_found", ticker=ticker, item=item)
        return ""
    out, truncated = truncate_visibly(text, max_chars)
    log.info("10k_section_fetched", ticker=ticker, item=item, source=source, chars=len(text),
             truncated=truncated)
    if truncated:
        log.warning("10k_section_truncated", ticker=ticker, item=item, kept=max_chars, total=len(text))
    return out


class EdgarFilingsAdapter:
    """
    Adapter para obtener las secciones cualitativas del 10-K anual.

    Cubre las fuentes para §1 (Business) y §7 (Risks & Red Flags):
    - Item 1: Business — descripción del negocio, segmentos, fuentes de revenue.
    - Item 1A: Risk Factors — riesgos materiales declarados por la empresa.
    - Item 7: MD&A — análisis de la dirección sobre resultados y tendencias.

    Los tres métodos comparten el mismo patrón: obtener el 10-K original más reciente
    (excluyendo 10-K/A — ver `_get_latest_full_10k`), extraer la sección semántica
    via edgartools 5.x (con fallback por encabezados, EC-106) y recortar con marca visible.
    Se mockean individualmente
    en tests — ver tests/test_agents_1b.py.
    """

    @staticmethod
    def fetch_10k_business(ticker: str, max_chars: int = TENK_ITEM_MAX_CHARS) -> str:
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
            primary = str(filing.obj().business or "")
        except Exception as e:  # el fallback por encabezados se intenta igualmente
            log.warning("10k_business_extraction_failed", ticker=ticker, error=str(e))
            primary = ""
        return _section_text(filing, ticker, "1", primary, max_chars)

    @staticmethod
    def fetch_10k_risk_factors(ticker: str, max_chars: int = TENK_ITEM_MAX_CHARS) -> str:
        """
        Obtiene el Item 1A del 10-K (Risk Factors).

        Contiene los riesgos materiales que la empresa reconoce formalmente:
        regulatorios, competitivos, tecnológicos, macroeconómicos. Es la
        fuente primaria para §7.2 (Regulatory exposure) y §7.3 (Disruption)
        del checklist.

        Primary: `tenk.risk_factors` de edgartools. Fallback (EC-27, EC-106): encabezados en el texto
        completo del filing (`tenk_sections.extract_section`), el mismo para los Items 1, 1A y 7.
        Antes el 1A tenía un regex propio que tomaba la ÚLTIMA aparición del encabezado; en JNJ esa
        era una referencia cruzada de 2.338 caracteres.
        """
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        filing = _get_latest_full_10k(company)  # excluye 10-K/A (EC-27)

        if not filing:
            log.warning("10k_not_found_for_risk_factors", ticker=ticker)
            return ""

        log.info("10k_risk_factors_fetch", ticker=ticker, period=str(filing.period_of_report))

        try:
            primary = str(filing.obj().risk_factors or "")
        except Exception as e:  # el fallback por encabezados se intenta igualmente
            log.warning("10k_risk_factors_primary_failed", ticker=ticker, error=str(e))
            primary = ""
        return _section_text(filing, ticker, "1A", primary, max_chars)

    @staticmethod
    def fetch_10k_mda(ticker: str, max_chars: int = TENK_ITEM_MAX_CHARS) -> str:
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
            primary = str(filing.obj().management_discussion or "")
        except Exception as e:  # el fallback por encabezados se intenta igualmente
            log.warning("10k_mda_extraction_failed", ticker=ticker, error=str(e))
            primary = ""
        return _section_text(filing, ticker, "7", primary, max_chars)


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

                # Aplicar cutoff por item: 4.02 usa restatement_months, resto usa months.
                # Esto evita surfacear 2.02/5.02 de hace 23 meses como "noticias recientes".
                matching = []
                for raw_item in filed_items_raw:
                    # Normalizar el item del filing: strip "Item " prefix para comparar (EC-30)
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
