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
        """Live EDGAR fetch. Use parse_financials(fixture) in tests instead."""
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        log.info("edgar_fetch_start", ticker=ticker)

        company = Company(ticker)
        facts = company.get_facts()

        def _extract_annual(concept: str, years: int = 5) -> list[dict]:
            try:
                df = getattr(facts.us_gaap, concept).to_dataframe()
                annual = df[df["form"].isin(["10-K", "10-K/A"])].copy()
                annual = annual.sort_values("end", ascending=False).head(years)
                return [
                    {
                        "year": row["end"].year,
                        "value": float(row["val"]),
                        "period": f"FY{row['end'].year}",
                    }
                    for _, row in annual.iterrows()
                ]
            except AttributeError:
                raise DataUnavailableError(f"{ticker}: XBRL concept '{concept}' not found")

        def _extract_point(concept: str, period: str) -> dict:
            try:
                df = getattr(facts.us_gaap, concept).to_dataframe()
                latest = df[df["form"].isin(["10-K", "10-K/A"])].sort_values("end").iloc[-1]
                return {"value": float(latest["val"]), "period": period}
            except (AttributeError, IndexError):
                raise DataUnavailableError(f"{ticker}: XBRL concept '{concept}' not found")

        filings = company.get_filings(form="10-K")
        latest_10k = filings.latest()
        fy_end = str(latest_10k.period_of_report)
        fy_label = f"FY{fy_end[:4]}"

        revenue = _extract_annual("RevenueFromContractWithCustomerExcludingAssessedTax")
        if not revenue:
            revenue = _extract_annual("Revenues")

        lt_debt = _extract_point("LongTermDebt", fy_label)
        st_debt_value = 0.0
        try:
            st_debt = _extract_point("ShortTermBorrowings", fy_label)
            st_debt_value = st_debt["value"]
        except DataUnavailableError:
            pass

        cash = _extract_point("CashAndCashEquivalentsAtCarryingValue", fy_label)
        sti_value = 0.0
        try:
            sti = _extract_point("AvailableForSaleSecuritiesDebtSecuritiesCurrent", fy_label)
            sti_value = sti["value"]
        except DataUnavailableError:
            pass

        raw = {
            "ticker": ticker.upper(),
            "cik": str(company.cik).zfill(10),
            "as_of_date": fy_end,
            "source_filing": f"10-K {fy_label}",
            "annual": {
                "revenue": revenue,
                "gross_profit": _extract_annual("GrossProfit"),
                "operating_income": _extract_annual("OperatingIncomeLoss"),
                "net_income": _extract_annual("NetIncomeLoss"),
                "operating_cash_flow": _extract_annual(
                    "NetCashProvidedByUsedInOperatingActivities"
                ),
                "capex": _extract_annual("PaymentsToAcquirePropertyPlantAndEquipment"),
            },
            "balance_sheet": {
                "total_assets": _extract_point("Assets", fy_label),
                "long_term_debt": lt_debt,
                "short_term_debt": {"value": st_debt_value, "period": fy_label},
                "cash_and_equivalents": cash,
                "short_term_investments": {"value": sti_value, "period": fy_label},
            },
            "shares": {
                "shares_outstanding": _extract_point(
                    "CommonStockSharesOutstanding", fy_label
                ),
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
