"""Every real statement must reconcile, for every profile.

This is the acceptance test for ingestion. It is deliberately not a
transaction-count assertion: counts drift as statements are added, whereas
`opening + sum(transactions) == closing` is a property that must hold forever.
A statement that balances is arithmetically complete -- nothing dropped,
nothing double-counted.

The cases are every file `finctl ingest run` would import: each parsed
account in the register, PDF and CSV, read by the parser the importer would
pick. A hand-kept list of accounts missed Scalable, and with it a whole
month that failed to import.

Statements are not in the repository, so these skip on a fresh clone.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from finctl import konten
from finctl.ingest.importer import (
    apply_corrections,
    detect,
    extract_pages,
    load_parser,
    reconcile,
)

STATEMENTS = Path("data/statements")

# Wie `ops.ingest_all`: PayPal exportiert `.CSV`.
_MUSTER = ("*.pdf", "*.PDF", "*.csv", "*.CSV")


def _cases() -> list[tuple[str, Path]]:
    cases = []
    for konto in konten.laden(Path("config")):
        if konto.get("ingest_mode") != "parsed" or konto.get("active") is False:
            continue
        folder = konten.auszugsordner(konto, STATEMENTS)
        if folder.is_dir():
            dateien = sorted({d for m in _MUSTER for d in folder.glob(m)})
            cases.extend((str(konto["id"]), datei) for datei in dateien)
    return cases


@functools.cache
def _geparst(account: str, datei: Path):
    """Jeden Auszug einmal lesen, nicht einmal je Pruefung.

    Ein Jahresauszug von Trade Republic braucht zehn Sekunden; beide
    Pruefungen unten lesen dasselbe. Damit sie im parallelen Lauf im selben
    Prozess landen, tragen sie eine gemeinsame `xdist_group` je Datei.

    Der Parser wird gewaehlt wie in `import_statement`: das Profil des
    Kontos, sonst das erkannte -- ein CSV-Export neben PDF-Auszuegen hat ein
    eigenes. Erklaerte Korrekturen gelten wie beim Import.
    """
    konto = next(k for k in konten.laden(Path("config")) if str(k["id"]) == account)
    seiten = extract_pages(datei)
    parser = load_parser(konto["parser_profile"])
    if not parser.matches("\n".join(seiten[:2]), datei):
        parser = detect(seiten, datei)
        assert parser is not None, f"{datei.name}: no parser profile matches"
    ergebnis = parser.parse(seiten, datei)
    apply_corrections(ergebnis, account)
    return ergebnis


def _faelle() -> list:
    return [pytest.param(account, datei, marks=pytest.mark.xdist_group(datei.name))
            for account, datei in _cases()]


@pytest.mark.skipif(not _cases(), reason="no statements present")
@pytest.mark.parametrize("account,pdf", _faelle(),
                         ids=lambda v: v.name[:22] if isinstance(v, Path) else v)
def test_statement_reconciles(account: str, pdf: Path):
    result = _geparst(account, pdf)
    rec = reconcile(result)

    assert rec.ok, (
        f"{pdf.name}: {len(result.transactions)} txns parsed, "
        f"off by {rec.delta_cents} cents "
        f"(expected {rec.expected_cents}, got {rec.actual_cents})"
    )
    assert result.transactions, "statement reconciled but contained no transactions"


@pytest.mark.skipif(not _cases(), reason="no statements present")
@pytest.mark.parametrize("account,pdf", _faelle(),
                         ids=lambda v: v.name[:22] if isinstance(v, Path) else v)
def test_booking_dates_fall_inside_period(account: str, pdf: Path):
    """A date outside the statement period means a false-positive row match.

    Reconciliation alone would not catch this: a row picked up from a
    neighbouring table can still balance if its amount happens to net out.
    """
    result = _geparst(account, pdf)
    start, end = result.header.period_start, result.header.period_end
    stray = [t.booking_date for t in result.transactions if not (start <= t.booking_date <= end)]
    assert not stray, f"{pdf.name}: {len(stray)} bookings outside {start}..{end}: {stray[:5]}"
