"""Categorizing twice must produce identical splits.

This is the regression test for the no-AI-at-runtime guarantee: the pipeline
is a pure function of (statements, rulebook, config). If it were not, no
report built on the ledger could be defended -- and a tax figure that changes
between runs is worthless.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finctl.ledger import db as ledger
from finctl.rules import categorize as cat

DB = Path("data/finance.db")


def _fingerprint(conn) -> list[tuple]:
    return conn.execute(
        """
        SELECT t.dedup_hash, s.seq, s.amount_cents, s.mgmt_category_id,
               s.tax_category_id, s.property_id, s.transfer_account_id,
               s.source, s.rule_id
        FROM   splits s JOIN transactions t ON t.id = s.transaction_id
        ORDER  BY t.dedup_hash, s.seq
        """
    ).fetchall()


@pytest.mark.skipif(not DB.exists(), reason="no ledger present")
def test_categorize_is_deterministic():
    conn = ledger.connect(DB)
    try:
        cat.categorize(conn, recompute=True)
        first = _fingerprint(conn)
        cat.categorize(conn, recompute=True)
        second = _fingerprint(conn)
    finally:
        conn.close()

    assert first == second, "categorize produced different splits on a second run"
    assert first, "no splits produced at all"


@pytest.mark.skipif(not DB.exists(), reason="no ledger present")
def test_split_sum_invariant_holds():
    conn = ledger.connect(DB)
    try:
        cat.categorize(conn, recompute=True)
        imbalances = ledger.split_imbalances(conn)
    finally:
        conn.close()
    assert not imbalances, f"{len(imbalances)} transactions whose splits do not sum"


@pytest.mark.skipif(not DB.exists(), reason="no ledger present")
def test_every_transaction_has_at_least_one_split():
    """Unmatched transactions still get a split, so no money disappears from
    reporting just because no rule claimed it."""
    conn = ledger.connect(DB)
    try:
        cat.categorize(conn, recompute=True)
        orphans = conn.execute(
            "SELECT COUNT(*) FROM transactions t WHERE NOT EXISTS "
            "(SELECT 1 FROM splits s WHERE s.transaction_id = t.id)"
        ).fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0
