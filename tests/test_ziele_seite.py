"""Ziele im Dashboard anlegen und loeschen.

Der Betrag wird eingetragen, nicht berechnet. Ein Ziel aus goals.yaml wird nur
ausgeblendet -- dort steht seine Herleitung, und die darf ein Klick nicht
loeschen.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finctl.web.server import app

client = TestClient(app)
PFAD = Path("config/ziele_custom.yaml")


def _gesichert(lauf) -> None:
    vorher = PFAD.read_text(encoding="utf-8") if PFAD.exists() else None
    try:
        lauf()
    finally:
        if vorher is None:
            PFAD.unlink(missing_ok=True)
        else:
            PFAD.write_text(vorher, encoding="utf-8")


def test_a_goal_can_be_added_and_removed_from_the_page():
    def lauf():
        r = client.post("/api/ziel", json={
            "neu": True, "name": "Pytest Hebel", "cents": 12_345_600,
            "stichtag": "2035-12-31", "basis": ["depot"], "notiz": "eingetragen"})
        assert r.status_code == 200, r.text
        html = client.get("/ziele").text
        assert "Pytest Hebel" in html and "123.456,00" in html
        assert 'id="z-cents-pytest-hebel"' in html

        doppelt = client.post("/api/ziel", json={"neu": True, "name": "Pytest Hebel",
                                                 "cents": 100})
        assert doppelt.status_code == 400

        assert client.post("/api/ziel", json={"id": "pytest-hebel",
                                              "loeschen": True}).status_code == 200
        assert "Pytest Hebel" not in client.get("/ziele").text
    _gesichert(lauf)


def _erstes_basisziel() -> str:
    """Irgendein Ziel aus goals.yaml -- welche dort stehen, aendert sich."""
    import yaml

    spec = yaml.safe_load(Path("config/goals.yaml").read_text(encoding="utf-8")) or {}
    ziele = [g["id"] for g in (spec.get("ziele") or []) if g.get("id")]
    assert ziele, "goals.yaml ohne Ziel"
    return ziele[0]


def test_a_base_goal_is_hidden_and_goals_yaml_stays_untouched():
    def lauf():
        gid = _erstes_basisziel()
        basis = Path("config/goals.yaml").read_text(encoding="utf-8")
        assert client.post("/api/ziel", json={"id": gid, "loeschen": True}).status_code == 200
        assert Path("config/goals.yaml").read_text(encoding="utf-8") == basis
        assert f'id="z-cents-{gid}"' not in client.get("/ziele").text
    _gesichert(lauf)


def test_a_new_goal_needs_a_name_an_amount_and_a_real_date():
    def lauf():
        vorher = PFAD.read_text(encoding="utf-8") if PFAD.exists() else None
        for body in ({"neu": True, "cents": 100},
                     {"neu": True, "name": "Pytest ohne Betrag"},
                     {"neu": True, "name": "Pytest Datum", "cents": 100,
                      "stichtag": "2035-13-01"}):
            assert client.post("/api/ziel", json=body).status_code == 400, body
        nachher = PFAD.read_text(encoding="utf-8") if PFAD.exists() else None
        assert nachher == vorher, "eine abgelehnte Eingabe schreibt nichts"
    _gesichert(lauf)


def test_a_dragged_order_is_kept_and_survives_saving_a_goal():
    import re

    def lauf():
        # Ziehen braucht zwei Ziele. Wie viele konfiguriert sind, entscheidet
        # die Seite -- bis auf den Puffer wird jedes dort angelegt.
        if len(re.findall(r'data-ziel="([^"]+)"', client.get("/ziele").text)) < 2:
            r = client.post("/api/ziel", json={"neu": True, "name": "Pytest Zweitziel",
                                               "cents": 100})
            assert r.status_code == 200, r.text
        vorher = re.findall(r'data-ziel="([^"]+)"', client.get("/ziele").text)
        assert len(vorher) >= 2
        neu = list(reversed(vorher))
        assert client.post("/api/ziele/reihenfolge", json={"ids": neu}).status_code == 200
        assert re.findall(r'data-ziel="([^"]+)"', client.get("/ziele").text) == neu

        # Ein gespeichertes Ziel wirft die gezogene Reihenfolge nicht weg.
        # Die Rentenluecke ist gerechnet und hat nichts zu speichern.
        bearbeitbar = next(z for z in neu if z != "rentenluecke")
        assert client.post("/api/ziel", json={"id": bearbeitbar,
                                              "notiz": "pytest"}).status_code == 200
        assert re.findall(r'data-ziel="([^"]+)"', client.get("/ziele").text) == neu

        # Ein spaeter angelegtes Ziel kommt ans Ende, statt die Ordnung zu stoeren.
        client.post("/api/ziel", json={"neu": True, "name": "Pytest Spaet", "cents": 100})
        danach = re.findall(r'data-ziel="([^"]+)"', client.get("/ziele").text)
        assert danach[:len(neu)] == neu and danach[-1] == "pytest-spaet"
    _gesichert(lauf)


def test_a_goal_over_all_liquid_assets_says_that_the_emergency_fund_is_left_out():
    import re

    def lauf():
        client.post("/api/ziel", json={"neu": True, "name": "Pytest Alles",
                                       "cents": 1_000_000_00})
        html = client.get("/ziele").text
        karte = html[html.index('data-ziel="pytest-alles"'):]
        karte = karte[:karte.index("</details>")]
        assert "ohne Notgroschen" in karte
        assert re.search(r"ohne Notgroschen [\d.]+,\d\d", karte)
    _gesichert(lauf)


def test_a_goal_can_be_renamed_without_changing_its_identity():
    """Der Name war nur Ueberschrift: angelegt hiess getauft.

    Die Kennung bleibt, was sie beim Anlegen wurde -- an ihr haengen das
    Overlay, die gezogene Reihenfolge und die Stellen, die ein Ziel im Code
    suchen. Ein Umbenennen, das die Kennung mitzieht, waere ein Loeschen mit
    anschliessendem Neuanlegen.
    """
    def lauf():
        r = client.post("/api/ziel", json={"neu": True, "name": "Pytest Taufe",
                                           "cents": 500_00})
        assert r.status_code == 200, r.text
        gid = r.json()["id"]

        html = client.get("/ziele").text
        assert f'id="z-name-{gid}"' in html, "kein Namensfeld an der Karte"

        assert client.post("/api/ziel", json={"id": gid,
                                              "name": "Pytest Umbenannt"}).status_code == 200
        html = client.get("/ziele").text
        assert "Pytest Umbenannt" in html and "Pytest Taufe" not in html
        assert f'data-ziel="{gid}"' in html, "die Kennung darf sich nicht aendern"

        # Leer laesst den Namen stehen, statt ein Ziel ohne Bezeichnung zu hinterlassen.
        assert client.post("/api/ziel", json={"id": gid, "name": "  ",
                                              "notiz": "x"}).status_code == 200
        assert "Pytest Umbenannt" in client.get("/ziele").text
    _gesichert(lauf)


def test_the_pension_gap_is_a_goal_even_before_it_can_be_computed(monkeypatch):
    """Ohne Geburtsdatum laesst sie sich nicht rechnen -- die Seite zeigt sie
    trotzdem und sagt, was fehlt."""
    from finctl.forecast import rentenluecke as rl

    monkeypatch.setattr(rl, "rechnen", lambda lauf: None)
    html = client.get("/ziele").text
    karte = html.split('data-ziel="rentenluecke"')[1].split("</div>\n</div>")[0]
    assert "Rentenlücke" in karte and 'href="/einrichtung#person"' in karte
