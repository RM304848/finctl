"""Goal progress.

Three rules decide whether the bar tells the truth, and each of them was
learned the hard way:

* An obligation is subtracted. The sister's money sits in the same account as
  the buffer, and counting it made the portfolio look a third larger.

* Every goal measures against the SAME liquid assets. Letting each goal name
  its own list produced two comparable targets measured against different
  pots -- and one of the lists named kinds that no longer existed, so it
  counted nothing at all while looking precise.

* No return is assumed. The gap is the target minus what exists, the rate is
  that gap over the months left. Compounding would shrink both, and being told
  to save less than necessary is the expensive direction to be wrong in.
"""

from __future__ import annotations

from datetime import date

from finctl.forecast import ziele as z
from finctl.forecast.ziele import Progress, progress

GOALS = {"ziele": [
    {"id": "puffer", "name": "Puffer", "cents": 2_700_000},
    {"id": "fire", "name": "FIRE", "cents": 100_000_000,
     "stichtag": date(2045, 12, 31)},
]}


def _balances(scalable=2_000_000, depot=500_000, rente=2_244_602, owed=1_700_000):
    return {
        "balances": [
            {"account_id": "scalable", "kind": "tagesgeld", "cents": scalable},
            {"name": "Depot", "kind": "depot", "cents": depot},
            {"name": "Krypto-Boerse", "kind": "krypto", "cents": None},
            {"name": "Rentenpolice", "kind": "rentenversicherung", "cents": rente},
        ],
        "obligations": [{"name": "Sister", "cents": owed}],
    }


# ------------------------------------------------------------ what counts

def test_an_obligation_is_subtracted():
    """The sister's money sits in the same account as the buffer."""
    assert z.liquid_cents(_balances()) == \
        2_000_000 + 500_000 + 2_244_602 - 1_700_000


def test_pensions_count_towards_what_has_been_built():
    """Counted by the owner's decision, and the caveat lives elsewhere.

    This page answers "what have I built". That a policy cannot be drawn
    before a given age or before its maturity is true and material, but it is
    the question the phase model answers -- mixing the two here would mean
    neither is answered cleanly.
    """
    assert z.liquid_cents(_balances(rente=2_244_602)) - \
           z.liquid_cents(_balances(rente=0)) == 2_244_602


def test_a_holding_with_no_value_is_skipped_not_counted_as_zero():
    """Staked ETH is worth something. Showing it as nothing would look like a
    measurement rather than a gap in the data."""
    assert z.liquid_cents(_balances()) == z.liquid_cents(_balances())


def test_every_goal_measures_against_the_same_pot():
    rows = progress(GOALS, _balances())
    assert len({r.have_cents for r in rows}) == 1
    assert rows[0].have_cents == z.liquid_cents(_balances())


# ------------------------------------------------------------ the due date

def test_a_date_written_as_a_string_survives():
    """The dashboard overlay writes ISO strings; goals.yaml yields date
    objects. Accepting only the latter is why a date typed into the page
    vanished the moment it was saved.
    """
    goals = {"ziele": [{"id": "x", "name": "X", "cents": 1000,
                        "stichtag": "2046-01-01"}]}
    assert progress(goals, _balances())[0].due == date(2046, 1, 1)


def test_an_unparseable_date_is_dropped_rather_than_crashing():
    goals = {"ziele": [{"id": "x", "name": "X", "cents": 1000,
                        "stichtag": "irgendwann"}]}
    assert progress(goals, _balances())[0].due is None


# ------------------------------------------------------- the required rate

def test_the_rate_is_straight_division_with_no_compounding():
    p = Progress("x", "X", target_cents=1_200_000, have_cents=0,
                 due=date(2027, 9, 1))
    assert p.monthly_needed_cents(date(2026, 9, 1)) == 1_200_000 // 12


def test_a_goal_with_no_date_has_no_rate():
    """"Per month until never" has no answer, so it must not invent one."""
    assert Progress("x", "X", 1000, 0).monthly_needed_cents(date(2026, 9, 1)) is None


def test_a_goal_already_due_needs_the_whole_remainder_now():
    p = Progress("x", "X", 1000, 200, due=date(2026, 9, 1))
    assert p.monthly_needed_cents(date(2026, 9, 1)) == 800


# ---------------------------------------------------------------- coverage

def test_promised_money_removes_the_rate_without_faking_the_balance():
    """A maturing endowment covers a goal, but it is not saved yet.

    Folding it into the balance would show the goal as met years before any
    money exists -- the one thing a progress bar must never do.
    """
    p = Progress("t", "T", 1_000_000, 0, due=date(2030, 1, 1),
                 covered_cents=900_000)
    assert p.pct == 0.0
    assert p.uncovered_cents == 100_000
    full = Progress("t", "T", 1_000_000, 0, due=date(2030, 1, 1))
    assert p.monthly_needed_cents(date(2026, 9, 1)) < \
           full.monthly_needed_cents(date(2026, 9, 1))


def test_progress_never_exceeds_one_hundred_percent():
    assert Progress("x", "X", 1000, 5000).pct == 100.0


def test_a_liability_is_not_a_goal():
    goals = {"ziele": [{"id": "s", "name": "S", "cents": 1, "art": "verbindlichkeit"}]}
    assert progress(goals, _balances()) == []


def test_crypto_counts_as_a_depot():
    """Ein Ziel ueber "alle Depots" zaehlt die Coins mit, und eine Basis, die
    beide nennt, zaehlt sie nicht doppelt."""
    from finctl.forecast import ziele as z

    bestand = {"balances": [{"name": "etf", "kind": "depot", "cents": 1000},
                            {"name": "coins", "kind": "krypto", "cents": 500},
                            {"name": "giro", "kind": "giro", "cents": 7}]}
    assert z.liquid_cents(bestand, ("depot",)) == 1500
    assert z.basis_von({"basis": ["depot", "krypto"]}) == ("depot",)
    assert z.liquid_cents(bestand, z.basis_von({"basis": ["depot", "krypto"]})) == 1500
