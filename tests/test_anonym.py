"""Anonymisieren: die Kopie verraet niemanden und rechnet noch."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from finctl.cli import app
from finctl.ingest import anonym
from finctl.ingest.importer import detect, extract_pages, load_parser, reconcile

BEISPIELE = Path("tests/beispiele/banken")


@pytest.mark.parametrize("pid", ["ing_csv", "comdirect_csv", "dkb_csv", "sparkasse_csv",
                                 "volksbank_csv", "commerzbank_csv", "n26_csv"])
@pytest.mark.parametrize("faktor", [1, 3])
def test_the_copy_is_still_recognised_and_still_reconciles(tmp_path, pid, faktor):
    kopie, _ = anonym.verfremden(BEISPIELE / f"{pid}.csv", tmp_path / f"{pid}.csv",
                                 namen=["Erika Beispiel"], faktor=faktor)
    seiten = extract_pages(kopie)
    erkannt = detect(seiten, kopie)
    assert erkannt is not None and erkannt.profile_id == pid
    original = load_parser(pid).parse(extract_pages(BEISPIELE / f"{pid}.csv"),
                                      BEISPIELE / f"{pid}.csv")
    verfremdet = erkannt.parse(seiten, kopie)
    assert reconcile(verfremdet).ok
    assert [t.amount_cents * faktor for t in original.transactions] == [
        t.amount_cents for t in verfremdet.transactions]
    assert [t.booking_date for t in original.transactions] == [
        t.booking_date for t in verfremdet.transactions]


def test_names_ibans_and_free_text_are_gone(tmp_path):
    kopie, zaehlung = anonym.verfremden(BEISPIELE / "dkb_csv.csv", tmp_path / "k.csv",
                                        namen=["Erika Beispiel"])
    text = kopie.read_text(encoding="utf-8")
    for weg in ("Erika Beispiel", "DE89370400440532013000", "Supermarkt Muster",
                "Beispiel Arbeitgeber GmbH", "Lohn August"):
        assert weg not in text, weg
    # Was Filter und Erkennung brauchen, bleibt.
    for bleibt in ("Gebucht", "Vorgemerkt", "Buchungsdatum", "2.000", "31.08.26"):
        assert bleibt in text, bleibt
    assert zaehlung["iban"] and zaehlung["text"]


def test_the_same_name_becomes_the_same_placeholder(tmp_path):
    kopie, _ = anonym.verfremden(BEISPIELE / "volksbank_csv.csv", tmp_path / "k.csv")
    text = kopie.read_text(encoding="utf-8")
    assert text.count("DE00000000000000000001") >= 2   # eigenes Konto, jede Zeile gleich
    assert "Musterbank eG" not in text


def test_the_customer_name_in_the_preamble_goes_even_without_being_named(tmp_path):
    kopie, _ = anonym.verfremden(BEISPIELE / "ing_csv.csv", tmp_path / "k.csv")
    assert "Erika Beispiel" not in kopie.read_text(encoding="utf-8")


def test_a_pdf_becomes_page_text_that_the_import_reads(tmp_path, monkeypatch):
    from finctl.ingest import importer

    seite1 = ("Kontoauszug Erika Beispiel\nIBAN DE89 3704 0044 0532 0130 00\n"
              "Kundennummer 12345678 Betrag 1.234,56")
    seiten = [seite1, "Seite 2"]
    monkeypatch.setattr(importer, "extract_pages", lambda p: seiten)
    pdf = tmp_path / "auszug.pdf"
    pdf.write_bytes(b"%PDF")
    kopie, _ = anonym.verfremden(pdf, namen=["Erika Beispiel"])
    assert kopie.suffix == ".txt"
    text = kopie.read_text(encoding="utf-8")
    assert "Erika" not in text and "DE89" not in text and "12345678" not in text
    assert "1.234,56" in text and text.count("\f") == 1


def test_the_original_is_never_overwritten(tmp_path):
    ziel = tmp_path / "x.csv"
    ziel.write_bytes((BEISPIELE / "n26_csv.csv").read_bytes())
    with pytest.raises(ValueError, match="Original"):
        anonym.verfremden(ziel, ziel)


def test_the_command_reminds_to_look_before_sharing(tmp_path):
    ergebnis = CliRunner().invoke(app, ["anonymisieren", str(BEISPIELE / "n26_csv.csv"),
                                        "--ziel", str(tmp_path / "k.csv")])
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Vor dem Weitergeben ansehen" in ergebnis.output
