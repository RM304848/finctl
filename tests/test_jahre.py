"""Die Jahresrechnung: wie viel Prozent von Barista FIRE stehen 2045?

Getestet wird die Struktur, nicht eine Eurozahl. Vier Eigenschaften
unterscheiden sie von einer Hochrechnung auf einem Bierdeckel, und jede davon
wurde beim Bauen einmal falsch gemacht:

* Das Basisjahr laeuft nur ueber seine RESTmonate. Die vergangenen stecken
  schon im Anfangsbestand, und sie ein zweites Mal zu projizieren zaehlte bei
  acht von zwoelf Monaten zwei Drittel eines Jahres doppelt.
* Kreditraten kommen aus dem Tilgungsplan und werden NICHT inflationiert.
  Eine Annuitaet ist nominal fest und wird real jedes Jahr billiger.
* Das Ziel skaliert mit der Inflation des Szenarios. Ohne das lieferte das
  pessimistische Szenario mehr Endkapital als die Basis -- oekonomisch
  richtig, weil Inflation Schuldner beguenstigt, und als Vergleich unsinnig.
* Sie benutzt dieselben Bloecke wie die Abstimmzeile, sonst prueft die
  Abstimmzeile etwas anderes als das, was gerechnet wird.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from finctl.forecast import abgleich as ag
from finctl.forecast import jahre as jm

DB = Path("data/finance.db")
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no ledger present")


@pytest.fixture
def conn():
    from finctl.ledger.db import connect

    c = connect()
    yield c
    c.close()


def test_the_base_year_runs_only_over_its_remaining_months(conn):
    """The elapsed months are already in the opening balance."""
    result = jm.project(conn, base_year=2026, end_year=2030, opening_cents=0)
    ende = ag.letzter_vollstaendiger_monat(conn)
    elapsed = ende.month if ende.year == 2026 else 0
    base = result.year(2026)
    later = result.year(2027)
    share = (12 - elapsed) / 12
    assert base.blocks["konsum"] == pytest.approx(
        later.blocks["konsum"] / (1 + 0.02) * share, rel=0.02)


def test_loan_payments_are_not_inflated(conn, ohne_plaene):
    """An annuity is fixed in nominal terms.

    Inflating it would erase the borrower's quiet advantage -- the payment
    that gets cheaper in real terms every year.
    """
    result = jm.project(conn, base_year=2026, end_year=2035, opening_cents=0,
                        szenarien=ohne_plaene)
    payments =[abs(result.year(y).blocks["kredit"]) for y in (2028, 2029, 2030)]
    # Constant while all three schedules run, never rising with prices.
    assert payments[0] == payments[1] == payments[2]


def test_costs_are_inflated(conn):
    result = jm.project(conn, base_year=2026, end_year=2035, opening_cents=0)
    a, b = (abs(result.year(y).blocks["konsum"]) for y in (2028, 2029))
    assert b > a
    assert b / a == pytest.approx(1.02, rel=0.001)


def test_investment_flows_are_left_out(conn):
    """A purchase moves money from the account into the depot, and both count
    towards the goal. Booking it as a cost would subtract it twice."""
    result = jm.project(conn, base_year=2026, end_year=2027, opening_cents=0)
    assert "investment" not in result.year(2027).blocks


def test_the_target_scales_with_the_scenario_inflation(tmp_path):
    """Otherwise the pessimistic scenario wins.

    Higher inflation lifts income and costs together while the loan payment
    stays fixed, so a net saver with a fixed-rate mortgage ends up nominally
    better off. That is economically right; leaving a target that was set in
    today's prices as a fixed nominal figure is what made the comparison
    nonsense.
    """
    other = tmp_path / "assumptions.hoch.yaml"
    other.write_text(yaml.safe_dump({"inflation_pa": 0.03}), encoding="utf-8")
    base = jm.target_under("config/assumptions.yaml", declared_cents=83_100_000,
                           declared_inflation=0.02, years=19)
    higher = jm.target_under(other, declared_cents=83_100_000,
                             declared_inflation=0.02, years=19)
    assert base == 83_100_000
    assert higher > base


def test_a_scenario_is_a_different_file(conn):
    """No feature, no table -- a copy of assumptions.yaml with other values."""
    optimistic = Path("config/assumptions.optimistisch.yaml")
    if not optimistic.exists():
        pytest.skip("kein Szenario abgelegt")
    base = jm.project(conn, end_year=2045, opening_cents=9_618_113)
    other = jm.project(conn, end_year=2045, opening_cents=9_618_113,
                       assumptions=optimistic)
    assert other.final_cents > base.final_cents


def test_the_projection_and_the_reconciliation_share_their_blocks():
    """Two partitions would make the reconciliation line check the wrong thing.

    Its zero differences would then earn trust they had not paid for.
    """
    known = {b.id for b in ag.bloecke()}
    assert set(jm.FROM_BASE) | set(jm.SPECIAL) | set(jm.IGNORED) == known


def _netto() -> int:
    """Was die Objekte mit Prognose zusammen im Monat abwerfen."""
    from finctl import objekte

    liste = objekte.prognosen()
    if not liste:
        pytest.skip("kein Objekt mit Prognose")
    return sum(p.netto_cents for p in liste)


def test_the_object_comes_from_the_assumption_not_one_measured_month(conn):
    """Genau ein Mietmonat steht im Ledger.

    Ueber acht vollstaendige Monate gemittelt sind das 163 statt 1.420. Diese
    Verwaesserung zwanzig Jahre fortzuschreiben waere der groesste stille
    Fehler im Modell gewesen -- sie kostete 445.000 im Endkapital, also mehr
    als Gehalt und Rendite zusammen.
    """
    result = jm.project(conn, base_year=2026, end_year=2030, opening_cents=0)
    voll = result.year(2028).blocks[ag.OBJEKT]
    # Ein voller Jahrgang liegt nahe am Zwoelffachen der Annahme, nicht beim
    # gemessenen Achtel.
    assert voll > _netto() * 11
    assert voll < _netto() * 13


def test_the_leasehold_stops_producing_at_its_end_date(conn):
    """Kein Eigentum: am Ende der Laufzeit null Einnahme und null Restwert.

    Genau das unterscheidet ein Leasehold vom Eigentum, und es ist der
    Grund, warum der Stichtag der Jahresrechnung dort liegt.
    """
    from finctl import objekte

    enden = [p.bis for p in objekte.prognosen()]
    if not enden or None in enden:
        pytest.skip("kein Objekt mit Ende der Vermietung")
    ende = max(enden).year
    result = jm.project(conn, base_year=2026, end_year=ende + 2, opening_cents=0)
    assert result.year(ende).blocks[ag.OBJEKT] > 0
    assert result.year(ende + 1).blocks[ag.OBJEKT] == 0
    assert result.year(ende + 2).blocks[ag.OBJEKT] == 0


def test_the_base_year_counts_only_the_months_still_to_come(conn):
    """Der August steht schon im Ledger und im Anfangsbestand."""
    jahr = date.today().year
    result = jm.project(conn, base_year=jahr, end_year=jahr + 1, opening_cents=0)
    ende = ag.letzter_vollstaendiger_monat(conn)
    elapsed = ende.month if ende.year == jahr else 0
    assert result.year(jahr).blocks[ag.OBJEKT] == _netto() * (12 - elapsed)


# ---------------------------------------------------- Crossing Point, Schritt 9

def test_the_crossing_point_ignores_salary(conn):
    """Das ist der ganze Punkt der Kennzahl.

    Ein Zielbetrag sagt, ob man aufhören KANN -- Kapital, das sich verzehren
    lässt. Der Wendepunkt sagt, ab wann man nicht mehr MUSS. Man kann das eine
    erreichen und das andere nie, und genau das ist hier der Fall: 103 %
    Kapitaldeckung 2045 bei 59 % laufender Deckung.
    """
    assert "gehalt" not in jm.PASSIV
    assert "sondereffekt" not in jm.PASSIV


def test_a_one_off_does_not_count_as_coverage(conn):
    """Eine grosse Erstattung liesse ein Jahr wie erreichte Unabhängigkeit
    aussehen. Sie deckt keinen laufenden Bedarf."""
    assert "sondereffekt" not in jm.PASSIV
    assert "sondereffekt" not in jm.BEDARF


def test_loan_payments_count_as_ongoing_need(conn):
    """Sie laufen weiter, ob gearbeitet wird oder nicht -- und sie enden von
    selbst, was der Wendepunkt sichtbar machen soll."""
    assert "kredit" in jm.BEDARF


def test_the_crossing_year_must_hold_to_the_end(conn):
    """Ein einzelnes gutes Jahr zwischen zwei schlechten ist kein Wendepunkt.

    Ohne diese Bedingung meldete ein Jahr mit hoher Objektauslastung
    Unabhängigkeit, die im Folgejahr wieder weg ist.
    """
    result = jm.project(conn, base_year=2026, end_year=2045, opening_cents=0)
    jahr = result.crossing_year
    if jahr is None:
        pytest.skip("auf den aktuellen Annahmen nicht erreicht")
    spaeter = [y for y in result.years if y.year >= jahr]
    assert all(jm._covers(y) for y in spaeter)


def test_coverage_is_a_percentage_of_the_ongoing_need(conn):
    result = jm.project(conn, base_year=2026, end_year=2030, opening_cents=0)
    pct = result.coverage_pct(2028)
    assert 0 < pct < 200


# ------------------------------------------------------------ die Kaskade

_SAETZE = dict(satz_tagesgeld=0.0, satz_depot=0.07,
               vorab_satz=0.0253 * 0.7, steuer_quote=0.7 * 0.26375)


def test_the_cascade_fills_the_buffer_before_the_depot():
    k = jm.kaskade(10_000_00, 0, 0, sparrate=30_000_00, grenze=35_000_00, **_SAETZE)
    assert (k["tagesgeld"], k["depot"], k["ins_depot"]) == (35_000_00, 5_000_00, 5_000_00)


def test_below_the_buffer_nothing_moves_and_the_depot_is_not_sold():
    k = jm.kaskade(10_000_00, 50_000_00, 0, sparrate=5_000_00, grenze=35_000_00, **_SAETZE)
    assert k["tagesgeld"] == 15_000_00
    assert k["ins_depot"] == 0


def test_a_negative_tagesgeld_is_not_quietly_covered_from_the_depot():
    """Fruher deckte sich ein Minus von selbst aus dem Depot.

    Beim Eigenheim leerte das unbemerkt das ganze Depot -- eine Entnahme von
    74.259, die niemand eingetragen hatte. Jetzt bleibt das Minus stehen und
    sagt damit, was es sagen soll: so ist der Plan nicht finanziert. Wer
    verkaufen will, schreibt eine Zeile auf ein Depotkonto.
    """
    k = jm.kaskade(10_000_00, 50_000_00, 0, sparrate=-30_000_00, grenze=35_000_00,
                   **_SAETZE)
    assert k["tagesgeld"] == -20_000_00
    assert k["ins_depot"] == 0
    assert k["depot"] > 50_000_00, "das Depot waechst weiter, statt geleert zu werden"


def test_an_entered_withdrawal_takes_from_the_depot_and_not_from_the_tagesgeld():
    """Die eingetragene Entnahme ist der Ersatz fuer die stille Deckung."""
    ohne = jm.kaskade(10_000_00, 50_000_00, 0, sparrate=0, grenze=35_000_00, **_SAETZE)
    mit = jm.kaskade(10_000_00, 50_000_00, 0, sparrate=0, grenze=35_000_00,
                     depot_abfluss=-20_000_00, **_SAETZE)
    assert mit["tagesgeld"] == ohne["tagesgeld"], "das Tagesgeld bleibt unberuehrt"
    # Dazu die Steuer auf den Gewinnanteil des Verkaufs, ebenfalls aus dem Depot.
    assert mit["verkaufsteuer"] > 0
    assert mit["depot"] == ohne["depot"] - 20_000_00 - mit["verkaufsteuer"]


def test_a_sale_is_taxed_on_its_gain_share_only():
    """Einstand 60.000, Wert 100.000: 40 % jedes verkauften Euros sind Gewinn."""
    satz = dict(satz_tagesgeld=0.0, satz_depot=0.0, vorab_satz=0.0, steuer_quote=0.26375)
    k = jm.kaskade(0, 100_000_00, 0, sparrate=0, grenze=0, depot_abfluss=-10_000_00,
                   einstand=60_000_00, **satz)
    assert k["verkaufsteuer"] == round(10_000_00 * 0.4 * 0.26375)
    assert k["einstand"] == 54_000_00, "der Verkauf nimmt seinen Anteil am Einstand mit"
    # Ohne Gewinn keine Steuer; ein Kauf legt seinen Betrag zum Einstand.
    ohne = jm.kaskade(0, 100_000_00, 0, sparrate=0, grenze=0, depot_abfluss=-10_000_00,
                      **satz)
    assert ohne["verkaufsteuer"] == 0
    kauf = jm.kaskade(50_000_00, 100_000_00, 0, sparrate=0, grenze=10_000_00,
                      einstand=60_000_00, **satz)
    assert kauf["einstand"] == 100_000_00


def test_the_advance_lump_sum_counts_towards_the_cost_basis():
    """Schon versteuerte Vorabpauschale wird beim Verkauf nicht noch einmal versteuert."""
    satz = dict(satz_tagesgeld=0.0, satz_depot=0.05, vorab_satz=0.02, steuer_quote=0.26375)
    k = jm.kaskade(0, 100_000_00, 0, sparrate=0, grenze=0, **satz)
    assert k["einstand"] == 100_000_00 + 2_000_00


def test_the_depot_pays_the_advance_lump_sum_tax_without_selling():
    """Die Vorabpauschale faellt jedes Jahr an, und nur auf das Depot."""
    k = jm.kaskade(0, 100_000_00, 0, sparrate=0, grenze=0, **_SAETZE)
    vorab = round(100_000_00 * _SAETZE["vorab_satz"])
    assert k["steuer"] == round(vorab * _SAETZE["steuer_quote"])
    assert k["depot"] == 100_000_00 + 7_000_00 - k["steuer"]
    assert k["rendite"] == 7_000_00 - k["steuer"]


def test_nothing_is_created_or_lost_between_the_pots():
    k = jm.kaskade(40_000_00, 20_000_00, 30_000_00, sparrate=12_345_00, grenze=45_000_00,
                   satz_tagesgeld=0.025, satz_depot=0.07,
                   vorab_satz=_SAETZE["vorab_satz"], steuer_quote=_SAETZE["steuer_quote"])
    vorher = 40_000_00 + 20_000_00 + 30_000_00
    assert k["tagesgeld"] + k["depot"] + k["policen"] == vorher + k["rendite"] + 12_345_00


def test_the_pots_add_up_and_the_tagesgeld_stays_under_its_limit(conn):
    result = jm.project(conn, base_year=2026, end_year=2035, puffer_cents=33_000_00,
                        toepfe={"tagesgeld": 50_000_00, "depot": 7_000_00, "policen": 36_000_00})
    vorher = None
    for y in result.years:
        assert y.tagesgeld_cents + y.depot_cents + y.policen_cents == y.closing_cents
        assert y.tagesgeld_cents <= y.puffer_grenze_cents
        if vorher is not None:
            assert y.opening_cents == vorher.closing_cents
        vorher = y


def test_the_pots_split_the_liquid_total():
    from finctl.forecast import ziele as zm

    bestand = {"balances": [
        {"kind": "giro", "cents": 1000}, {"kind": "tagesgeld", "cents": 5000},
        {"kind": "depot", "cents": 700}, {"kind": "krypto", "cents": None},
        {"kind": "rentenversicherung", "cents": 300}],
        "obligations": [{"cents": 2000}]}
    assert zm.toepfe(bestand) == {"tagesgeld": 6000, "depot": 700, "policen": 300}
    # Die Verbindlichkeit bleibt im Tagesgeld; sie geht mit dem Notgroschen ab.
    assert sum(zm.toepfe(bestand).values()) == zm.liquid_cents(bestand) + 2000


def test_a_goal_extrapolates_the_pots_of_what_is_ticked():
    from finctl.forecast import ziele as zm

    assert zm.toepfe_fuer_basis(("depot", "krypto")) == ("depot",)
    assert zm.toepfe_fuer_basis(("depot", "rentenversicherung")) == ("depot", "policen")
    assert zm.toepfe_fuer_basis(("dkb-giro",)) is None
    assert zm.toepfe_fuer_basis(None) is None


def test_the_policies_grow_like_funds_without_the_advance_tax():
    k = jm.kaskade(0, 0, 100_000_00, sparrate=0, grenze=0, **_SAETZE)
    assert k["policen"] == 107_000_00
    assert k["steuer"] == 0


def test_the_cascade_limit_is_the_tagesgeld_goal_typed_by_hand():
    from finctl.forecast import ziele as zm

    ziele = {"ziele": [{"id": "tagesgeld-puffer", "cents": 3_300_000},
                       {"id": "barista-fire", "cents": 1}]}
    assert zm.puffer_cents(ziele) == 3_300_000
    assert zm.puffer_cents({"ziele": []}) is None


def test_without_a_tagesgeld_goal_nothing_moves_to_the_depot(conn):
    result = jm.project(conn, base_year=2026, end_year=2032,
                        toepfe={"tagesgeld": 200_000_00, "depot": 0, "policen": 0})
    assert all(y.ins_depot_cents == 0 for y in result.years)
    assert all(y.depot_cents == 0 for y in result.years)


def test_the_depot_goal_now_gets_an_extrapolation():
    from fastapi.testclient import TestClient

    from finctl.web.server import app

    html = TestClient(app).get("/ziele").text
    if 'data-ziel="depot-2027"' not in html:
        pytest.skip("Depotziel auf der Seite geloescht")
    karte = html[html.index('data-ziel="depot-2027"'):]
    karte = karte[:karte.index("</details>")]
    assert "extrapoliert" in karte
    assert "daher ohne Extrapolation" not in karte


# --------------------------------------------------------- der Notgroschen

def test_every_goal_ignores_the_emergency_fund_except_the_fund_itself():
    from finctl.forecast import ziele as zm

    ziele = {"ziele": [
        {"id": "tagesgeld-puffer", "cents": 33_000_00, "basis": ["tagesgeld", "giro"]},
        {"id": "barista", "cents": 800_000_00},
        {"id": "depot", "cents": 30_000_00, "basis": ["depot"]}]}
    bestand = {"balances": [{"kind": "tagesgeld", "cents": 65_000_00},
                            {"kind": "depot", "cents": 7_000_00}], "obligations": []}
    p = {x.goal_id: x for x in zm.progress(ziele, bestand)}
    assert p["tagesgeld-puffer"].have_cents == 65_000_00
    assert p["barista"].have_cents == 72_000_00 - 33_000_00
    assert p["barista"].abzug_cents == 33_000_00
    assert (p["depot"].have_cents, p["depot"].abzug_cents) == (7_000_00, 0)


def test_a_thin_tagesgeld_gives_up_only_what_it_holds():
    from finctl.forecast import ziele as zm

    ziele = {"ziele": [{"id": "tagesgeld-puffer", "cents": 33_000_00},
                       {"id": "barista", "cents": 800_000_00}]}
    bestand = {"balances": [{"kind": "tagesgeld", "cents": 10_000_00},
                            {"kind": "depot", "cents": 50_000_00}], "obligations": []}
    barista = next(x for x in zm.progress(ziele, bestand) if x.goal_id == "barista")
    assert barista.abzug_cents == 10_000_00
    assert barista.have_cents == 50_000_00


def test_a_goal_is_measured_on_its_date_not_at_the_end_of_its_year():
    """Ein Ziel zum 01.01.2027 sieht das Ende von 2026 -- samt Kaufraten."""
    from datetime import date

    lauf = jm.Projection(years=[jm.Year(year=2026, opening_cents=10_000_00),
                                jm.Year(year=2027, opening_cents=40_000_00),
                                jm.Year(year=2028, opening_cents=50_000_00)])
    def wert(zeile):
        return zeile.closing_cents

    assert lauf.zum(date(2027, 1, 1), wert) == 10_000_00
    assert lauf.zum(date(2027, 7, 2), wert) == pytest.approx(25_000_00, rel=0.01)
    assert lauf.zum(date(2028, 1, 1), wert) == 40_000_00
    assert lauf.zum(date(2031, 1, 1), wert) == 50_000_00


def test_the_sisters_money_is_part_of_the_emergency_fund_and_leaves_with_it():
    """Geld, das im eigenen Konto liegt aber jemand anderem gehoert, zaehlt zum
    Notgroschen -- und geht mit ihm ab, nicht noch einmal obendrauf."""
    from finctl.forecast import ziele as zm

    ziele = {"ziele": [
        {"id": "tagesgeld-puffer", "cents": 33_000_00, "basis": ["tagesgeld", "giro"]},
        {"id": "alles", "cents": 800_000_00}]}
    bestand = {"balances": [{"kind": "tagesgeld", "cents": 82_000_00},
                            {"kind": "depot", "cents": 7_000_00}],
               "obligations": [{"cents": 17_000_00}]}
    p = {x.goal_id: x for x in zm.progress(ziele, bestand)}
    assert p["tagesgeld-puffer"].have_cents == 82_000_00
    assert p["alles"].have_cents == 89_000_00 - 33_000_00
    assert zm.toepfe(bestand)["tagesgeld"] == 82_000_00


def test_the_free_capital_leaves_out_the_buffer():
    zeile = jm.Year(year=2030, opening_cents=133_000_00, tagesgeld_cents=33_000_00,
                    depot_cents=100_000_00, puffer_grenze_cents=33_000_00)
    assert zeile.closing_cents == 133_000_00
    assert zeile.frei_cents == 100_000_00


def _mit_einer_zeile(tmp_path, konto: str):
    """Eine Klammer mit genau einer Einmalzahlung auf `konto`."""
    import yaml

    spec = {"szenarien": [{
        "id": "pytest-entnahme", "name": "Pytest Entnahme", "aktiv": True,
        "zeilen": [{"label": "Abhebung", "art": "betrag",
                    "amount_cents": -20_000_00, "frequenz": "einmalig",
                    "start": "2029-06", "kategorie": "wohnen/miete",
                    "konto": konto}]}]}
    pfad = tmp_path / f"szen_{konto}.yaml"
    pfad.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return pfad


def test_a_plan_line_on_a_depot_account_takes_from_the_depot(conn, tmp_path):
    """Das Konto einer Planzeile war in dieser Rechnung ohne jede Wirkung.

    Eine Zeile auf einem Depot minderte stillschweigend das Tagesgeld. Wer
    fuer den Hauskauf Anteile verkauft, belastet aber das Depot -- und genau
    das ist der Ersatz fuer die frueher automatische Deckung.
    """
    depot = [r[0] for r in conn.execute(
        "SELECT id FROM accounts WHERE active = 1 AND account_type = 'depot'")]
    assert depot, "kein Depotkonto in accounts.yaml"

    # Ein Puffer ausserhalb jeder Reichweite schaltet den Sweep ab. Sonst
    # raeumt er den entnommenen Betrag im selben Jahr aus dem Ueberschuss
    # wieder ins Depot und die Bewegung, um die es geht, ist unsichtbar.
    gemeinsam = dict(base_year=2026, end_year=2030,
                     toepfe={"tagesgeld": 100_000_00, "depot": 100_000_00},
                     puffer_cents=10_000_000_00)
    auf_depot = jm.project(conn, szenarien=_mit_einer_zeile(tmp_path, depot[0]),
                           **gemeinsam).year(2029)
    auf_giro = jm.project(conn, szenarien=_mit_einer_zeile(tmp_path, "dkb-giro"),
                          **gemeinsam).year(2029)

    # Weniger um die 20.000 und die Steuer auf ihren Gewinnanteil.
    steuer = -sum(p.cents for p in auf_depot.posten["rendite"]
                  if p.label == "Steuer auf Depotverkauf")
    assert 0 < steuer < 20_000_00 * 0.26375
    unterschied = auf_giro.depot_cents - auf_depot.depot_cents
    assert 20_000_00 + steuer <= unterschied < 20_000_00 + 2 * steuer, (unterschied, steuer)
    # Das Tagesgeld liegt um dieselben 20.000 hoeher -- und um ein paar Euro
    # mehr: das Geld ist dort nicht abgeflossen, also hat es das Jahr ueber
    # mitverzinst. Genau deshalb zaehlt eine Depotzeile nicht in den
    # gewichteten Abfluss, der nur die Tagesgeldzinsen betrifft.
    mehr = auf_depot.tagesgeld_cents - auf_giro.tagesgeld_cents
    assert 20_000_00 <= mehr < 21_000_00, mehr
    # Die Zeile steht im Rechenweg, nur in ihrem eigenen Block.
    entnahme = auf_depot.posten["depotentnahme"]
    assert [p.label for p in entnahme] == ["Pytest Entnahme: Abhebung"]
    # Die Herleitung sagt, WOHER das Geld kommt -- sonst stuende die Zeile im
    # Rechenweg wie jede andere Ausgabe (Konvention rechenweg-zu-jeder-prognose).
    assert f"vom Depot ({depot[0]})" in entnahme[0].herleitung
    assert entnahme[0].quelle == "plan"
    assert not any("Abhebung" in p.label for p in auf_depot.posten["sondereffekt"])
    assert not auf_giro.posten.get("depotentnahme")


def test_a_broker_account_is_not_a_depot(conn, tmp_path):
    """Aus Trade Republic wird laufend bezahlt -- ein stiller Umweg aufs
    Depot waere genau die unsichtbare Logik, die hier nicht sein soll."""
    broker = conn.execute(
        "SELECT id FROM accounts WHERE active = 1 AND account_type = 'broker' "
        "LIMIT 1").fetchone()
    if broker is None:
        pytest.skip("kein Brokerkonto konfiguriert")
    jahr = jm.project(conn, base_year=2026, end_year=2030,
                      szenarien=_mit_einer_zeile(tmp_path, broker[0]),
                      toepfe={"tagesgeld": 100_000_00, "depot": 100_000_00},
                      puffer_cents=33_000_00).year(2029)
    assert not jahr.posten.get("depotentnahme")


# ------------------------------------------------------- Gewinn im Depot

def test_the_stated_gain_lowers_the_cost_basis_of_the_depot(conn, monkeypatch):
    """"davon Gewinn" aus dem Monatsabschluss: Einstand ist Wert minus Gewinn.
    Ohne Angabe gilt der ganze Wert als Einstand."""
    gesehen = []
    echt = jm.kaskade

    def mitschreiben(*a, **k):
        gesehen.append(k.get("einstand"))
        return echt(*a, **k)

    monkeypatch.setattr(jm, "kaskade", mitschreiben)
    jm.project(conn, base_year=2026, end_year=2026,
               toepfe={"tagesgeld": 0, "depot": 100_000_00}, depot_gewinn_cents=30_000_00)
    jm.project(conn, base_year=2026, end_year=2026, toepfe={"tagesgeld": 0, "depot": 100_000_00})
    assert gesehen == [70_000_00, 100_000_00]


def test_only_a_depot_contributes_its_gain():
    from finctl.forecast import ziele as z

    bestand = {"balances": [
        {"kind": "depot", "cents": 50_000_00, "gewinn_cents": 10_000_00},
        {"kind": "krypto", "cents": 5_000_00, "gewinn_cents": -1_000_00},
        {"kind": "rentenversicherung", "cents": 20_000_00, "gewinn_cents": 9_000_00},
        {"kind": "depot", "cents": 10_000_00}]}
    assert z.toepfe(bestand)["depot"] == 65_000_00
    assert z.depot_gewinn(bestand) == 9_000_00
