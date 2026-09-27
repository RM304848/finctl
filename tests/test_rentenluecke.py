"""Die Rentenluecke: Kapital zum Rentenbeginn, gerechnet statt getippt."""

from __future__ import annotations

import pytest

from finctl.forecast import rentenluecke as rl


def test_without_growth_and_return_it_is_the_gap_times_the_years():
    assert rl.kapitalbedarf(10_000_00, 30, 0.0, 0.0, 1.0) == 300_000_00


def test_a_return_above_inflation_needs_less_capital():
    ohne = rl.kapitalbedarf(10_000_00, 30, 0.02, 0.02, 1.0)
    mit = rl.kapitalbedarf(10_000_00, 30, 0.05, 0.02, 1.0)
    assert ohne == 300_000_00 and mit < ohne


def test_keeping_half_needs_more_capital():
    """Wer die Haelfte uebrig lassen will, braucht mehr am Anfang -- und am
    Ende steht genau diese Haelfte da."""
    ganz = rl.kapitalbedarf(10_000_00, 30, 0.05, 0.02, 1.0)
    halb = rl.kapitalbedarf(10_000_00, 30, 0.05, 0.02, 0.5)
    assert halb > ganz
    kapital = float(halb)
    for t in range(30):
        kapital = (kapital - 10_000_00 * 1.02 ** t) * 1.05
    assert kapital == pytest.approx(halb * 0.5, rel=1e-6)


def test_no_gap_needs_no_capital():
    assert rl.kapitalbedarf(-5_000_00, 30, 0.05, 0.02, 1.0) == 0
