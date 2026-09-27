"""DKB Girokonto parser.

The synthetic tests are the real regression guard: they encode the kerning
defect that made five of nine statements fail reconciliation on the first run.
The golden test runs against actual statements when they are present, and skips
otherwise -- statement PDFs are gitignored, so a fresh clone has none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finctl.ingest.importer import extract_pages, reconcile
from finctl.ingest.profiles.dkb_giro import _BALANCE, _TXN, PARSER, _iso

STATEMENTS = Path("data/statements/dkb-giro")


# ------------------------------------------------------- the kerning defect

@pytest.mark.parametrize(
    "line,expect_date,expect_amount",
    [
        ("05.08.2026Basislastschrift -180,00", "05.08.2026", "-180,00"),
        # pdfplumber reads DKB's kerning as real spaces *inside* the date.
        # Both of these appear verbatim in the March 2026 statement, and a
        # strict \d{2}\.\d{2}\.\d{4} silently skipped them.
        ("2 6.02.2026Zahlungseingang 890,00", "2 6.02.2026", "890,00"),
        ("0 3.03.2026Basislastschrift -550,00", "0 3.03.2026", "-550,00"),
        ("10.08.2026Echtzeitüberweisung -4.250,00", "10.08.2026", "-4.250,00"),
    ],
)
def test_txn_line_tolerates_stray_spaces(line, expect_date, expect_amount):
    m = _TXN.match(line)
    assert m is not None, f"failed to match: {line!r}"
    assert m.group("date") == expect_date
    assert m.group("amount") == expect_amount


def test_iso_strips_stray_spaces():
    assert _iso("2 6.02.2026") == "2026-02-26"
    assert _iso("05.08.2026") == "2026-08-05"


@pytest.mark.parametrize(
    "line",
    [
        "Kontostand am 04.08.2026, Auszug Nr. 8 3.344,67",
        "Kontostand am 04.09.2026 um 18:04 Uhr 3.487,38",
    ],
)
def test_balance_lines(line):
    assert _BALANCE.match(line) is not None


def test_description_line_is_not_a_transaction():
    """A continuation line that merely contains amounts must not start a txn."""
    assert _TXN.match("Tilgung 250,00 Zinsen 150,00") is None


# ------------------------------------------------------------------ golden

def _statement_files() -> list[Path]:
    return sorted(STATEMENTS.glob("*.pdf")) if STATEMENTS.is_dir() else []


@pytest.mark.skipif(not _statement_files(), reason="no DKB statements present")
@pytest.mark.parametrize("pdf", _statement_files(), ids=lambda p: p.name[:16])
def test_every_statement_reconciles(pdf):
    """opening + sum(transactions) == closing, for every real statement.

    This is the acceptance test for the parser: a statement that balances is
    arithmetically complete, so no transaction was dropped or double-counted.
    """
    result = PARSER.parse(extract_pages(pdf), pdf)
    rec = reconcile(result)
    assert rec.ok, (
        f"{pdf.name}: parsed {len(result.transactions)} txns, "
        f"off by {rec.delta_cents} cents"
    )
    assert result.transactions, "statement parsed but contained no transactions"
