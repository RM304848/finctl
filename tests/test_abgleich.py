"""Does the forecast describe the same world as the ledger?

The one property that makes the comparison meaningful is that the blocks
partition the ledger: every split lands in exactly one, so the parts add up to
the whole. Without it a difference could be double counting or an omission and
nothing would say which.

Gemessen wird ueber die letzten zwoelf vollstaendigen Monate. Ueber das
angebrochene Kalenderjahr hochgerechnet zaehlte jede Jahreszahlung, die in die
bisherigen Monate fiel, anderthalbfach, und was erst im Herbst kommt, fehlte.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finctl.forecast import abgleich as ag

DB = Path("data/finance.db")
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no ledger present")


@pytest.fixture
def conn():
    from finctl.ledger.db import connect

    c = connect()
    yield c
    c.close()


def test_the_blocks_partition_the_ledger(conn):
    """Parts equal the whole, or something is counted twice or dropped."""
    result = ag.build(conn)
    assert result.months
    assert result.unassigned_cents == 0, f"{result.unassigned_cents} cents in keinem Block"


def test_the_window_is_the_last_twelve_complete_months(conn):
    """Ein Monat zählt erst, wenn ihn jedes Konto vollständig abdeckt."""
    ende = ag.letzter_vollstaendiger_monat(conn)
    f = ag.fenster(conn)
    assert f.bis == ende
    assert f.monate == 12
    assert f.von == date(ende.year - (ende.month < 12), ende.month % 12 + 1, 1)


def test_loan_payments_are_not_counted_as_fixed_costs_as_well(conn):
    """Both loan categories carry the fixkosten flag; the block order stops
    them being counted twice."""
    ids = [b.id for b in ag.bloecke()]
    assert ids.index("kredit") < ids.index("fixkosten")

    f = ag.fenster(conn)
    measured = ag.measure(conn, f)
    naive = conn.execute("""
        SELECT COALESCE(SUM(s.amount_cents), 0)
        FROM   splits s JOIN transactions t ON t.id = s.transaction_id
        JOIN   mgmt_categories m ON m.id = s.mgmt_category_id
        WHERE  m.fixkosten = 1 AND t.booking_date BETWEEN ? AND ?
    """, (f.von.isoformat(), f.bis.isoformat())).fetchone()[0]
    assert abs(measured["fixkosten"]) < abs(naive)


def test_a_schedule_that_starts_inside_the_window_says_so(conn):
    """A schedule anchored at the last observed balance cannot reproduce the
    months before it, and the row has to say so rather than read as zero."""
    row = ag.build(conn, ag.planned(conn)).by_id("kredit")
    if row.window_months != row.window_months or not row.note:
        pytest.skip("alle Tilgungspläne decken das Fenster")
    assert "ab 20" in row.note or "nicht vergleichbar" in row.note
    # Ob der Vorlagenkredit mitzaehlt, entscheidet der Schalter auf /planung.
    from finctl.forecast import szenarien as sz

    if "eigenheim" in sz.aktive_ids():
        assert "Eigenheim" in row.note, "ein eingeschalteter Vorlagenkredit zählt"
    else:
        assert "Eigenheim" not in row.note, "ein abgeschalteter Vorlagenkredit zählt nicht"


def test_transfers_are_outside_the_partition(conn):
    """Money moved between his own accounts is neither income nor cost."""
    f = ag.fenster(conn)
    total = ag.total_excluding_transfers(conn, f)
    with_transfers = conn.execute("""
        SELECT COALESCE(SUM(s.amount_cents), 0)
        FROM   splits s JOIN transactions t ON t.id = s.transaction_id
        WHERE  t.booking_date BETWEEN ? AND ?
    """, (f.von.isoformat(), f.bis.isoformat())).fetchone()[0]
    assert total != with_transfers
