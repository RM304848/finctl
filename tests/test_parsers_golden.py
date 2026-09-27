"""Every real statement must reconcile, for every profile.

This is the acceptance test for ingestion. It is deliberately not a
transaction-count assertion: counts drift as statements are added, whereas
`opening + sum(transactions) == closing` is a property that must hold forever.
A statement that balances is arithmetically complete -- nothing dropped,
nothing double-counted.

Statement PDFs are gitignored, so these skip on a fresh clone.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from finctl.ingest.importer import extract_pages, load_parser, reconcile

STATEMENTS = Path("data/statements")

ACCOUNT_PROFILES = {
    "dkb-giro": "dkb_giro",
    "c24": "c24_giro",
    "trade-republic": "trade_republic",
}


def _cases() -> list[tuple[str, Path]]:
    cases = []
    for account, profile in ACCOUNT_PROFILES.items():
        folder = STATEMENTS / account
        if folder.is_dir():
            cases.extend((profile, pdf) for pdf in sorted(folder.glob("*.pdf")))
    return cases


@functools.cache
def _geparst(profile: str, pdf: Path):
    """Jeden Auszug einmal lesen, nicht einmal je Pruefung.

    Ein Jahresauszug von Trade Republic braucht zehn Sekunden; beide
    Pruefungen unten lesen dasselbe. Damit sie im parallelen Lauf im selben
    Prozess landen, tragen sie eine gemeinsame `xdist_group` je Datei.
    """
    return load_parser(profile).parse(extract_pages(pdf), pdf)


def _faelle() -> list:
    return [pytest.param(profile, pdf, marks=pytest.mark.xdist_group(pdf.name))
            for profile, pdf in _cases()]


@pytest.mark.skipif(not _cases(), reason="no statements present")
@pytest.mark.parametrize("profile,pdf", _faelle(),
                         ids=lambda v: v.name[:22] if isinstance(v, Path) else v)
def test_statement_reconciles(profile: str, pdf: Path):
    result = _geparst(profile, pdf)
    rec = reconcile(result)

    assert rec.ok, (
        f"{pdf.name}: {len(result.transactions)} txns parsed, "
        f"off by {rec.delta_cents} cents "
        f"(expected {rec.expected_cents}, got {rec.actual_cents})"
    )
    assert result.transactions, "statement reconciled but contained no transactions"


@pytest.mark.skipif(not _cases(), reason="no statements present")
@pytest.mark.parametrize("profile,pdf", _faelle(),
                         ids=lambda v: v.name[:22] if isinstance(v, Path) else v)
def test_booking_dates_fall_inside_period(profile: str, pdf: Path):
    """A date outside the statement period means a false-positive row match.

    Reconciliation alone would not catch this: a row picked up from a
    neighbouring table can still balance if its amount happens to net out.
    """
    result = _geparst(profile, pdf)
    start, end = result.header.period_start, result.header.period_end
    stray = [t.booking_date for t in result.transactions if not (start <= t.booking_date <= end)]
    assert not stray, f"{pdf.name}: {len(stray)} bookings outside {start}..{end}: {stray[:5]}"
