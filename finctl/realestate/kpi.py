"""Per-property economics.

Three numbers that are routinely conflated, kept separate here because they
answer different questions:

* **Cash flow** -- what actually leaves your account, Tilgung included. This is
  what strains liquidity, and it is the number that made 2026 feel punishing.
* **Net profit (tax basis)** -- rent minus operating costs, minus INTEREST but
  not Tilgung, minus AfA. Principal repayment is not an expense; it converts
  cash into equity. AfA is an expense that costs no cash.
* **Equity build** -- the principal repaid, which is the part of the cash drain
  you keep.

A property can drain cash every month and still be profitable, which is the
usual case for a leveraged German rental and exactly what a naive cash view
gets wrong.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from finctl.pfade import CONFIG_DIR


@dataclass(slots=True)
class PropertyKPI:
    property_id: str
    name: str
    months_observed: int

    rent_cents: int = 0
    operating_cents: int = 0          # negative
    interest_cents: int = 0           # negative
    principal_cents: int = 0          # negative in cash terms
    other_cents: int = 0

    capital_cents: int = 0            # instalments / Kaufnebenkosten: investment
    afa_base_cents: int = 0
    afa_annual_cents: int = 0
    equity_invested_cents: int = 0

    @property
    def cash_flow_cents(self) -> int:
        """OPERATING cash flow. Capital payments are excluded deliberately.

        Purchase instalments are investment, not running cost.
        Annualising them as though they recurred produced -115.789/year for a
        property that has no operations at all yet -- a number that is not
        merely wrong but actively misleading.
        """
        return (self.rent_cents + self.operating_cents + self.interest_cents
                + self.principal_cents + self.other_cents)

    @property
    def net_profit_cents(self) -> int:
        """Tax basis: excludes Tilgung, includes AfA, annualised."""
        months = self.months_observed or 1
        operating_year = (self.rent_cents + self.operating_cents
                          + self.interest_cents + self.other_cents) * 12 // months
        return operating_year - self.afa_annual_cents

    @property
    def cash_flow_annual_cents(self) -> int:
        months = self.months_observed or 1
        return self.cash_flow_cents * 12 // months

    @property
    def equity_build_annual_cents(self) -> int:
        months = self.months_observed or 1
        return -self.principal_cents * 12 // months

    @property
    def total_return_annual_cents(self) -> int:
        """Cash flow plus the equity you bought with it."""
        return self.cash_flow_annual_cents + self.equity_build_annual_cents

    @property
    def yield_pct(self) -> float | None:
        if not self.equity_invested_cents:
            return None
        return 100.0 * self.total_return_annual_cents / self.equity_invested_cents

    @property
    def payback_years(self) -> float | None:
        """Years until the annual return repays the equity invested.

        None when the return is negative -- an infinite payback is better
        stated as 'never at this rate' than as a misleadingly large number.
        """
        if not self.equity_invested_cents or self.total_return_annual_cents <= 0:
            return None
        return self.equity_invested_cents / self.total_return_annual_cents


def compute(conn: sqlite3.Connection, property_id: str) -> PropertyKPI:
    prop = conn.execute(
        "SELECT id, name, purchase_price_cents, incidental_costs_cents, "
        "land_share_pct, afa_rate_pct, afa_base_cents, afa_extra_annual_cents, "
        "equity_cents "
        "FROM properties WHERE id = ?",
        (property_id,),
    ).fetchone()
    if prop is None:
        raise ValueError(f"no property {property_id}")

    months = conn.execute(
        "SELECT COUNT(DISTINCT substr(t.booking_date,1,7)) FROM splits s "
        "JOIN transactions t ON t.id = s.transaction_id WHERE s.property_id = ?",
        (property_id,),
    ).fetchone()[0] or 1

    kpi = PropertyKPI(property_id=prop["id"], name=prop["name"], months_observed=months)

    for row in conn.execute(
        "SELECT s.mgmt_category_id AS cat, SUM(s.amount_cents) AS cents "
        "FROM splits s WHERE s.property_id = ? GROUP BY cat", (property_id,)
    ):
        cat, cents = row["cat"] or "", row["cents"]
        if cat in ("immobilie/mieteinnahme", "einkommen/mieteinnahmen"):
            kpi.rent_cents += cents
        elif cat == "immobilie/kaufnebenkosten":
            kpi.capital_cents += cents
        elif cat.startswith("immobilie/"):
            kpi.operating_cents += cents
        elif cat == "kredit/zinsen":
            kpi.interest_cents += cents
        elif cat in ("kredit/tilgung", "kredit/sondertilgung"):
            # The ledger books the whole annuity here; the interest share is
            # split out below from the loan schedule where one exists.
            kpi.principal_cents += cents
        elif cat.startswith("transfer/"):
            continue
        else:
            kpi.other_cents += cents

    # AfA: building only. Land is not depreciable, which is why the land share
    # has to be known rather than assumed.
    #
    # A base stated in the tax return wins over one reconstructed from price and
    # land share. The Finanzamt has already accepted that figure; the
    # reconstruction is a derivation from two numbers a bank statement never
    # shows, and it silently omits notary and Grunderwerbsteuer.
    rate = prop["afa_rate_pct"] or 0.0
    if prop["afa_base_cents"]:
        kpi.afa_base_cents = prop["afa_base_cents"]
    else:
        price = prop["purchase_price_cents"] or 0
        incidental = prop["incidental_costs_cents"] or 0
        land_pct = prop["land_share_pct"] or 0.0
        kpi.afa_base_cents = int(round((price + incidental) * (1 - land_pct / 100.0)))
    kpi.afa_annual_cents = int(round(kpi.afa_base_cents * rate / 100.0))
    # Kitchen, furniture and fittings depreciate beside the building on
    # their own life, so this is added rather than rolled into the base.
    kpi.afa_annual_cents += prop["afa_extra_annual_cents"] or 0
    kpi.equity_invested_cents = prop["equity_cents"] or 0
    return kpi


def split_annuity(conn: sqlite3.Connection, kpi: PropertyKPI) -> PropertyKPI:
    """Separate interest from principal using the loan schedule.

    Bank statements book one annuity; only the schedule knows the split, and
    the split is what decides whether a property is profitable.
    """
    from datetime import date

    import yaml

    from finctl.realestate.loan import amortise, opening_balance_cents, segments_from

    spec = yaml.safe_load(
        (CONFIG_DIR / "loans.yaml").read_text(encoding="utf-8")) or {}
    for loan in spec.get("loans", []):
        if loan.get("property_id") != kpi.property_id or not loan.get("segments"):
            continue
        schedule = amortise(
            str(loan["id"]),
            opening_balance_cents(loan),
            segments_from(loan),
        )
        window = [p for p in schedule.payments
                  if date(2026, 1, 1) <= p.month <= date(2026, 12, 31)][:kpi.months_observed]
        if not window:
            continue
        interest = sum(p.interest_cents for p in window)
        principal = sum(p.principal_cents for p in window)
        total = kpi.principal_cents          # currently the whole annuity, negative
        if total:
            kpi.interest_cents += -interest
            kpi.principal_cents = -principal
            # Anything the ledger booked beyond the modelled annuity stays put.
            kpi.other_cents += total + interest + principal
    return kpi
