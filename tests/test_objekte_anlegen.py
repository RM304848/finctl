"""Objekte anlegen und entfernen, ohne YAML von Hand.

Der Grund fuers Entfernen steht in `docs/Generalization_plan.md`: Wer dieses
Werkzeug uebernimmt, erbt sonst fremde Wohnungen in seiner
Vermoegensrechnung. Die Basisdatei traegt zu jedem Objekt seine Herleitung --
AfA-Basis aus der Feststellung, Anschaffungsdatum aus dem Notarvertrag --,
und die darf ein Klick nicht loeschen.

Alle Objekte hier sind erfunden.
"""

from __future__ import annotations

import yaml

from finctl import objekte

BASIS = """# Die Begruendung, die bleiben muss.
properties:
  - id: altbau
    name: Altbau Musterstadt
    address: "Musterweg 1"
    acquired_on: 2019-03-01
    status: rented
    # Herleitung, die kein Klick loeschen darf.
    afa_base_cents: 20000000
"""


def _config(tmp_path):
    (tmp_path / "properties.yaml").write_text(BASIS, encoding="utf-8")
    return tmp_path


def _eigene(tmp_path) -> dict:
    pfad = tmp_path / "properties_custom.yaml"
    if not pfad.exists():
        return {}
    return (yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}).get("objekte") or {}


# ------------------------------------------------------------------- anlegen

def test_a_new_property_lands_in_the_overlay_not_the_base_file(tmp_path):
    cfg = _config(tmp_path)
    objekte.anlegen({"id": "neubau", "name": "Neubau Beispiel",
                     "status": "rented"}, cfg)

    assert "# Die Begruendung, die bleiben muss." in \
        (cfg / "properties.yaml").read_text(encoding="utf-8")
    assert _eigene(cfg)["neubau"]["name"] == "Neubau Beispiel"
    assert set(objekte.vorhandene(cfg)) == {"altbau", "neubau"}


def test_the_register_merges_field_by_field(tmp_path):
    """Das Overlay ueberschreibt EINZELNE Felder.

    Nur den Namen zu aendern darf nicht die AfA-Basis aus der Basisdatei
    mitnehmen -- die steht dort mit ihrer Herleitung.
    """
    cfg = _config(tmp_path)
    objekte.anlegen({"id": "zweitwohnung", "name": "Zweitwohnung",
                     "status": "owner_occupied"}, cfg)
    eigene = _eigene(cfg)
    eigene["altbau"] = {"name": "Altbau, umbenannt"}
    (cfg / "properties_custom.yaml").write_text(
        yaml.safe_dump({"objekte": eigene}, allow_unicode=True), encoding="utf-8")

    altbau = objekte.vorhandene(cfg)["altbau"]
    assert altbau["name"] == "Altbau, umbenannt"
    assert altbau["afa_base_cents"] == 20000000      # aus der Basisdatei


# ------------------------------------------------------------------- pruefen

def test_a_duplicate_key_is_refused(tmp_path):
    cfg = _config(tmp_path)
    fehler = objekte.pruefen(objekte.objekt(
        {"id": "altbau", "name": "X", "status": "rented"}),
        set(objekte.vorhandene(cfg)))
    assert any("schon vergeben" in f for f in fehler)


def test_the_form_asks_for_three_things_and_no_more(tmp_path):
    """Kennung, Name, Zustand -- mehr braucht ein Objekt hier nicht.

    Der erste Entwurf verlangte zusaetzlich das Anschaffungsdatum, mit der
    Zehnjahresfrist des §23 EStG als Begruendung. Was ein Objekt gekostet
    hat, steht aber laengst als einmaliger Betrag in der Planung, und wer ein
    Objekt anlegt, hat den Notarvertrag nicht neben sich liegen.
    """
    assert objekte.FELDER == ("id", "name", "status")
    assert objekte.pruefen(
        objekte.objekt({"id": "schlicht", "name": "Schlicht",
                        "status": "rented"}), set()) == []


def test_an_unknown_status_is_refused(tmp_path):
    """Die deutsche Beschriftung ist nicht die Kennung.

    Geprueft wird auf das VORHANDENSEIN eines Fehlers, nicht auf seinen Text:
    „vermietet" steht auch in der Aufzaehlung der gueltigen Zustaende, ein
    Test darauf koennte also gar nicht durchfallen.
    """
    assert objekte.pruefen(
        objekte.objekt({"id": "x", "name": "X", "status": "vermietet"}), set())
    assert objekte.pruefen(
        objekte.objekt({"id": "x", "name": "X", "status": ""}), set())
    # Und die Kennung selbst geht durch.
    assert objekte.pruefen(
        objekte.objekt({"id": "x", "name": "X", "status": "rented"}), set()) == []


# ------------------------------------------------------------------ entfernen

def test_removing_a_base_property_hides_it_without_touching_the_file(tmp_path):
    cfg = _config(tmp_path)
    objekte.entfernen("altbau", cfg)

    assert "# Die Begruendung, die bleiben muss." in \
        (cfg / "properties.yaml").read_text(encoding="utf-8")
    assert _eigene(cfg)["altbau"] == {"entfernt": True}
    assert "altbau" not in objekte.vorhandene(cfg)


def test_removing_an_own_property_deletes_the_entry(tmp_path):
    """Was diese Seite angelegt hat, loescht sie auch -- ein `entfernt: true`
    auf einen Eintrag, den nur sie kennt, waere Ballast."""
    cfg = _config(tmp_path)
    objekte.anlegen({"id": "neubau", "name": "Neubau", "status": "construction"}, cfg)
    objekte.entfernen("neubau", cfg)

    assert "neubau" not in _eigene(cfg)
    assert set(objekte.vorhandene(cfg)) == {"altbau"}


def test_removing_something_that_does_not_exist_says_so(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="Kein Objekt"):
        objekte.entfernen("gibtsnicht", _config(tmp_path))


# ---------------------------------------------------------------- Seite

from fastapi.testclient import TestClient  # noqa: E402

from finctl.web.server import app  # noqa: E402

client = TestClient(app)


def test_the_page_offers_the_form_and_a_way_out():
    html = client.get("/immobilien").text
    assert 'id="o-id"' in html and 'id="o-status"' in html
    # Je Objekt ein Weg, es loszuwerden -- das ist der Teil, den ein neuer
    # Nutzer zuerst braucht.
    assert html.count("objektEntfernen(") >= 1


def test_the_status_list_offers_exactly_the_known_states():
    import re

    html = client.get("/immobilien").text
    block = html[html.index('id="o-status"'):]
    angeboten = set(re.findall(r'<option value="([^"]+)"',
                               block[:block.index("</select>")]))
    assert angeboten == set(objekte.ZUSTAENDE)


def test_the_endpoints_refuse_before_they_write():
    """Beide Ablehnungen kommen vor dem Schreiben, brauchen also keine
    Konfiguration -- und fassen die echte nicht an."""
    antwort = client.post("/api/objekt-neu", json={"id": "", "name": ""})
    assert antwort.status_code == 400 and "Kennung" in antwort.json()["error"]

    antwort = client.post("/api/objekt-entfernen", json={"id": "gibtsnicht"})
    assert antwort.status_code == 400 and "Kein Objekt" in antwort.json()["error"]
