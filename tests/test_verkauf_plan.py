"""Ein Verkauf als Planzeile: schaltbar, mit Einmaleffekt, und der Kredit endet.

Termin und Preis auf /immobilien waren nicht schaltbar -- "2030 oder 2034"
liess sich fuer dasselbe Objekt nicht nebeneinanderstellen. In einer Klammer
geht das, und die Zeile gewinnt ueber die Objektdaten, damit es EINEN Verkauf
je Objekt gibt.

WELCHES OBJEKT, entscheidet die Konfiguration. Vorher stand hier eine
bestimmte Wohnung; geprueft wird aber nicht sie, sondern dass ein Verkauf den
Kredit beendet und der Erloes einmalig zufliesst.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml

from finctl.forecast import jahre as jm
from finctl.realestate.loan import lade_kredite

DB = Path("data/finance.db")
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no ledger present")


VERKAUFSMONAT = date(2030, 7, 1)


def _kredit_mit_objekt() -> dict:
    """Ein Kredit, der zum Verkaufstermin noch laeuft -- aus `loans.yaml`.

    Mit Restschuld, denn der Test prueft, dass sie vom Erloes abgeht. Ein
    laengst getilgter Kredit liesse ihn durchlaufen, ohne etwas zu zeigen.
    """
    for kredit in lade_kredite(jm.CONFIG_DIR):
        if not kredit.get("property_id"):
            continue
        rest, _ = jm.restschuld(kredit, VERKAUFSMONAT)
        if rest > 0:
            return kredit
    pytest.skip("kein laufender Kredit mit Objekt in loans.yaml", allow_module_level=True)
    raise AssertionError            # pragma: no cover -- skip springt vorher


KREDIT = _kredit_mit_objekt() if DB.exists() else {}
OBJEKT = str(KREDIT.get("property_id") or "")
KLAMMER = "Objekt verkaufen"


@pytest.fixture
def conn():
    """Eine Kopie im Speicher: der Vorrang-Test aendert Objektdaten."""
    from finctl.ledger.db import connect

    echt = connect()
    kopie = sqlite3.connect(":memory:")
    echt.backup(kopie)
    echt.close()
    kopie.row_factory = sqlite3.Row
    yield kopie
    kopie.close()


def _plan(tmp_path, ohne_plaene, *, aktiv=True, monat="2030-07", preis=200_000_00,
          kosten=8_000_00) -> Path:
    spec = yaml.safe_load(Path(ohne_plaene).read_text(encoding="utf-8"))
    spec["szenarien"].append({
        "id": "objekt-verkaufen", "name": KLAMMER, "aktiv": aktiv,
        "zeilen": [{"label": f"Verkauf {OBJEKT}", "art": "verkauf", "objekt": OBJEKT,
                    "amount_cents": preis, "verkaufskosten_cents": kosten,
                    "frequenz": "einmalig", "start": monat}]})
    pfad = tmp_path / "szenarien_verkauf.yaml"
    pfad.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return pfad


def _raten(lauf, jahr) -> list:
    """Die Raten genau dieses Kredits -- ueber seine eigene Kennung."""
    return [p for p in lauf.year(jahr).posten["kredit"]
            if p.verweis == str(KREDIT["id"])]


def test_the_loan_ends_in_the_month_of_the_sale(conn, tmp_path, ohne_plaene):
    plan = _plan(tmp_path, ohne_plaene)
    lauf = jm.project(conn, end_year=2032, szenarien=plan)
    [rate_2030] = _raten(lauf, 2030)
    assert rate_2030.herleitung.startswith("6 Raten")
    assert "durch Verkauf" in rate_2030.herleitung
    assert _raten(lauf, 2031) == []


def test_the_proceeds_are_price_minus_costs_minus_the_remaining_debt(conn, tmp_path, ohne_plaene):
    plan = _plan(tmp_path, ohne_plaene)
    [v] = jm.verkaeufe(conn, 2026, plan)
    rest, _ = jm.restschuld(KREDIT, VERKAUFSMONAT)
    assert v.netto_cents == 200_000_00 - 8_000_00 - rest
    lauf = jm.project(conn, end_year=2031, szenarien=plan)
    erloes = [p for p in lauf.year(2030).posten["sondereffekt"] if "Nettoerlös" in p.label]
    assert [p.cents for p in erloes] == [v.netto_cents]
    assert erloes[0].einmalig
    assert lauf.year(2030).cashflow()["einmalig_rein"] >= v.netto_cents


def test_a_switched_off_bracket_changes_nothing(conn, tmp_path, ohne_plaene):
    aus = jm.project(conn, end_year=2032, szenarien=_plan(tmp_path, ohne_plaene, aktiv=False))
    ohne = jm.project(conn, end_year=2032, szenarien=ohne_plaene)
    assert aus.final_cents == ohne.final_cents


def test_the_plan_line_beats_the_property_data(conn, tmp_path, ohne_plaene):
    conn.execute("UPDATE properties SET planned_sale_on='2034-01-01', "
                 "sale_price_cents=15000000 WHERE id=?", (OBJEKT,))
    [v] = jm.verkaeufe(conn, 2026, _plan(tmp_path, ohne_plaene))
    assert (v.ab, v.erloes_cents, v.klammer) == (VERKAUFSMONAT, 200_000_00, KLAMMER)
    [v] = jm.verkaeufe(conn, 2026, _plan(tmp_path, ohne_plaene, aktiv=False))
    assert (v.ab, v.erloes_cents, v.klammer) == (date(2034, 1, 1), 15_000_000, None)


def test_a_sale_line_does_not_reach_the_accounts(tmp_path, ohne_plaene):
    from finctl.forecast import szenarien as sz

    spec = yaml.safe_load(_plan(tmp_path, ohne_plaene).read_text(encoding="utf-8"))
    geladen = sz.load(spec)
    assert not [r for r in sz.dated_amounts(geladen, date(2040, 1, 1))
                if r["scenario_id"] == "objekt-verkaufen"]
