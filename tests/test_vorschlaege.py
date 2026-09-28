"""Regelvorschlaege ueber Claude: hinaus geht nur Bereinigtes, herein nur Geprueftes.

Die App ruft kein Sprachmodell auf. Sie baut einen Auftrag zum Kopieren und
liest die eingefuegte Antwort -- und beides ist die Stelle, an der etwas
schiefgehen kann: eine IBAN im Auftrag, eine erfundene Kategorie in der
Antwort, eine Regel, die still das halbe Hauptbuch umordnet.
"""

from __future__ import annotations

import pytest

from finctl.rules import vorschlaege as vs


@pytest.fixture
def conn():
    from finctl.ledger.db import connect

    c = connect()
    yield c
    c.close()


def test_numbers_never_leave_the_machine():
    """IBAN, Betrag, Datum, Referenz fallen weg; ein Name mit einer Ziffer bleibt."""
    assert vs.bereinigt("Erfunden GmbH DE12500105170648489890 12,99 2026-01-15 Ref 99887") \
        == "Erfunden GmbH Ref"
    assert vs.bereinigt("O2 Rechnung 5,00") == "O2 Rechnung"
    assert vs.bereinigt(None) == ""
    assert len(vs.bereinigt("x" * 200)) == 60


def test_the_prompt_carries_categories_and_the_chosen_texts_only():
    kats = [{"id": "lebensmittel/supermarkt", "label": "Supermarkt"}]
    auftrag = vs.prompt(kats, ["Erfundener Laden"], {"lebensmittel/supermarkt": 3})
    assert "lebensmittel/supermarkt – Supermarkt – 3" in auftrag
    assert "- Erfundener Laden" in auftrag
    assert "Suchtext ; Kategorie" in auftrag


@pytest.mark.parametrize("antwort", [
    "Erfundener Laden ; lebensmittel/supermarkt",
    "- Erfundener Laden; lebensmittel/supermarkt",
    "| Erfundener Laden | lebensmittel/supermarkt |",
    "Erfundener Laden\tLebensmittel/Supermarkt",
    "„Erfundener Laden“ ; `lebensmittel/supermarkt`",
])
def test_the_answer_is_read_whatever_its_layout(antwort):
    assert vs.antwort_lesen(antwort) == [
        {"text": "Erfundener Laden", "kategorie": "lebensmittel/supermarkt"}]


def test_prose_headers_and_duplicates_are_skipped():
    antwort = ("Hier sind meine Vorschläge:\n\n| Suchtext | Kategorie |\n|---|---|\n"
               "| Laden A | konsum/sonstiges |\n| Laden A | konsum/sonstiges |\n")
    assert vs.antwort_lesen(antwort) == [{"text": "Laden A", "kategorie": "konsum/sonstiges"}]


def test_checking_flags_unknown_categories_and_conflicts(conn):
    """Eine erfundene Kategorie ist unbrauchbar; ein Text, der schon zugeordnete
    Buchungen einer anderen Kategorie trifft, widerspricht ihnen."""
    _tx, text, kat = conn.execute(
        "SELECT t.id, t.counterparty, s.mgmt_category_id FROM transactions t "
        "JOIN splits s ON s.transaction_id = t.id "
        "WHERE t.counterparty IS NOT NULL AND length(t.counterparty) >= 6 "
        "AND s.mgmt_category_id IS NOT NULL ORDER BY t.id LIMIT 1").fetchone()
    andere = conn.execute("SELECT id FROM mgmt_categories WHERE active = 1 AND id != ? "
                          "ORDER BY id LIMIT 1", (kat,)).fetchone()[0]
    [unbekannt, widerspricht] = vs.pruefen(conn, [
        {"text": "gibt es nicht", "kategorie": "erfunden/kategorie"},
        {"text": text, "kategorie": andere}])
    assert not unbekannt["bekannt"]
    assert widerspricht["bekannt"] and widerspricht["anders"] >= 1


def test_taking_over_writes_ordinary_text_rules(monkeypatch):
    from finctl.rules import regelwerk as rw

    gespeichert = []
    monkeypatch.setattr(rw, "laden", lambda *a, **k: [{"id": "laden-a"}])
    monkeypatch.setattr(rw, "speichern", lambda e, *a, **k: gespeichert.append(e))
    ids = vs.uebernehmen([{"text": "Laden A", "kategorie": "konsum/sonstiges"}])
    assert ids == ["laden-a-2"]
    assert gespeichert == [{"id": "laden-a-2", "name": "Laden A", "priority": vs.PRIORITAET,
                            "match": {"text": ["Laden A"]},
                            "set": {"mgmt": "konsum/sonstiges"}}]


def test_the_api_refuses_texts_the_page_did_not_offer():
    """Nur, was die Seite bereinigt angeboten hat, kommt in den Auftrag."""
    from fastapi.testclient import TestClient

    from finctl.web.server import app

    antwort = TestClient(app).post("/api/regeln/vorschlaege/prompt",
                                   json={"texte": ["DE12500105170648489890"]})
    assert antwort.status_code == 400


def test_the_api_refuses_to_take_over_an_unknown_category():
    from fastapi.testclient import TestClient

    from finctl.web.server import app

    antwort = TestClient(app).post("/api/regeln/vorschlaege/uebernehmen", json={
        "zeilen": [{"text": "Laden A", "kategorie": "erfunden/kategorie"}]})
    assert antwort.status_code == 400
