"""Der erste Start: drei Fragen, und keine davon mit geratener Antwort.

Der Grund steht in `docs/Generalization_plan.md`, Phase 3: Wer dieses Werkzeug
uebernimmt, musste bisher wissen, dass es `FINCTL_DATEN` gibt, wo
`backup.yaml` liegt und wie ein Kontoeintrag aussieht. Drei Dinge, die
nirgends standen, und jedes einzelne haelt die App an.

Alle Pfade hier sind erfunden und liegen unter `tmp_path`. Der Zeiger auf den
Datenordner haengt am Heimatverzeichnis, deshalb bekommt jeder Test, der ihn
anfasst, ein eigenes -- sonst traegt ein Testlauf dem Entwickler seinen
echten Datenordner um.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finctl.web.routen import einrichtung as ein
from finctl.web.server import app

client = TestClient(app)


@pytest.fixture
def eigenes_heim(tmp_path, monkeypatch):
    """Ein Heimatverzeichnis nur fuer diesen Test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return tmp_path


# ------------------------------------------------------------------- Seite

def test_the_page_lists_exactly_the_three_questions():
    html = client.get("/einrichtung").text
    for stueck in ("Wo die Daten liegen", "Wohin gesichert wird",
                   "Welche Konten es gibt"):
        assert stueck in html, stueck


def test_the_page_carries_the_same_masks_as_the_pages_that_own_them():
    """Keine zweite Maske, sondern dieselbe.

    Das Sicherungsziel gehoert dem Sichern-Knopf, das Kontoformular dem
    Kontenregister. Zwei Kopien liefen nach dem ersten Umbau auseinander, und
    die Einrichtung waere die Kopie, die niemand nachzieht.
    """
    html = client.get("/einrichtung").text
    assert 'id="ziel-ordner"' in html and 'id="n-id"' in html

    vorlagen = Path("finctl/web/templates")
    for feld, teil in (('id="ziel-ordner"', "_sicherungsziel.html"),
                       ('id="n-id"', "_neues_konto.html")):
        traeger = [p.name for p in sorted(vorlagen.glob("*.html"))
                   if feld in p.read_text(encoding="utf-8")]
        assert traeger == [teil], f"{feld} steht in {traeger}"


def _offene(html: str) -> list[str]:
    """Die aufgeklappten Schrittbereiche, in der Reihenfolge der Seite."""
    import re

    return [m.group(0) for m in re.finditer(r'<details class="klapp"[^>]*>', html)
            if " open" in m.group(0)]


def _stand(**fertig) -> list[dict]:
    """Ein erfundener Einrichtungsstand.

    Damit der leere Fall pruefbar ist. Auf dem Rechner des Entwicklers ist
    alles eingerichtet -- ein Test, der nur den fertigen Zustand sieht, prueft
    ausgerechnet das nicht, wofuer die Seite gebaut wurde.
    """
    return [{"nr": i, "id": kennung, "titel": kennung, "fertig": fertig[kennung],
             "stand": "erfunden"}
            for i, kennung in enumerate(("datenordner", "sicherung", "konten"), 1)]


def test_the_first_unfinished_step_is_the_one_that_is_open(monkeypatch):
    """Wer die Seite oeffnet, soll nicht suchen, wo er weitermacht."""
    monkeypatch.setattr(ein, "schritte", lambda: _stand(
        datenordner=True, sicherung=False, konten=False))
    html = client.get("/einrichtung").text

    # Offen ist genau einer -- und zwar der, der zum offenen Schritt gehoert.
    # Schritt zwei kommt aus einer Teilvorlage; die muss dafuer einen Schalter
    # haben, sonst liesse sich ausgerechnet er nie aufklappen.
    assert len(_offene(html)) == 1
    auf = html.index(_offene(html)[0])
    assert auf < html.index('id="ziel-ordner"') + 200
    assert auf > html.index('id="d-ordner"')


def test_a_finished_setup_opens_nothing(monkeypatch):
    monkeypatch.setattr(ein, "schritte", lambda: _stand(
        datenordner=True, sicherung=True, konten=True))
    html = client.get("/einrichtung").text
    assert _offene(html) == []
    assert html.count('<span class="pos">ja</span>') == 3


def test_each_step_can_be_the_open_one(monkeypatch):
    """Alle drei, nicht nur die beiden bequemen.

    Der mittlere Schritt kommt aus einer Teilvorlage, die auch neben dem
    Sichern-Knopf steht. Ohne eigenen Schalter blieb er dort zu -- und der
    Fehler waere niemandem aufgefallen, weil die anderen beiden aufgingen.
    """
    for offen_id, feld in (("datenordner", 'id="d-ordner"'),
                           ("sicherung", 'id="ziel-ordner"'),
                           ("konten", 'id="n-id"')):
        fertig = {k: k != offen_id for k in ("datenordner", "sicherung", "konten")}
        # Nur die VOR ihm gelten als fertig, sonst waere er nicht der erste offene.
        for k in ("konten", "sicherung", "datenordner"):
            if k == offen_id:
                break
            fertig[k] = True
        monkeypatch.setattr(ein, "schritte", lambda f=fertig: _stand(**f))
        html = client.get("/einrichtung").text
        assert len(_offene(html)) == 1, offen_id
        assert html.index(_offene(html)[0]) < html.index(feld), offen_id


# ------------------------------------------------------------------- Stand

def test_done_is_read_from_the_data_not_from_a_checkbox():
    """Wie auf /monatsabschluss: kein Haken zum Anklicken.

    Geprueft wird gegen dieselbe Quelle, die die Seite benutzt -- steht ein
    Sicherungsziel in der Konfiguration, ist Schritt zwei fertig, und sonst
    nicht. Eine feste Erwartung waere hier falsch: der Test laeuft auf einem
    eingerichteten und auf einem leeren Bestand.
    """
    from finctl import ops

    stand = {s["id"]: s for s in ein.schritte()}
    assert set(stand) == {"datenordner", "sicherung", "konten", "person", "module"}
    # Die Modulwahl ist keine Pflicht: eine gueltige Auswahl gibt es immer.
    assert stand["module"]["fertig"]

    try:
        ops.backup_settings()
        hat_ziel = True
    except ops.KeinBackupZielError:
        hat_ziel = False
    assert stand["sicherung"]["fertig"] is hat_ziel

    # Und der Stand ist nie leer: eine Zeile ohne Text sagt nichts.
    for s in stand.values():
        assert s["stand"].strip()


def test_the_monthly_close_points_here_only_while_something_is_missing(monkeypatch):
    """Der Hinweis erscheint, wenn etwas fehlt -- und verschwindet danach.

    Beide Richtungen, weil ein Hinweis, der bleibt, nach dem dritten Monat
    ueberlesen wird, und einer, der nie kommt, niemandem hilft. Dafuer wird
    `offen()` erfunden: Auf einem eingerichteten Bestand waere sonst nur eine
    der beiden Richtungen zu sehen.
    """
    from finctl.web.routen import auswertung as _aus

    monkeypatch.setattr(_aus._einrichtung, "offen", lambda: 2)
    html = client.get("/monatsabschluss").text
    assert "Einrichtung ist noch nicht fertig" in html
    assert 'href="/einrichtung"' in html and "2 von 3" in html

    monkeypatch.setattr(_aus._einrichtung, "offen", lambda: 0)
    assert "Einrichtung ist noch nicht fertig" not in \
        client.get("/monatsabschluss").text


def test_a_saved_step_makes_the_page_read_itself_again():
    """Gespeichert, und die Uebersicht widerspricht -- das darf nicht sein.

    Die Zeile oben steht serverseitig auf "offen" und bleibt es, bis die Seite
    neu gelesen wird. Beide Masken melden deshalb ueber denselben Haken, dass
    ein Schritt fertig ist; das Kontoformular laedt ohnehin neu.
    """
    seite = Path("finctl/web/templates/einrichtung.html").read_text(encoding="utf-8")
    ziel = Path("finctl/web/templates/_sicherungsziel.html").read_text(encoding="utf-8")

    assert "function schrittFertig()" in seite
    assert "location.reload()" in seite
    # Die geteilte Maske kennt die Einrichtung nicht -- sie ruft den Haken nur,
    # wenn es ihn gibt. Neben dem Sichern-Knopf gibt es ihn nicht.
    assert "typeof schrittFertig === 'function'" in ziel
    assert "location.reload" not in ziel


# -------------------------------------------------------------- Datenordner

def test_the_data_folder_endpoint_says_which_rule_won(eigenes_heim):
    antwort = client.get("/api/datenordner").json()
    assert antwort["ordner"] and antwort["grund"]
    assert antwort["zeiger"].endswith("ort.txt")


def test_an_empty_folder_is_refused_before_anything_is_written(eigenes_heim):
    from finctl import pfade as _p

    antwort = client.post("/api/datenordner", json={"ordner": "  "})
    assert antwort.status_code == 400
    assert not _p.zeiger().exists()


def test_a_folder_that_cannot_be_written_is_refused(eigenes_heim, tmp_path):
    """Geprueft wird durch SCHREIBEN, nicht durch Nachdenken ueber Rechte.

    Ein Datenordner, in den nichts geschrieben werden kann, ist schlimmer als
    ein fehlender: Die Einrichtung saehe fertig aus, und das Hauptbuch
    entstuende nie.
    """
    from finctl import pfade as _p

    gesperrt = tmp_path / "gesperrt"
    gesperrt.mkdir()
    gesperrt.chmod(0o500)
    try:
        antwort = client.post("/api/datenordner", json={"ordner": str(gesperrt)})
        if antwort.status_code == 200:          # als root ist alles schreibbar
            pytest.skip("Schreibrechte lassen sich hier nicht entziehen")
        assert antwort.status_code == 400
        assert not _p.zeiger().exists()
    finally:
        gesperrt.chmod(0o700)


def test_registering_a_folder_writes_the_pointer_and_moves_nothing(eigenes_heim,
                                                                   tmp_path):
    from finctl import pfade as _p

    alt = tmp_path / "alt" / "config"
    alt.mkdir(parents=True)
    (alt / "rules.yaml").write_text("rules: []\n", encoding="utf-8")

    neu = tmp_path / "neuer ordner"
    neu.mkdir()
    antwort = client.post("/api/datenordner", json={"ordner": str(neu)})
    assert antwort.status_code == 200, antwort.text

    inhalt = antwort.json()
    assert inhalt["ordner"] == str(neu)
    # Die laufende App wechselt ihr Hauptbuch nicht mitten im Betrieb, und die
    # Antwort sagt das, statt es den Nutzer merken zu lassen.
    assert "nächsten Start" in inhalt["hinweis"]

    assert _p.ort() == neu
    assert (alt / "rules.yaml").exists()        # nichts umgezogen
    assert not any(neu.iterdir())               # und nichts angelegt


def test_the_setup_says_what_to_replace_with_ones_own_data():
    """Wer das Werkzeug uebernimmt, sieht je Bereich: was, wo, welche Datei,
    welcher Stand. Ausgeschaltete Module fehlen in der Liste."""
    from finctl import module as _module

    zeilen = ein.eigene_angaben()
    assert zeilen and all(z["datei"].startswith("config/") and z["stand"] for z in zeilen)
    for z in zeilen:
        assert _module.seite_an(z["wo"].split("#")[0]), z["wo"]
    assert {"/einrichtung#konten", "/einrichtung#person"} <= {z["wo"] for z in zeilen}
