"""Der Rechenweg: jede Zahl der Jahresrechnung besteht aus ihren Posten.

Die Invariante ist die ganze Idee. Eine Erklaerung, die neben der Rechnung
herlaeuft, stimmt am ersten Tag; gebildet aus denselben Posten kann sie gar
nicht auseinanderlaufen.
"""

from __future__ import annotations

from datetime import date as dt_date
from pathlib import Path

import pytest

from finctl.forecast import abgleich as ag
from finctl.forecast import jahre as jm
from finctl.forecast.herkunft import QUELLEN, Posten, summe

DB = Path("data/finance.db")
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no ledger present")

_TOEPFE = {"tagesgeld": 80_000_00, "depot": 7_000_00, "policen": 36_000_00}


@pytest.fixture
def conn():
    from finctl.ledger.db import connect

    c = connect()
    yield c
    c.close()


@pytest.fixture
def lauf(conn):
    return jm.project(conn, end_year=2048, toepfe=_TOEPFE, puffer_cents=33_000_00)


def test_every_block_is_the_sum_of_its_posten(lauf):
    for y in lauf.years:
        for block, cents in y.blocks.items():
            assert summe(y.posten[block]) == cents, (y.year, block)


def test_returns_and_the_cascade_are_the_sum_of_their_posten(lauf):
    for y in lauf.years:
        assert summe(y.posten["rendite"]) == y.return_cents, y.year
        assert summe(y.posten["kaskade"]) == y.ins_depot_cents, y.year


def test_every_posten_says_where_it_comes_from(lauf):
    for y in lauf.years:
        for liste in y.posten.values():
            for p in liste:
                assert p.quelle in QUELLEN
                assert p.herleitung


def test_the_categories_add_up_to_the_measured_blocks(conn):
    f = ag.fenster(conn)
    bloecke = ag.measure(conn, f)
    kategorien = ag.measure_kategorien(conn, f)
    for block, cents in bloecke.items():
        assert sum(kategorien.get(block, {}).values()) == cents, block


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError):
        Posten(label="x", cents=1, quelle="geraten", herleitung="")


# ---------------------------------------------------------- das Basisjahr

_SAETZE = dict(satz_tagesgeld=0.03, satz_depot=0.06, vorab_satz=0.0,
               steuer_quote=0.0)


def test_the_base_year_earns_only_its_remaining_months():
    """Vier Restmonate verzinsen den Bestand vier Monate, nicht zwoelf."""
    voll = jm.kaskade(120_000_00, 60_000_00, 0, sparrate=0, grenze=2**62, **_SAETZE)
    rest = jm.kaskade(120_000_00, 60_000_00, 0, sparrate=0, grenze=2**62,
                      anteil=4 / 12, **_SAETZE)
    assert rest["zins_tagesgeld"] == round(120_000_00 * 4 / 12 * 0.03)
    assert rest["gewinn_depot"] == round(60_000_00 * 0.06 * 4 / 12)
    assert rest["rendite"] < voll["rendite"]


def test_a_one_off_payment_stops_earning_from_its_month():
    """60.000 im Oktober fehlen dem Tagesgeld drei Monate."""
    ohne = jm.kaskade(120_000_00, 0, 0, sparrate=0, grenze=2**62,
                      anteil=4 / 12, **_SAETZE)
    mit = jm.kaskade(120_000_00, 0, 0, sparrate=0, grenze=2**62, anteil=4 / 12,
                     abfluss_gewichtet=round(60_000_00 * 3 / 12), **_SAETZE)
    assert ohne["zins_tagesgeld"] - mit["zins_tagesgeld"] == round(60_000_00 * 3 / 12 * 0.03)


def test_the_projection_prorates_the_base_year_returns(conn):
    ende = ag.letzter_vollstaendiger_monat(conn)
    lauf = jm.project(conn, base_year=ende.year, end_year=ende.year + 1,
                      toepfe={"tagesgeld": 0, "depot": 100_000_00, "policen": 0},
                      puffer_cents=0)
    rest = 12 - ende.month
    depot = next(p for p in lauf.years[0].posten["rendite"] if p.label == "Depot")
    import finctl.assumptions as ann

    assert depot.cents == round(100_000_00 * ann.rendite_depot_pa() * rest / 12)


# ------------------------------------------------------ Teilzeit als Plan

def _teilzeitplan(anteil: float, start: str, ende: str | None = None):
    from finctl.forecast import szenarien as sz

    zeile = {"label": "halbe Stelle", "art": "teilzeit", "anteil": anteil,
             "start": start}
    if ende:
        zeile["ende"] = ende
    return sz.load({"szenarien": [{"id": "tz", "name": "Teilzeit", "aktiv": True,
                                   "zeilen": [zeile]}]})


def test_teilzeit_is_a_plan_that_cuts_the_salary_from_its_month():
    """Ein Wechsel im Juli laesst sechs Monate voll, sechs zum Anteil.

    Das Gehalt steht voll im Rechenweg, die Teilzeit daneben als Planposten
    mit ihrem Preis -- beide zusammen sind der Block.
    """
    from finctl.forecast import szenarien as sz

    zeilen = sz.teilzeitzeilen(_teilzeitplan(0.5, "2040-07"))
    assert jm._teilzeitposten(zeilen, 2039, 0, 500_000) == []
    [gemischt] = jm._teilzeitposten(zeilen, 2040, 0, 500_000)
    assert gemischt.cents == -250_000 * 6
    assert gemischt.quelle == "plan" and gemischt.verweis == "tz:0"
    [voll] = jm._teilzeitposten(zeilen, 2041, 0, 500_000)
    assert voll.cents == -250_000 * 12
    # Im Basisjahr zaehlen nur die Monate, die noch kommen.
    [rest] = jm._teilzeitposten(zeilen, 2040, 9, 500_000)
    assert rest.cents == -250_000 * 3


def test_a_later_teilzeit_line_replaces_an_earlier_one():
    from finctl.forecast import szenarien as sz

    geladen = sz.load({"szenarien": [{"id": "tz", "name": "Teilzeit", "aktiv": True,
        "zeilen": [{"label": "80 %", "art": "teilzeit", "anteil": 0.8, "start": "2040-01"},
                   {"label": "50 %", "art": "teilzeit", "anteil": 0.5, "start": "2042-01",
                    "ende": "2042-12"}]}]})
    zeilen = sz.teilzeitzeilen(geladen)
    assert sz.teilzeit_im_monat(zeilen, dt_date(2041, 5, 1))[2].anteil == 0.8
    assert sz.teilzeit_im_monat(zeilen, dt_date(2042, 5, 1))[2].anteil == 0.5
    # Nach ihrem Ende gilt wieder die fruehere.
    assert sz.teilzeit_im_monat(zeilen, dt_date(2043, 1, 1))[2].anteil == 0.8
    assert sz.teilzeit_im_monat(zeilen, dt_date(2039, 12, 1)) is None


def test_teilzeit_touches_neither_accounts_nor_bookings():
    """Nur Jahresrechnung: kein Betrag auf den Konten, keine Vorschlaege."""
    from finctl.forecast import szenarien as sz

    geladen = _teilzeitplan(0.7, "2027-01")
    assert sz.dated_amounts(geladen, dt_date(2030, 1, 1)) == []
    line = geladen[0].lines[0]
    assert sz.vorschlaege(object(), line, object()) == []
    assert sz.restwirkung_monatlich(line, {"cents": 0, "monate": 12}) == 0


def test_the_plan_api_refuses_a_malformed_teilzeit():
    from finctl.web.routen.planung import _teilzeitzeile

    assert _teilzeitzeile({"anteil": 0.7, "start": "2045"}, "x").status_code == 400
    assert _teilzeitzeile({"anteil": 1.7, "start": "2045-01"}, "x").status_code == 400
    assert _teilzeitzeile({"start": "2045-01"}, "x").status_code == 400
    assert _teilzeitzeile({"anteil": 0.7, "start": "2045-01"}, "x") == {
        "label": "x", "art": "teilzeit", "anteil": 0.7, "frequenz": "monatlich",
        "start": "2045-01"}


def test_the_settings_api_refuses_a_non_number():
    from finctl.web.server import _setting_pruefen

    assert _setting_pruefen("inflation_pa", "zwei") is not None
    assert _setting_pruefen("inflation_pa", 0.02) is None


def test_cash_in_and_out_add_up_to_the_saving_rate(lauf):
    for y in lauf.years:
        cf = y.cashflow()
        assert sum(cf.values()) == y.saving_cents, y.year
        assert cf["einmalig_rein"] >= 0 >= cf["einmalig_raus"]


def test_a_teilzeit_plan_does_not_count_as_a_summed_variant():
    """Die Warnung "mehr als ein Plan gleichzeitig an" gilt Varianten, deren
    Betraege sich addieren. Teilzeit addiert nichts."""
    assert not _teilzeitplan(0.7, "2045-01")[0].summiert
