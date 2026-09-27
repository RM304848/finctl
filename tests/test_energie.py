"""Energie: weitere Zaehler und Vorraete ueber die Seite, in eigener Datei."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient

from finctl import strom as st
from finctl.energie import speicher as sp
from finctl.web.server import app

client = TestClient(app)


@pytest.fixture
def dateien(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "PFAD", tmp_path / "strom.yaml")
    monkeypatch.setattr(sp, "PFAD", tmp_path / "energie.yaml")
    return tmp_path


def _gas():
    return client.post("/api/energie/zaehler", json={
        "art": "gas", "name": "Gas Keller", "profil": "heizung", "grundlast": 0.15,
        "brennwert": 11.2, "zustandszahl": 0.95})


def test_a_gas_meter_lands_in_its_own_file_and_leaves_electricity_alone(dateien):
    antwort = _gas()
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["id"] == "gas-keller"
    roh = yaml.safe_load((dateien / "energie.yaml").read_text(encoding="utf-8"))
    assert roh["zaehler"]["gas-keller"]["brennwert"] == 11.2
    assert not (dateien / "strom.yaml").exists()


def test_gas_needs_the_values_from_the_bill(dateien):
    antwort = client.post("/api/energie/zaehler", json={"art": "gas", "name": "Gas"})
    assert antwort.status_code == 400


def test_a_gas_period_and_reading_are_billed_in_kwh(dateien):
    _gas()
    heute = date.today()
    beginn = heute - timedelta(days=100)
    assert client.post("/api/energie/zeitraum", json={
        "zaehler": "gas-keller", "nr": None, "zeitraum": {
            "beginn": beginn.isoformat(), "ende": (beginn + timedelta(days=364)).isoformat(),
            "zaehlerstand_beginn": 1000,
            "tarife": [{"ab": beginn.isoformat(), "grundpreis_jahr_cents": 15000,
                        "arbeitspreis_ct_kwh": 12}],
            "abschlaege": [{"ab": beginn.isoformat(), "monat_cents": 9000}]}}).status_code == 200
    gestern = (heute - timedelta(days=1)).isoformat()
    assert client.post("/api/energie/ablesung", json={
        "zaehler": "gas-keller", "datum": gestern, "stand": 1200}).status_code == 200
    seite = client.get("/energie?ansicht=gas-keller").text
    assert "Gas Keller" in seite and "ct/kWh" in seite and "nach Gradtagen" in seite
    # 200 m³ × 11,2 × 0,95 = 2.128 kWh bisher.
    assert "2.128 kWh" in seite


def test_a_stock_takes_deliveries_and_readings_and_says_how_long_it_lasts(dateien):
    antwort = client.post("/api/energie/vorrat", json={
        "art": "heizoel", "name": "Öltank", "kapazitaet": 3000, "mindestbestand": 300,
        "profil": "linear"})
    assert antwort.status_code == 200, antwort.text
    vid = antwort.json()["id"]
    heute = date.today()
    for art, tage, menge, extra in (("stand", 400, 1500, {}),
                                    ("lieferung", 200, 2000, {"cents": 200000}),
                                    ("stand", 35, 1500, {})):
        r = client.post("/api/energie/vorrat/eintrag", json={
            "vorrat": vid, "art": art, "menge": menge,
            "datum": (heute - timedelta(days=tage)).isoformat(), **extra})
        assert r.status_code == 200, r.text
    seite = client.get(f"/energie?ansicht={vid}").text
    assert "Reicht bis" in seite and "Zurücklegen im Monat" in seite
    # 2.000 l in 365 Tagen, 1 € je Liter: 167 € im Monat.
    assert "167,00" in seite


def test_a_reading_above_capacity_is_refused(dateien):
    vid = client.post("/api/energie/vorrat", json={"art": "pellets", "name": "Lager",
                                                    "kapazitaet": 5000}).json()["id"]
    r = client.post("/api/energie/vorrat/eintrag", json={
        "vorrat": vid, "art": "stand", "menge": 6000, "datum": date.today().isoformat()})
    assert r.status_code == 400 and "Kapazität" in r.json()["error"]


def test_electricity_cannot_be_removed_but_others_can(dateien):
    _gas()
    assert client.post("/api/energie/entfernen", json={"id": "strom"}).status_code == 400
    assert client.post("/api/energie/entfernen", json={"id": "gas-keller"}).status_code == 200
    assert "gas-keller" not in sp.laden().zaehler


def test_every_tab_renders(dateien):
    _gas()
    client.post("/api/energie/vorrat", json={"art": "fluessiggas", "name": "Gastank"})
    for ansicht in ("strom", "gas-keller", "gastank", "neu"):
        assert client.get(f"/energie?ansicht={ansicht}").status_code == 200, ansicht
