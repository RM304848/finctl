"""Loan amortisation engine.

Validated against reality rather than against itself: Sparda prints the
interest/principal split on every payment, so the engine's output can be
compared to eight months of what the bank actually charged.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finctl.realestate.loan import Segment, amortise, annuity_for, verify_against_actuals

# Ein erfundenes Darlehen mit Anschluss: niedriger Zins bis 03/2027, dann
# hoeher bei hoeherer Rate. Die echten Vertraege stehen in loans.yaml.
ANFANGSSALDO = 15000000
ZINSSATZ = 1.25


def tilgungsplan():
    return amortise("001", ANFANGSSALDO, [
        Segment(date(2026, 1, 30), date(2027, 3, 31), ZINSSATZ,
                annuity_cents=52000, basis="actual"),
        Segment(date(2027, 4, 30), date(2061, 12, 31), 3.75,
                annuity_cents=72000, basis="assumption"),
    ])


def test_matches_the_banks_own_arithmetic():
    """The acceptance test: reproduce what the bank actually charged.

    Zins und Tilgung druckt die Bank je Rate auf den Auszug; sie stehen als
    `beobachtete_raten` am Kredit in loans.yaml, und der Tilgungsplan aus
    denselben Vertragsdaten muss sie treffen.
    """
    import yaml

    from finctl.realestate.loan import opening_balance_cents, segments_from

    kredite = [k for k in yaml.safe_load(open("config/loans.yaml", encoding="utf-8"))["loans"]
               if k.get("beobachtete_raten")]
    if not kredite:
        pytest.skip("kein Kredit mit beobachteten Raten in loans.yaml")
    for loan in kredite:
        plan = amortise(str(loan["id"]), opening_balance_cents(loan), segments_from(loan))
        raten = [(date.fromisoformat(f"{str(r['monat'])[:7]}-01"), r["zins_cents"],
                  r["tilgung_cents"]) for r in loan["beobachtete_raten"]]
        problems = verify_against_actuals(plan, raten, tolerance_cents=5)
        assert not problems, (loan["id"], problems)


def test_refinancing_slows_amortisation():
    """The point of modelling segments at all.

    From 2027-04 the payment rises while principal repayment FALLS. A model
    tracking only the payment amount would show this as getting worse by the
    difference in rate; it is actually worse by that plus the lost
    amortisation.
    """
    schedule = tilgungsplan()
    before = [p for p in schedule.payments if p.month <= date(2027, 3, 31)][-1]
    after = next(p for p in schedule.payments if p.month >= date(2027, 4, 1))

    assert after.payment_cents > before.payment_cents
    assert after.interest_cents > before.interest_cents * 2
    assert after.principal_cents < before.principal_cents


def test_balance_reaches_zero_and_never_goes_negative():
    schedule = tilgungsplan()
    assert all(p.balance_cents >= 0 for p in schedule.payments)
    assert schedule.payments[-1].balance_cents == 0


def test_principal_plus_interest_equals_payment():
    for p in tilgungsplan().payments:
        assert p.interest_cents + p.principal_cents == p.payment_cents


def test_sondertilgung_shortens_the_loan():
    base = tilgungsplan()
    extra = amortise("001", ANFANGSSALDO, [
        Segment(date(2026, 1, 30), date(2027, 3, 31), ZINSSATZ,
                annuity_cents=52000),
        Segment(date(2027, 4, 30), date(2061, 12, 31), 3.75,
                annuity_cents=72000),
    ], sondertilgung={date(2026, 6, 30): 1000000})
    assert len(extra.payments) < len(base.payments)
    assert extra.total_interest_cents() < base.total_interest_cents()


def test_annuity_that_cannot_cover_interest_is_rejected():
    """Silently compounding a debt would be worse than refusing to model it."""
    with pytest.raises(ValueError, match="does not cover"):
        amortise("x", 20000000, [
            Segment(date(2026, 1, 1), date(2030, 1, 1), 6.0, annuity_cents=5000),
        ])


def test_annuity_for_clears_the_balance():
    payment = annuity_for(10000000, 3.5, 20)
    schedule = amortise("x", 10000000, [
        Segment(date(2026, 1, 1), date(2050, 1, 1), 3.5, annuity_cents=payment),
    ])
    assert schedule.payments[-1].balance_cents == 0
    assert 19 * 12 <= len(schedule.payments) <= 20 * 12 + 1


# ------------------------------------------------- schedule-driven splitting

def test_schedule_split_is_refused_when_it_does_not_match_the_payment():
    """A drifted schedule must not restate what the account actually paid.

    The bank debited 745,00; if the modelled annuity says anything else, the
    segment is wrong and the payment stays whole rather than being broken down
    into two numbers that do not add up to it.
    """
    from types import SimpleNamespace

    from finctl.rules import categorize as cz
    from finctl.rules.engine import Rule

    rule = Rule(id="loan-x", priority=1, name="x", match={},
                actions={"loan": "999", "split_declared": True})
    tx = SimpleNamespace(booking_date="2026-09-01", raw_text="", amount_cents=-74500)

    cz._SCHEDULES["999"] = {"2026-09": (44601, 29899, "assumption")}
    assert cz._scheduled_parts(rule, tx, -74500) is not None

    cz._SCHEDULES["999"] = {"2026-09": (44601, 20000, "assumption")}
    assert cz._scheduled_parts(rule, tx, -74500) is None
    del cz._SCHEDULES["999"]


def test_schedule_split_marks_an_unconfirmed_rate():
    """A modelled split must say so; a stated one must not be second-guessed."""
    from types import SimpleNamespace

    from finctl.rules import categorize as cz
    from finctl.rules.engine import Rule

    rule = Rule(id="loan-x", priority=1, name="x", match={},
                actions={"loan": "998", "split_declared": True})
    tx = SimpleNamespace(booking_date="2026-09-01", raw_text="", amount_cents=-74500)

    cz._SCHEDULES["998"] = {"2026-09": (44601, 29899, "assumption")}
    zins, tilgung = cz._scheduled_parts(rule, tx, -74500)
    assert zins[0] == -44601 and zins[1]["mgmt"] == "kredit/zinsen"
    assert tilgung[0] == -29899 and tilgung[1]["mgmt"] == "kredit/tilgung"
    assert "unbestätigt" in zins[1]["note"]
    # Tilgung buys equity; it is never deductible.
    assert tilgung[1]["tax"] == "privat/nicht-abzugsfaehig"

    cz._SCHEDULES["998"] = {"2026-09": (44601, 29899, "actual")}
    zins, _ = cz._scheduled_parts(rule, tx, -74500)
    assert "unbestätigt" not in zins[1]["note"]
    del cz._SCHEDULES["998"]


def test_a_stated_split_beats_a_modelled_one():
    """The bank's own figures win wherever it printed them."""
    from finctl.rules.categorize import declared_split

    text = ("Rechnung Darl.-Leistung 4711000001 Tilgung 250,00 Zinsen 150,00")
    assert declared_split(text) == (15000, 25000)


# ------------------------------------- validated against the lender's own plan

def test_forward_offer_reproduces_the_lenders_tilgungsplan():
    """The engine against an independent authority, not against itself.

    A lender's forward offer prints its own amortisation schedule. Reproducing
    it to the cent is the strongest check available that interest is charged on
    the right balance, in the right order, with the right rounding -- and it is
    the schedule that decides how much of every payment is deductible under
    Anlage V. Offer and expectation both come from config, not from here.
    """
    from datetime import date

    import yaml

    from finctl.realestate.loan import amortise, opening_balance_cents, segments_from

    kredite = [k for k in yaml.safe_load(open("config/loans.yaml", encoding="utf-8"))["loans"]
               if k.get("angebot_tilgungsplan")]
    if not kredite:
        pytest.skip("kein Kredit mit abgeschriebenem Tilgungsplan in loans.yaml")
    for loan in kredite:
        kennung = str(loan["id"])
        schedule = amortise(kennung, opening_balance_cents(loan), segments_from(loan))
        by_month = {p.month.strftime("%Y-%m"): p for p in schedule.payments}

        # Zins, Tilgung, Darlehensstand, so wie das Angebot sie druckt.
        for zeile in loan["angebot_tilgungsplan"]:
            monat = str(zeile["monat"])[:7]
            p = by_month[monat]
            assert (p.interest_cents, p.principal_cents, p.balance_cents) == \
                   (zeile["zins_cents"], zeile["tilgung_cents"], zeile["stand_cents"]), \
                   f"{kennung} {monat}"

        # Summen ueber Jahrzehnte, in denen sich jede Monatsabweichung aufhaeuft.
        ab = date.fromisoformat(str(loan["followup_from"])).replace(day=1)
        if loan.get("followup_total_interest_cents"):
            danach = [p for p in schedule.payments if p.month >= ab]
            assert sum(p.interest_cents for p in danach) == \
                loan["followup_total_interest_cents"], kennung
        if loan.get("followup_balance_at_fixing_cents"):
            ende = ab.replace(year=ab.year + int(loan["followup_zinsbindung_years"]))
            am_ende = [p for p in schedule.payments if p.month < ende][-1]
            assert abs(am_ende.balance_cents - loan["followup_balance_at_fixing_cents"]) <= 100


def test_a_segment_may_state_its_own_opening_balance():
    """A prolongation names its own Darlehensbetrag, and that beats carry-forward."""
    from datetime import date

    from finctl.realestate.loan import Segment, amortise

    segments = [
        Segment(start=date(2026, 1, 31), end=date(2026, 3, 31),
                annual_rate_pct=1.25, annuity_cents=52000, basis="actual"),
        Segment(start=date(2026, 4, 30), end=date(2026, 5, 31),
                annual_rate_pct=3.9, annuity_cents=70000, basis="offer",
                opening_balance_cents=11800000),
    ]
    schedule = amortise("t", 13000000, segments)
    first_of_second = next(p for p in schedule.payments if p.segment_index == 1)
    # 118.000,00 * 3,9% / 12 = 383,50
    assert first_of_second.interest_cents == 38350
    assert first_of_second.basis == "offer"


def test_a_stated_afa_base_beats_a_reconstructed_one():
    """The tax return's figure is a fact; price x (1 - land share) is a guess.

    Reconstructing the base also silently omits notary and Grunderwerbsteuer,
    which do belong in it -- so the two do not merely differ in confidence,
    they differ in what they include.
    """
    import sqlite3

    from finctl.realestate import kpi as kpimod

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(open("finctl/ledger/schema.sql", encoding="utf-8").read())
    db.execute(
        "INSERT INTO properties (id, name, status, purchase_price_cents, "
        "incidental_costs_cents, land_share_pct, afa_rate_pct, afa_base_cents) "
        "VALUES ('x','X','rented', 10000000, 1000000, 20.0, 3.0, 12500000)")

    k = kpimod.compute(db, "x")
    assert k.afa_base_cents == 12500000          # stated, not 8.800.000
    assert k.afa_annual_cents == 375000          # 3.750,00, as filed

    # Without a stated base the reconstruction still applies.
    db.execute("UPDATE properties SET afa_base_cents = NULL WHERE id = 'x'")
    k2 = kpimod.compute(db, "x")
    assert k2.afa_base_cents == 8800000


def test_moveable_asset_afa_is_added_beside_the_building():
    """Kitchen and fittings run on their own life, not the building's rate.

    A property can claim Gebäude-AfA at 2 % plus a separate annual amount on
    "diverse Wirtschaftsgüter". Folding the latter into the building base would
    depreciate it over fifty years instead of ten.
    """
    import sqlite3

    from finctl.realestate import kpi as kpimod

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(open("finctl/ledger/schema.sql", encoding="utf-8").read())
    db.execute(
        "INSERT INTO properties (id, name, status, afa_rate_pct, afa_base_cents, "
        "afa_extra_annual_cents) VALUES ('x','X','rented', 2.0, 19000000, 100000)")

    k = kpimod.compute(db, "x")
    assert k.afa_base_cents == 19000000
    assert k.afa_annual_cents == 480000          # 3.800,00 + 1.000,00


def test_a_rule_split_covers_every_matching_transaction():
    """One breakdown done by hand becomes the rule for all of them.

    A shared-cost payment covers several components at once. Left whole it
    parks the whole contribution in one category while another reads gross.

    DIE REGELN WERDEN UEBER IHRE FORM GESUCHT, nicht ueber ihre Kennung: alle
    mit mindestens drei festen Teilen. Vorher stand hier der Name EINER
    bestimmten Regel aus der eigenen `rules.yaml` -- und damit ein
    Personenname im Test. Jetzt laufen alle durch, nicht nur die eine.
    """
    from finctl.rules import engine
    from finctl.rules.categorize import _split_amounts

    regeln = [r for r in engine.load_rules()
              if len(r.splits or []) >= 3
              and all("amount" in teil for teil in r.splits[:-1])]
    assert regeln, "keine Regel mit festen Teilbetraegen in rules.yaml"

    for rule in regeln:
        fest = sum(int(round(float(teil["amount"]) * 100))
                   for teil in rule.splits[:-1])
        summe = fest + 4500            # ein Rest, der auf keinen Teil passt
        parts = _split_amounts(rule, summe)

        # Die Teile gehen EXAKT auf -- ein Restcent, der verschwindet, ist eine
        # Buchung, die nicht mehr auf ihre Zahlung passt.
        assert sum(cents for cents, _ in parts) == summe
        # Die festen Teile stehen so da, wie sie konfiguriert sind ...
        assert sum(cents for cents, _ in parts[:-1]) == fest
        # ... und der letzte bekommt die Differenz, keine feste Zahl.
        assert parts[-1][0] == 4500


# ------------------------------------- prolongation quoted the German way

def test_rate_plus_repayment_yields_the_annuity():
    """A bank quotes "3,8 % Zins, 2 % Tilgung", not a monthly payment.

    The payment follows as (rate + repayment) x balance / 12, computed on the
    balance at the START of the period. Getting this wrong by using the
    current balance each month would turn an annuity into a falling payment.
    """
    from finctl.realestate.loan import amortise, segments_from

    loan = {"id": "t", "principal_cents": 12_000_000,
            "segments": [{"start": date(2026, 1, 1), "end": date(2026, 12, 31),
                          "annual_rate_pct": 4.0, "annuity_cents": 55_000}],
            "anschluss": {"ab": "2027-01-01", "zins_pct": 4.5,
                          "tilgung_pct": 2.0}}
    sched = amortise("t", 12_000_000, segments_from(loan))
    after = [p for p in sched.payments if p.month.year == 2027]
    opening = [p for p in sched.payments if p.month.year == 2026][-1].balance_cents
    assert after[0].payment_cents == round(opening * 6.5 / 100 / 12)
    # Flat from there: the repayment share grows, the payment does not.
    assert after[0].payment_cents == after[-1].payment_cents
    assert after[-1].principal_cents > after[0].principal_cents


def test_a_higher_repayment_shortens_the_term():
    """The term is a RESULT of the two percentages, not a third input.

    This is why the form asks for no end date: entering one as well would let
    a combination be saved that cannot repay by the date typed, and the
    schedule would then disagree with the form.
    """
    from finctl.realestate.loan import amortise, segments_from

    def payoff(tilgung):
        loan = {"id": "t", "principal_cents": 12_000_000,
                "segments": [{"start": date(2026, 1, 1), "end": date(2026, 12, 31),
                              "annual_rate_pct": 4.0, "annuity_cents": 55_000}],
                "anschluss": {"ab": "2027-01-01", "zins_pct": 4.5,
                              "tilgung_pct": tilgung}}
        return amortise("t", 12_000_000, segments_from(loan)).payoff_month()

    assert payoff(3.0) < payoff(2.0) < payoff(1.0)


def test_a_loan_without_a_prolongation_is_unchanged():
    """Adding the feature must not alter a loan that does not use it."""
    from finctl.realestate.loan import amortise, segments_from

    loan = {"id": "t", "principal_cents": 12_000_000,
            "segments": [{"start": date(2026, 1, 1), "end": date(2030, 12, 31),
                          "annual_rate_pct": 4.0, "annuity_cents": 55_000}]}
    assert len(segments_from(loan)) == 1
    assert amortise("t", 12_000_000, segments_from(loan)).payments[-1].month.year == 2030


def _eingereichte_zinsen() -> list[tuple[str, int, int]]:
    """Objekt, Jahr und eingereichte Schuldzinsen -- aus config/properties.yaml.

    Die Zahl stand frueher samt Objektkennung hier im Test. Sie gehoert neben
    die Unterlage, aus der sie stammt: `filed_<jahr>.schuldzinsen_cents`. Wer
    dieses Werkzeug uebernimmt, traegt dort seine eigene ein -- und bekommt
    denselben Abgleich, ohne dass eine fremde Wohnung im Test steht.
    """
    import re

    import yaml

    pfad = Path("config/properties.yaml")
    if not pfad.exists():
        return []
    aus = []
    for prop in (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}).get(
            "properties") or []:
        for schluessel, block in prop.items():
            if (treffer := re.fullmatch(r"filed_(\d{4})", str(schluessel))) and \
                    isinstance(block, dict) and block.get("schuldzinsen_cents"):
                aus.append((str(prop["id"]), int(treffer.group(1)),
                            int(block["schuldzinsen_cents"])))
    return aus


def test_the_ledger_reproduces_the_interest_that_was_filed():
    """Independent confirmation that the backward derivation is right.

    Seventeen payments carried no printed split, so the rule fell back to
    booking the whole annuity as Tilgung -- several thousand euro of
    deductible interest that appeared in no Anlage V. The schedule was
    extended backwards to the first annuity, with the opening balance solved
    from the observed interest of a later payment.

    The check that it is not merely self-consistent: the ledger's total is
    compared against what was ACTUALLY FILED, and nothing in the derivation
    used that figure. Both sides come from config, not from this file.
    """
    import sqlite3

    eingereicht = _eingereichte_zinsen()
    if not eingereicht:
        pytest.skip("keine eingereichten Schuldzinsen in properties.yaml")
    db = Path("data/finance.db")
    if not db.exists():
        pytest.skip("no ledger present")

    conn = sqlite3.connect(db)
    try:
        erster, letzter = conn.execute(
            "SELECT MIN(booking_date), MAX(booking_date) FROM transactions").fetchone()
        for objekt, jahr, soll in eingereicht:
            # Ein Jahr, das die Auszuege nicht ganz abdecken, sagt nichts.
            if not erster or erster > f"{jahr}-01-31" or letzter < f"{jahr}-12-01":
                continue
            booked = conn.execute("""
                SELECT COALESCE(SUM(-s.amount_cents), 0)
                FROM   splits s JOIN transactions t ON t.id = s.transaction_id
                WHERE  s.property_id = ? AND s.mgmt_category_id = 'kredit/zinsen'
                  AND  substr(t.booking_date, 1, 4) = ?
            """, (objekt, str(jahr))).fetchone()[0]
            assert abs(booked - soll) < 100, \
                f"{objekt} {jahr}: gebucht {booked}, eingereicht {soll}"
    finally:
        conn.close()


def test_no_annuity_is_booked_whole():
    """A payment booked whole is a deduction left on the table.

    Gesucht wird ueber die KATEGORIE, nicht ueber einen Betrag: eine Annuitaet
    mit genau einem `kredit/`-Teil ist eine, bei der Zins und Tilgung nicht
    getrennt wurden. Vorher stand hier die Rate eines bestimmten Kredits als
    feste Zahl -- die faengt genau diesen einen und keinen zweiten.

    NUR ABFLUESSE. Ohne diese Einschraenkung meldete die Probe eine Rueckzahlung
    von 18,50 aus einer Ueberzahlung -- ein Geldeingang, der voellig zu Recht
    nur aus Tilgung besteht. Eine Annuitaet ist Geld, das hinausgeht.
    """
    import sqlite3

    db = Path("data/finance.db")
    if not db.exists():
        pytest.skip("no ledger present")
    conn = sqlite3.connect(db)
    try:
        whole = conn.execute("""
            SELECT COUNT(*) FROM transactions t
            WHERE  (SELECT COUNT(*) FROM splits s
                    WHERE s.transaction_id = t.id
                      AND s.mgmt_category_id LIKE 'kredit/%') = 1
              AND  EXISTS (SELECT 1 FROM splits s2
                           WHERE s2.transaction_id = t.id
                             AND s2.mgmt_category_id = 'kredit/tilgung')
              AND  t.booking_date >= '2024-05-01'
              AND  t.amount_cents < 0
        """).fetchone()[0]
    finally:
        conn.close()
    assert whole == 0, f"{whole} Annuitäten stehen ungeteilt im Ledger"


def test_the_rules_know_no_loan_and_the_loans_sign_up():
    """Die Regeln gehoeren zur Basis und importieren kein Modul. Den
    Tilgungsplan meldet finctl/kredite.py an, angeschlossen von der App."""
    from finctl import kredite
    from finctl.rules import categorize as cz

    quelle = (Path("finctl/rules/categorize.py")).read_text(encoding="utf-8")
    assert "realestate" not in quelle
    assert kredite.tilgungsplan in cz._TILGUNGSPLAENE
    # Doppelt anmelden zaehlt einmal.
    kredite.anmelden()
    assert cz._TILGUNGSPLAENE.count(kredite.tilgungsplan) == 1
