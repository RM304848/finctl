"""Die Seite /regeln und ihr Overlay rules_custom.yaml.

rules.yaml traegt die Begruendung jeder Regel in Kommentaren. Die Seite darf
sie nie anfassen, und in die Kopie gehoert nur, was abweicht -- sonst friert
ein Speichern ohne Aenderung jede Regel auf dem Stand von heute ein.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from finctl.ledger.db import connect
from finctl.rules import engine
from finctl.rules import regelwerk as rw
from finctl.web.server import app

client = TestClient(app)

BASIS = """# Kommentar, der bleiben muss
rules:
  # warum alpha
  - id: alpha
    priority: 10
    name: Alpha
    match: {text: ["Bundeskasse"], any_of: [{account: c24}, {account: dkb-giro}]}
    set: {mgmt: steuern/kfz, tax: privat/nicht-abzugsfaehig, transfer_account: c24}
  - id: beta
    priority: 20
    name: Beta
    match: {text: ["Beta"], amount_abs_between: [0, 50]}
    set: {mgmt: konsum/x}
"""


@pytest.fixture
def dateien(tmp_path):
    basis = tmp_path / "rules.yaml"
    basis.write_text(BASIS, encoding="utf-8")
    return basis, tmp_path / "rules_custom.yaml"


def _form(**kw) -> dict:
    f = {"name": "", "priority": "", "text": "", "text_all": "", "counterparty": "",
         "account": "", "sign": "", "betrag_von_cents": None, "betrag_bis_cents": None,
         "datum_von": "", "datum_bis": "", "mgmt": "", "tax": "", "property": "",
         "note": ""}
    f.update(kw)
    return f


def _wie_geladen(regel_id, basis, custom, **aenderung) -> dict:
    """Was die Seite fuer eine Regel ins Formular schreibt, mit Aenderungen."""
    e = next(r for r in rw.laden(basis, custom) if r["id"] == regel_id)
    return {**rw.formwerte(e), **aenderung, "id": regel_id}


def _eine_kategorie() -> str:
    c = connect()
    try:
        return c.execute("SELECT id FROM mgmt_categories WHERE parent_id IS NOT NULL "
                         "AND active = 1 ORDER BY id LIMIT 1").fetchone()["id"]
    finally:
        c.close()


# ----------------------------------------------------------------- Overlay

def test_saving_an_unchanged_rule_writes_nothing(dateien):
    basis, custom = dateien
    for regel in ("alpha", "beta"):
        e, _ = rw.entwurf(_wie_geladen(regel, basis, custom), basis, custom)
        rw.speichern(e, basis, custom)
    assert rw.custom_lesen(custom) == {}
    assert basis.read_text(encoding="utf-8") == BASIS


def test_an_edit_stores_only_what_differs_and_keeps_what_the_page_cannot_show(dateien):
    basis, custom = dateien
    e, _ = rw.entwurf(_wie_geladen("alpha", basis, custom, text="Bundeskasse, Zoll"),
                      basis, custom)
    rw.speichern(e, basis, custom)

    assert list(rw.custom_lesen(custom)["alpha"]) == ["match"]
    alpha = next(r for r in engine.load_rules(basis, custom) if r.id == "alpha")
    assert alpha.match["text"] == ["Bundeskasse", "Zoll"]
    assert alpha.match["any_of"] == [{"account": "c24"}, {"account": "dkb-giro"}]
    assert alpha.actions["transfer_account"] == "c24"
    assert basis.read_text(encoding="utf-8") == BASIS, "rules.yaml bleibt unberuehrt"


def test_a_new_rule_gets_an_id_and_removal_hides_a_base_rule(dateien):
    basis, custom = dateien
    e, _ = rw.entwurf({**_form(name="Kfz-Steuer Zoll", text="Zoll", mgmt="steuern/kfz"),
                       "neu": True}, basis, custom)
    assert e["id"] == "kfz-steuer-zoll"
    rw.speichern(e, basis, custom)
    rw.entfernen("beta", basis, custom)

    assert {r["id"]: r["herkunft"] for r in rw.laden(basis, custom)} == {
        "alpha": "basis", "kfz-steuer-zoll": "eigen"}
    rw.entfernen("kfz-steuer-zoll", basis, custom)
    assert "kfz-steuer-zoll" not in rw.custom_lesen(custom)


@pytest.mark.parametrize("aenderung, meldung", [
    ({"text": "", "betrag_bis_cents": None}, "Bedingung"),
    ({"mgmt": ""}, "setzt nichts"),
    ({"datum_von": "2026-1"}, "Datum"),
    ({"priority": "zehn"}, "Priorität"),
    ({"name": ""}, "Name"),
], ids=["ohne-bedingung", "setzt-nichts", "datum", "prioritaet", "name"])
def test_an_invalid_rule_is_refused_before_anything_is_written(dateien, aenderung, meldung):
    basis, custom = dateien
    with pytest.raises(ValueError, match=meldung):
        e, _ = rw.entwurf(_wie_geladen("beta", basis, custom, **aenderung), basis, custom)
        rw.speichern(e, basis, custom)
    assert not custom.exists()


def test_the_amount_window_survives_the_form_unchanged(dateien):
    basis, custom = dateien
    w = _wie_geladen("beta", basis, custom)
    assert (w["betrag_von_cents"], w["betrag_bis_cents"]) == (None, 5000)
    e, _ = rw.entwurf(w, basis, custom)
    assert e["match"]["amount_abs_between"] == [0, 50]


# ------------------------------------------------------------------- Seite

def test_the_rules_page_lists_rules_and_prefills_a_new_one_from_a_booking():
    from finctl.rules.engine import load_rules

    html = client.get("/regeln").text
    assert load_rules()[0].id in html
    assert "const VORLAGE = null" in html

    c = connect()
    try:
        tx = c.execute("SELECT id FROM transactions ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        c.close()
    html = client.get(f"/regeln?aus={tx['id']}").text
    assert "const VORLAGE = null" not in html


def test_review_and_transactions_offer_a_rule_from_the_booking():
    assert "/regeln?aus=" in client.get("/transactions").text


def test_the_preview_shows_what_a_draft_would_catch_without_writing(monkeypatch, tmp_path):
    custom = tmp_path / "rules_custom.yaml"
    monkeypatch.setattr(engine, "RULES_CUSTOM_PATH", custom)
    r = client.post("/api/regeln/vorschau", json={
        **_form(name="pytest", text="Bundeskasse", mgmt=_eine_kategorie()), "neu": True})
    assert r.status_code == 200, r.text
    assert {"treffer", "aendert", "verdeckt", "eigen", "verliert", "zeilen"} <= set(r.json())
    assert not custom.exists()


def test_a_rule_pointing_at_an_unknown_category_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "RULES_CUSTOM_PATH", tmp_path / "rules_custom.yaml")
    r = client.post("/api/regeln/vorschau", json={
        **_form(name="pytest", text="Bundeskasse", mgmt="gibt/es-nicht"), "neu": True})
    assert r.status_code == 400
    assert "unbekannte Kategorie" in r.json()["error"]


def test_saving_and_removing_through_the_page(monkeypatch, tmp_path):
    custom = tmp_path / "rules_custom.yaml"
    monkeypatch.setattr(engine, "RULES_CUSTOM_PATH", custom)
    r = client.post("/api/regeln", json={
        **_form(name="pytest nie gesehen", text="pytest-nie-gesehen-4711",
                mgmt=_eine_kategorie()), "neu": True})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert rid in rw.custom_lesen(custom)
    assert f"r-{rid}" in client.get("/regeln").text

    assert client.post(f"/api/regeln/{rid}/entfernen").status_code == 200
    assert rid not in rw.custom_lesen(custom)
