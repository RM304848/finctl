"""Ein Konto anlegen, ohne eine YAML-Datei von Hand zu schreiben.

Das war der erste Schritt der App und der einzige, der nur im Texteditor
ging: kein Konto, kein Import, kein Hauptbuch, keine Warnung vor dem
Tiefpunkt. Wer die App neu aufsetzt, steht genau hier.

Testdaten sind erfunden.
"""

from __future__ import annotations

import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient

from finctl import konten
from finctl.web.server import app

BEISPIEL = {"id": "beispielbank", "display_name": "Beispielbank Giro",
            "institution": "Beispielbank", "account_type": "giro",
            "ingest_mode": "summary"}


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.executescript(open("finctl/ledger/schema.sql", encoding="utf-8").read())
    return c


@pytest.fixture
def konfig(tmp_path):
    """Eine Basisdatei mit einem Konto und einer Begruendung darin."""
    ordner = tmp_path / "config"
    ordner.mkdir()
    (ordner / konten.BASIS).write_text(
        "# Warum dieses Konto so gefuehrt wird.\n"
        + yaml.safe_dump({"accounts": [
            {"id": "vorhanden", "display_name": "Vorhanden", "institution": "X",
             "account_type": "giro", "ingest_mode": "summary"}]}),
        encoding="utf-8")
    return ordner


def test_a_new_account_reaches_both_the_file_and_the_table(db, konfig, tmp_path):
    konten.anlegen(db, dict(BEISPIEL), konfig, tmp_path / "statements")

    eigen = yaml.safe_load((konfig / konten.EIGEN).read_text(encoding="utf-8"))
    assert "beispielbank" in eigen["accounts"]
    zeile = db.execute("SELECT display_name, active FROM accounts "
                       "WHERE id = 'beispielbank'").fetchone()
    assert zeile == ("Beispielbank Giro", 1)


def test_the_hand_written_file_is_never_rewritten(db, konfig, tmp_path):
    """Die Basisdatei traegt die Begruendung. Ein Dashboard, das sie neu
    schreibt, loescht sie beim ersten Klick -- deshalb zwei Dateien."""
    vorher = (konfig / konten.BASIS).read_text(encoding="utf-8")
    konten.anlegen(db, dict(BEISPIEL), konfig, tmp_path / "statements")
    assert (konfig / konten.BASIS).read_text(encoding="utf-8") == vorher
    assert "# Warum dieses Konto" in vorher


def test_the_statement_folder_exists_right_away(db, konfig, tmp_path):
    """Ein Konto, dessen Ablage erst beim ersten Import entsteht, laesst den
    Nutzer raten, wohin die Datei soll."""
    ziel = tmp_path / "statements"
    konten.anlegen(db, dict(BEISPIEL), konfig, ziel)
    assert (ziel / "beispielbank").is_dir()


def test_a_named_folder_stays_inside_the_data_directory(tmp_path):
    """Ein Ordnername liegt im Datenordner und ist damit im Backup."""
    ordner = konten.auszugsordner({"id": "x", "statement_folder": "ablage"},
                                  tmp_path)
    assert ordner == tmp_path / "ablage"
    assert not konten.eigener_pfad({"id": "x", "statement_folder": "ablage"})


def test_a_full_path_is_taken_as_it_is_and_flagged(tmp_path):
    """Und wird als das gekennzeichnet, was es ist: ausserhalb der Sicherung."""
    konto = {"id": "x", "statement_folder": str(tmp_path / "woanders")}
    assert konten.auszugsordner(konto, tmp_path / "egal") == tmp_path / "woanders"
    assert konten.eigener_pfad(konto)


def test_the_folder_defaults_to_the_account_id(tmp_path):
    assert konten.auszugsordner({"id": "kennung"}, tmp_path) == tmp_path / "kennung"


@pytest.mark.parametrize("aenderung, wort", [
    ({"id": "Mit Großbuchstaben"}, "Kennung"),
    ({"id": "vorhanden"}, "vergeben"),
    ({"display_name": ""}, "Anzeigename"),
    ({"institution": ""}, "Institut"),
    ({"account_type": "sparstrumpf"}, "Art"),
    ({"ingest_mode": "parsed"}, "Parserprofil"),
])
def test_a_broken_account_is_refused_with_a_reason(db, konfig, tmp_path,
                                                   aenderung, wort):
    """Abgelehnt wird mit Begruendung, nicht mit einem roten Rand.

    Besonders das Parserprofil: ein geparstes Konto ohne Profil saehe
    eingerichtet aus und bliebe beim Import stumm.
    """
    with pytest.raises(ValueError, match=wort):
        konten.anlegen(db, {**BEISPIEL, **aenderung}, konfig,
                       tmp_path / "statements")
    assert not (konfig / konten.EIGEN).exists()


def test_the_overlay_wins_field_by_field(db, konfig, tmp_path):
    """Wie ueberall: die Basisdatei bleibt, die eigene Datei gewinnt."""
    (konfig / konten.EIGEN).write_text(yaml.safe_dump({"accounts": {
        "vorhanden": {"display_name": "Anders benannt"}}}), encoding="utf-8")
    [konto] = konten.laden(konfig)
    assert konto["display_name"] == "Anders benannt"
    assert konto["institution"] == "X"          # aus der Basisdatei


def test_a_removed_account_leaves_the_register(db, konfig):
    (konfig / konten.EIGEN).write_text(yaml.safe_dump({"accounts": {
        "vorhanden": {"entfernt": True}}}), encoding="utf-8")
    assert konten.laden(konfig) == []


def test_every_parser_profile_is_offered(tmp_path):
    """Die Liste kommt aus dem Ordner, nicht aus einer gepflegten Aufzaehlung
    -- sonst waere sie die Stelle, die man beim naechsten Profil vergisst."""
    from pathlib import Path

    from finctl.ingest.importer import PROFILE_MODULES

    ordner = Path("finctl/ingest/profiles")
    einzeln = {p.stem for p in ordner.glob("*.py")
               if not p.stem.startswith("_") and p.stem != "banken_csv"}
    angeboten = set(konten.profile())
    assert einzeln <= angeboten
    # Jede Bank aus der Sammlung einzeln, und jedes Angebot laedt auch.
    assert {"ing_csv", "dkb_csv", "sparkasse_csv"} <= angeboten
    assert angeboten <= set(PROFILE_MODULES)


# ---------------------------------------------------------------- Seite

client = TestClient(app)


def test_the_register_lives_in_the_setup():
    """Stammdaten, nicht Prognose: /konten beantwortet die Frage nach dem
    Tiefpunkt, die Einrichtung die danach, wie ein Auszug gelesen wird --
    dort, wo ein Konto angelegt wird."""
    r = client.get("/kontenregister", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/einrichtung#konten"
    html = client.get("/einrichtung").text
    assert '<details class="klapp" id="konten"' in html
    assert 'id="neu-konto"' in html and "Neues Konto" in html
    # Die Kennung, nicht der Anzeigename -- wie ueberall in dieser App.
    for art in konten.ARTEN:
        assert f'value="{art}"' in html
    # Und die Prognoseseite traegt das Formular nicht mehr.
    assert "neuesKonto" not in client.get("/konten").text


def test_the_register_shows_where_the_statements_live():
    html = client.get("/einrichtung").text
    assert "data/statements" in html


def test_the_endpoint_refuses_a_broken_account_without_writing():
    """Die Ablehnung braucht keinen Datenordner: sie kommt vor dem Schreiben."""
    antwort = client.post("/api/konto-neu",
                          json={"id": "", "display_name": "", "institution": "",
                                "account_type": "giro", "ingest_mode": "summary"})
    assert antwort.status_code == 400
    assert "Kennung" in antwort.json()["error"]
