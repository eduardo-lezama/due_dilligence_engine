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

        def _get_annual(df: Any, concept: str, n: int = 5) -> list[dict]:
            """Extrae hasta n años de un concepto. Devuelve [] si no existe."""
            if concept not in df.index:
                return []
            row = df.loc[concept]
            result = []
            for col in _fy_cols(df)[:n]:
                val = row.get(col)
                if pd.notna(val):
                    # 'FY 2025' → 'FY2025' para consistencia con el fixture format
                    period = col.replace("FY ", "FY")
                    result.append({"value": float(val), "period": period})
            return result

        def _get_point(df: Any, concept: str, period: str) -> dict:
            """Extrae el valor más reciente de un concepto puntual (balance sheet)."""
            if concept not in df.index:
                raise DataUnavailableError(f"{ticker}: XBRL concept '{concept}' not found")
            row = df.loc[concept]
            for col in _fy_cols(df):
                val = row.get(col)
                if pd.notna(val):
                    return {"value": float(val), "period": period}
            raise DataUnavailableError(f"{ticker}: '{concept}' has no annual data")

        # --- Income statement ---
        revenue = _get_annual(inc_df, "RevenueFromContractWithCustomerExcludingAssessedTax")
        if not revenue:
            revenue = _get_annual(inc_df, "Revenues")
        if not revenue:
            raise DataUnavailableError(f"{ticker}: revenue concept not found in income statement")

        # --- Balance sheet ---
        # LongTermDebt en edgartools 5.x ya incluye la porción corriente
        lt_debt = _get_point(bs_df, "LongTermDebt", fy_label)

        # ShortTermBorrowings: deuda a corto plazo distinta de la porción corriente del LTD
        # (muchas empresas no lo tienen — defaulta a 0 sin fallar)
        st_debt_value = 0.0
        if "ShortTermBorrowings" in bs_df.index:
            try:
                st_debt_value = _get_point(bs_df, "ShortTermBorrowings", fy_label)["value"]
            except DataUnavailableError:
                pass

        cash = _get_point(bs_df, "CashAndCashEquivalentsAtCarryingValue", fy_label)

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
            },
            "balance_sheet": {
                "total_assets": _get_point(bs_df, "Assets", fy_label),
                "long_term_debt": lt_debt,
                "short_term_debt": {"value": st_debt_value, "period": fy_label},
                "cash_and_equivalents": cash,
                "short_term_investments": {"value": sti_value, "period": fy_label},
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
            source_filing=raw["source_filing"],
        )


def get_financials(ticker: str) -> FinancialsReport:
    raw = EdgarAdapter.fetch_raw(ticker)
    return EdgarAdapter.parse_financials(raw)
