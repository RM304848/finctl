"""Einen Auszug waehlen: erkennen, zuordnen, als Profil speichern, ablegen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finctl import konten
from finctl.ingest import importer, zuordnung
from finctl.web.server import app

client = TestClient(app)
BEISPIELE = Path("tests/beispiele/banken")

UNBEKANNT = ("Datum;Empfänger;Zweck;Betrag;Kontostand\r\n"
             "15.08.2026;Beispiel Arbeitgeber GmbH;Lohn;2.500,00;3.700,00\r\n"
             "01.08.2026;Wohnbau Muster eG;Miete;-800,00;1.200,00\r\n").encode()


def _datei(name: str, inhalt: bytes):
    return {"datei": (name, inhalt, "text/csv")}


def test_a_known_export_names_bank_account_period_and_whether_it_adds_up():
    inhalt = (BEISPIELE / "dkb_csv.csv").read_bytes()
    o = client.post("/api/einlesen/erkennen", files=_datei("umsaetze.csv", inhalt)).json()
    e = o["erkannt"]
    assert (e["profil"], e["bank"], e["konto"]) == ("dkb_csv", "DKB", "DE89370400440532013000")
    assert e["umsaetze"] == 2 and e["abgestimmt"]
    assert e["warnungen"], "nur Endsaldo: der Import sagt, was er nicht pruefen konnte"


def test_an_unknown_export_comes_back_with_suggested_columns():
    o = client.post("/api/einlesen/erkennen", files=_datei("bank.csv", UNBEKANNT)).json()
    a = o["analyse"]
    assert a["trenner"] == ";"
    v = a["vorschlag"]
    assert (v["datum"], v["betrag"], v["saldospalte"], v["gegenpartei"]) == (
        "Datum", "Betrag", "Kontostand", "Empfänger")
    assert a["saldo"] == "spalte"


def _zuordnung():
    a = zuordnung.analysieren(UNBEKANNT.decode("utf-8"))
    z = {"trenner": ";", "saldo": "spalte", "bank": "Testbank", "datum": "Datum",
         "betrag": "Betrag", "saldospalte": "Kontostand", "gegenpartei": "Empfänger",
         "zweck": "Zweck"}
    return z, a["spalten"]


def test_the_preview_reads_with_the_mapping_before_anything_is_saved():
    z, spalten = _zuordnung()
    o = client.post("/api/einlesen/vorschau", files=_datei("bank.csv", UNBEKANNT),
                    data={"zuordnung": json.dumps({"zuordnung": z, "spalten": spalten})}).json()
    v = o["vorschau"]
    assert v["abgestimmt"] and v["umsaetze"] == 2
    assert (v["anfang_cents"], v["ende_cents"]) == (200000, 370000)


def test_a_saved_mapping_is_a_profile_the_import_recognises(tmp_path, monkeypatch):
    monkeypatch.setattr(zuordnung, "PFAD", tmp_path / "csv_profile_custom.yaml")
    z, spalten = _zuordnung()
    antwort = client.post("/api/einlesen/profil", json={"kennung": "testbank", "zuordnung": z,
                                                        "spalten": spalten})
    assert antwort.json() == {"ok": True, "profil": "eigen_testbank"}
    assert "eigen_testbank" in importer.available_profiles()
    assert "eigen_testbank" in konten.profile()
    o = client.post("/api/einlesen/erkennen", files=_datei("bank.csv", UNBEKANNT)).json()
    assert o["erkannt"]["profil"] == "eigen_testbank" and o["erkannt"]["abgestimmt"]


def test_a_mapping_without_date_or_amount_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(zuordnung, "PFAD", tmp_path / "csv_profile_custom.yaml")
    antwort = client.post("/api/einlesen/profil", json={
        "kennung": "halb", "zuordnung": {"datum": "Datum"}, "spalten": ["Datum"]})
    assert antwort.status_code == 400
    assert not (tmp_path / "csv_profile_custom.yaml").exists()


def test_an_unknown_pdf_points_to_the_csv_export():
    antwort = client.post("/api/einlesen/erkennen",
                          files={"datei": ("auszug.pdf", b"%PDF-1.4 kaputt", "application/pdf")})
    assert antwort.status_code == 400


def test_something_that_is_not_a_statement_is_refused_by_its_name():
    antwort = client.post("/api/einlesen/erkennen", files=_datei("urlaub.jpg", b"xyz"))
    assert antwort.status_code == 400 and "CSV" in antwort.json()["error"]


def test_filing_never_overwrites(tmp_path, monkeypatch):
    konto = {"id": "testkonto", "statement_folder": str(tmp_path / "auszuege")}
    monkeypatch.setattr(konten, "laden", lambda: [konto])
    erst = client.post("/api/einlesen/ablegen", files=_datei("a.csv", b"eins"),
                       data={"konto": "testkonto"}).json()
    gleich = client.post("/api/einlesen/ablegen", files=_datei("a.csv", b"eins"),
                         data={"konto": "testkonto"}).json()
    anders = client.post("/api/einlesen/ablegen", files=_datei("a.csv", b"zwei"),
                         data={"konto": "testkonto"}).json()
    assert erst["pfad"] == gleich["pfad"] != anders["pfad"]
    assert Path(anders["pfad"]).name == "a-2.csv"
    assert Path(erst["pfad"]).read_bytes() == b"eins"


@pytest.mark.parametrize("pid", ["ing_csv", "volksbank_csv"])
def test_the_suggestions_would_also_read_the_described_banks(pid):
    """Die Heuristik trifft auch die bekannten Exporte -- ein Hinweis, dass
    sie fuer die unbekannten nicht ins Blaue raet."""
    text = importer.extract_pages(BEISPIELE / f"{pid}.csv")[0]
    v = zuordnung.analysieren(text)["vorschlag"]
    assert v["betrag"] and v["datum"] and v["saldospalte"]
