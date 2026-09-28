"""Geteilt: Anteile, Salden je Person und Projekt, der Haken "ausgeglichen"."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from finctl import geteilt as g
from finctl.web.server import app

client = TestClient(app)
DB = Path("data/finance.db")


def _projekt(personen=("anna", "ben")) -> g.Projekt:
    return g.Projekt("reise", "Reise", list(personen))


# ---------------------------------------------------------------- Anteile

def test_equal_is_the_default_and_loses_no_cent():
    assert g.anteile(10000, None, _projekt()) == {"ich": 3334, "anna": 3333, "ben": 3333}


def test_equal_without_me():
    t = {"art": "gleich", "ich": False, "personen": {"anna": 1, "ben": 1}}
    assert g.anteile(101, t, _projekt()) == {"ich": 0, "anna": 51, "ben": 50}


def test_equal_among_some_of_the_project():
    t = {"art": "gleich", "ich": True, "personen": {"anna": 1}}
    assert g.anteile(1000, t, _projekt()) == {"ich": 500, "anna": 500}


def test_weights_include_my_own():
    t = {"art": "gewicht", "ich": 2, "personen": {"anna": 1, "ben": 1}}
    assert g.anteile(10000, t, _projekt()) == {"ich": 5000, "anna": 2500, "ben": 2500}


def test_fixed_amounts_leave_the_rest_to_me():
    t = {"art": "betrag", "personen": {"anna": 3000}}
    assert g.anteile(10000, t, _projekt()) == {"anna": 3000, "ich": 7000}


def test_percentages_leave_the_rest_to_me_and_keep_every_cent():
    t = {"art": "prozent", "personen": {"anna": 50, "ben": 25}}
    ergebnis = g.anteile(1001, t, _projekt())
    assert sum(ergebnis.values()) == 1001
    assert ergebnis["anna"] == 501


def test_only_me():
    assert g.anteile(1234, {"art": "nur-ich"}, _projekt()) == {"ich": 1234}


def test_a_refund_is_shared_the_same_way_with_the_other_sign():
    assert g.anteile(-3000, None, _projekt()) == {"ich": -1000, "anna": -1000, "ben": -1000}


def test_a_project_without_people_only_collects_costs():
    assert g.anteile(5000, None, _projekt(personen=())) == {"ich": 5000}


@pytest.mark.parametrize(("teilung", "meldung"), [
    ({"art": "betrag", "personen": {"anna": 20000}}, "höher als die Buchung"),
    ({"art": "prozent", "personen": {"anna": 80, "ben": 30}}, "mehr als 100"),
    ({"art": "gleich", "personen": {"carla": 1}}, "Nicht im Budgettopf"),
    ({"art": "gewicht", "personen": {"anna": -1}}, "Negative"),
    ({"art": "raten"}, "Unbekannte Aufteilung"),
])
def test_a_split_that_cannot_be_right_is_refused(teilung, meldung):
    with pytest.raises(ValueError, match=meldung):
        g.anteile(10000, teilung, _projekt())


# ----------------------------------------------------------------- Salden

def _zeile(hash_, cents, seq=0, datum="2026-08-01"):
    return {"dedup_hash": hash_, "seq": seq, "teile": 1, "booking_date": datum,
            "account_id": "giro", "raw_text": "Restaurant", "mgmt_category_id": None,
            "amount_cents": cents}


def _daten():
    d = g.Daten(personen={"anna": "Anna Beispiel", "ben": "Ben Muster"})
    d.projekte["reise"] = _projekt()
    return d


def test_the_balance_is_per_person_and_project():
    d = _daten()
    g.zuordnen(d, [("a", 0), ("b", 0)], "reise")
    g.teilung_setzen(d, ("b", 0), {"art": "betrag", "personen": {"anna": 2000}}, 6000)
    je = g.posten(d, [_zeile("a", -9000), _zeile("b", -6000), _zeile("c", -500)])
    s = {x.person: x for x in g.salden(d, je)}
    assert s["anna"].offen_cents == 3000 + 2000
    assert s["ben"].offen_cents == 3000


def test_a_tick_remembers_the_amount_and_a_later_expense_opens_only_the_difference():
    d = _daten()
    g.zuordnen(d, [("a", 0)], "reise")
    je = g.posten(d, [_zeile("a", -9000)])
    g.ausgleichen(d, "reise", "anna", True, 3000, date(2026, 9, 1))
    assert {x.person: x.offen_cents for x in g.salden(d, je)}["anna"] == 0

    g.zuordnen(d, [("b", 0)], "reise")
    je = g.posten(d, [_zeile("a", -9000), _zeile("b", -600)])
    anna = next(x for x in g.salden(d, je) if x.person == "anna")
    assert (anna.offen_cents, anna.seit_ausgleich_cents, anna.ausgeglichen_am) == (
        200, 200, "2026-09-01")


def test_a_project_without_people_has_no_balances():
    d = _daten()
    d.projekte["umbau"] = g.Projekt("umbau", "Umbau", [])
    g.zuordnen(d, [("a", 0)], "umbau")
    assert not [s for s in g.salden(d, g.posten(d, [_zeile("a", -9000)]))
                if s.projekt == "umbau"]


def test_only_the_assigned_part_of_a_split_purchase_counts():
    d = _daten()
    g.zuordnen(d, [("einkauf", 1)], "reise")
    je = g.posten(d, [_zeile("einkauf", -6000, seq=0), _zeile("einkauf", -3000, seq=1)])
    assert [x.cents for x in je["reise"]] == [-3000]


def test_a_project_without_people_refuses_a_split():
    d = _daten()
    d.projekte["umbau"] = g.Projekt("umbau", "Umbau", [])
    g.zuordnen(d, [("a", 0)], "umbau")
    with pytest.raises(ValueError, match="ohne Personen"):
        g.teilung_setzen(d, ("a", 0), {"art": "nur-ich"}, 900)


def test_moving_to_another_project_drops_a_split_that_named_old_people():
    d = _daten()
    d.projekte["andere"] = g.Projekt("andere", "Andere", ["ben"])
    g.zuordnen(d, [("a", 0)], "reise")
    g.teilung_setzen(d, ("a", 0), {"art": "gewicht", "ich": 1, "personen": {"anna": 2}}, 900)
    g.zuordnen(d, [("a", 0)], "andere")
    assert d.buchungen[("a", 0)].teilung is None


def test_someone_who_carries_a_custom_split_cannot_be_removed():
    d = _daten()
    g.zuordnen(d, [("a", 0)], "reise")
    g.teilung_setzen(d, ("a", 0), {"art": "betrag", "personen": {"anna": 100}}, 900)
    with pytest.raises(ValueError, match="eigene Teilung"):
        g.projekt_setzen(d, "reise", "Reise", ["ben"])


def test_the_file_survives_a_round_trip(tmp_path):
    d = _daten()
    g.zuordnen(d, [("a", 2)], "reise")
    g.teilung_setzen(d, ("a", 2), {"art": "prozent", "personen": {"anna": 40}}, 1000)
    g.ausgleichen(d, "reise", "ben", True, 300, date(2026, 9, 1))
    g.schreiben(d, tmp_path / "g.yaml")
    wieder = g.laden(tmp_path / "g.yaml")
    assert wieder.personen == d.personen
    assert wieder.buchungen == d.buchungen
    assert wieder.projekte["reise"].ausgeglichen == {"ben": {"am": "2026-09-01", "cents": 300}}


# -------------------------------------------------------------------- Web

pytestmark_web = pytest.mark.skipif(not DB.exists(), reason="no ledger present")


@pytest.fixture
def datei(tmp_path, monkeypatch):
    pfad = tmp_path / "geteilt_custom.yaml"
    monkeypatch.setattr(g, "PFAD", pfad)
    return pfad


def _eine_ausgabe():
    db = sqlite3.connect(DB)
    row = db.execute(
        "SELECT t.dedup_hash, s.seq, s.amount_cents FROM splits s "
        "JOIN transactions t ON t.id = s.transaction_id "
        "WHERE s.amount_cents < -1000 ORDER BY t.booking_date DESC LIMIT 1").fetchone()
    db.close()
    return row


@pytestmark_web
def test_the_pages_open_without_a_file(datei):
    for pfad in ("/projekte", "/salden", "/geteilt/buchungen"):
        assert client.get(pfad).status_code == 200, pfad
    assert not datei.exists()


@pytestmark_web
def test_create_assign_split_and_see_it_in_the_balance(datei):
    hash_, seq, cents = _eine_ausgabe()
    anna = client.post("/api/geteilt/person", json={"name": "Anna Beispiel"}).json()["id"]
    projekt = client.post("/api/geteilt/projekt",
                          json={"name": "Testreise", "personen": [anna]}).json()["id"]
    assert client.post("/api/geteilt/zuordnen",
                       json={"teile": [[hash_, seq]], "projekt": projekt}).status_code == 200

    vorschau = client.post("/api/geteilt/teilung", json={
        "buchung": hash_, "teil": seq, "vorschau": True,
        "teilung": {"art": "gewicht", "ich": 1, "personen": {anna: 3}}}).json()
    assert vorschau["anteile"][anna] == round(-cents * 3 / 4)
    assert "teilung" not in yaml.safe_load(datei.read_text(encoding="utf-8"))["buchungen"][0]

    client.post("/api/geteilt/teilung", json={
        "buchung": hash_, "teil": seq,
        "teilung": {"art": "gewicht", "ich": 1, "personen": {anna: 3}}})
    seite = client.get("/salden").text
    assert "Anna Beispiel" in seite and "Testreise" in seite


@pytestmark_web
def test_the_assignment_table_shows_the_category_but_offers_no_field_for_it(datei):
    hash_, seq, _ = _eine_ausgabe()
    seite = client.get("/geteilt/buchungen", params={"von": "2000-01-01"}).text
    assert f'data-buchung="{hash_}" data-teil="{seq}"' in seite
    assert 'class="catsel"' not in seite and 'id="cat-' not in seite


@pytestmark_web
def test_bulk_assignment_writes_every_marked_row(datei):
    projekt = client.post("/api/geteilt/projekt", json={"name": "Umbau"}).json()["id"]
    db = sqlite3.connect(DB)
    teile = [list(r) for r in db.execute(
        "SELECT t.dedup_hash, s.seq FROM splits s JOIN transactions t "
        "ON t.id = s.transaction_id WHERE s.amount_cents < 0 LIMIT 3")]
    db.close()
    client.post("/api/geteilt/zuordnen", json={"teile": teile, "projekt": projekt})
    geschrieben = yaml.safe_load(datei.read_text(encoding="utf-8"))["buchungen"]
    assert len(geschrieben) == 3


@pytestmark_web
def test_a_refused_split_answers_in_german_and_writes_nothing(datei):
    hash_, seq, _ = _eine_ausgabe()
    anna = client.post("/api/geteilt/person", json={"name": "Anna Beispiel"}).json()["id"]
    projekt = client.post("/api/geteilt/projekt",
                          json={"name": "Reise", "personen": [anna]}).json()["id"]
    client.post("/api/geteilt/zuordnen", json={"teile": [[hash_, seq]], "projekt": projekt})
    vorher = datei.read_text(encoding="utf-8")
    antwort = client.post("/api/geteilt/teilung", json={
        "buchung": hash_, "teil": seq, "teilung": {"art": "prozent", "personen": {"x": 10}}})
    assert antwort.status_code == 400
    assert "Nicht im Budgettopf" in antwort.json()["error"]
    assert datei.read_text(encoding="utf-8") == vorher


@pytestmark_web
def test_the_navigation_has_geteilt_and_no_tags():
    kopf = client.get("/projekte").text
    kopf = kopf[kopf.index("<header>"):kopf.index("</header>")]
    assert ">Geteilt<" in kopf
    for ziel in ("/projekte", "/salden", "/geteilt/buchungen"):
        assert f'href="{ziel}"' in kopf
    assert 'href="/tags"' not in kopf
    assert client.get("/tags").status_code == 404


def test_old_tags_in_the_overrides_file_do_not_break_the_replay(tmp_path):
    from finctl.rules.categorize import replay_overrides

    datei = tmp_path / "overrides.yaml"
    datei.write_text(yaml.safe_dump({"overrides": [
        {"dedup_hash": "gibt-es-nicht", "parts": [], "tags": ["alte-reise"]}]}),
        encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE transactions (id INTEGER, dedup_hash TEXT)")
    assert replay_overrides(conn, datei) == 0
