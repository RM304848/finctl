"""Goldtests der CSV-Exporte: erkennen, lesen, abstimmen.

Die Dateien unter tests/beispiele/banken/ sind erfunden und folgen der
Beschreibung in finctl/ingest/profiles/banken_csv.py. Sie pruefen den Parser
gegen die Beschreibung -- ob die Beschreibung die echte Bank trifft, zeigt
erst ein echter Export.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finctl.ingest.importer import detect, extract_pages, load_parser, reconcile
from finctl.ingest.profiles.banken_csv import PROFILE

BEISPIELE = Path("tests/beispiele/banken")

#: Profil -> (Anzahl gebucht, Anfang, Ende, eine Gegenpartei, Saldoquelle)
ERWARTET = {
    "ing_csv": (3, 30000, 190500, "Stadtwerke Musterstadt", "spalte"),
    "comdirect_csv": (2, 100000, 115550, None, "kopf"),
    "dkb_csv": (2, 51000, 242234, "Supermarkt Muster", "ende"),
    "sparkasse_csv": (2, 0, 3001, "Telefon Muster GmbH", "keiner"),
    "volksbank_csv": (2, 200000, 149260, "Bäckerei Muster", "spalte"),
    "commerzbank_csv": (2, 0, 174000, None, "keiner"),
    "n26_csv": (2, 0, 23710, "Buchhandlung Muster", "keiner"),
}


def test_every_described_bank_has_a_sample():
    assert {p.id for p in PROFILE} == set(ERWARTET)
    for pid in ERWARTET:
        assert (BEISPIELE / f"{pid}.csv").exists(), pid


@pytest.mark.parametrize("pid", sorted(ERWARTET))
def test_the_bank_is_recognised_from_the_file_alone(pid):
    pfad = BEISPIELE / f"{pid}.csv"
    erkannt = detect(extract_pages(pfad), pfad)
    assert erkannt is not None and erkannt.profile_id == pid


@pytest.mark.parametrize("pid", sorted(ERWARTET))
def test_the_sample_reconciles_and_skips_what_is_not_booked(pid):
    anzahl, anfang, ende, gegenpartei, quelle = ERWARTET[pid]
    pfad = BEISPIELE / f"{pid}.csv"
    ergebnis = load_parser(pid).parse(extract_pages(pfad), pfad)
    assert len(ergebnis.transactions) == anzahl
    kopf = ergebnis.header
    assert (kopf.balance_start_cents, kopf.balance_end_cents) == (anfang, ende)
    assert reconcile(ergebnis).ok
    daten = [t.booking_date for t in ergebnis.transactions]
    assert daten == sorted(daten), "chronologisch, auch wenn die Bank neueste zuerst liefert"
    if gegenpartei:
        assert gegenpartei in {t.counterparty for t in ergebnis.transactions}
    # Ohne vollstaendigen Saldo sagt der Import, was er nicht pruefen konnte.
    assert bool(ergebnis.warnings) == (quelle in ("ende", "keiner"))


def test_an_incoming_payment_at_dkb_names_the_payer_not_the_owner():
    pfad = BEISPIELE / "dkb_csv.csv"
    eingang = next(t for t in load_parser("dkb_csv").parse(extract_pages(pfad), pfad).transactions
                   if t.amount_cents > 0)
    assert eingang.counterparty == "Beispiel Arbeitgeber GmbH"


def test_the_period_is_the_exported_range_not_the_first_and_last_booking():
    """Ohne Buchung am Monatsletzten saehe der Monat sonst unvollstaendig aus,
    und der Stichtag der Prognose bliebe einen Monat zurueck."""
    pfad = BEISPIELE / "dkb_csv.csv"
    kopf = load_parser("dkb_csv").parse(extract_pages(pfad), pfad).header
    assert (kopf.period_start, kopf.period_end) == ("2026-08-01", "2026-08-30")


def test_the_export_day_stays_open_for_the_next_export():
    """Was am Tag des Exports noch gebucht wird, steht in keiner Datei, die an
    diesem Tag endet. Der Tag geht ganz in den naechsten Export; der Endsaldo
    ist der vom Vorabend: Kontostand ohne die heutigen Buchungen."""
    pfad = BEISPIELE / "dkb_csv.csv"
    ergebnis = load_parser("dkb_csv").parse(extract_pages(pfad), pfad)
    assert "2026-08-31" not in {t.booking_date for t in ergebnis.transactions}
    assert ergebnis.header.balance_end_cents == 241234 + 1000
    assert any("nächsten Export" in w for w in ergebnis.warnings)


def test_references_outside_the_purpose_still_reach_the_rules():
    """Im PDF stehen Mandatsreferenz und Glaeubiger-ID im Buchungstext, und
    manche Regel erkennt einen Kredit nur daran."""
    pfad = BEISPIELE / "dkb_csv.csv"
    einkauf = next(t for t in load_parser("dkb_csv").parse(extract_pages(pfad), pfad).transactions
                   if t.amount_cents == -8766)
    assert "KREDIT-4711" in einkauf.raw_text and "DE00ZZZ00000000001" in einkauf.raw_text
    assert "KREDIT-4711" not in (einkauf.purpose or "")


def test_a_balance_dated_after_the_range_is_refused(tmp_path):
    """Die DKB nennt den Kontostand vom Tag des Exports. Endet der Zeitraum
    frueher, stuende der Anfangssaldo um alles dazwischen falsch da."""
    text = (BEISPIELE / "dkb_csv.csv").read_text(encoding="utf-8-sig")
    pfad = tmp_path / "spaet.csv"
    pfad.write_text(text.replace("Kontostand vom 31.08.2026", "Kontostand vom 30.09.2026"),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="bis heute"):
        load_parser("dkb_csv").parse(extract_pages(pfad), pfad)


def test_the_own_account_is_read_from_the_export():
    for pid in ("ing_csv", "dkb_csv", "sparkasse_csv", "volksbank_csv"):
        pfad = BEISPIELE / f"{pid}.csv"
        kopf = load_parser(pid).parse(extract_pages(pfad), pfad).header
        assert kopf.account_hint == "DE89370400440532013000", pid


def test_a_gap_in_the_running_balance_is_reported(tmp_path):
    text = (BEISPIELE / "ing_csv.csv").read_bytes().decode("cp1252")
    text = text.replace("15.08.2026;15.08.2026;Stadtwerke Musterstadt;Lastschrift;"
                        "Abschlag Strom;-595,00;EUR;-95,00;EUR\r\n", "")
    pfad = tmp_path / "luecke.csv"
    pfad.write_bytes(text.encode("cp1252"))
    ergebnis = load_parser("ing_csv").parse(extract_pages(pfad), pfad)
    assert any("Saldo springt" in w for w in ergebnis.warnings)
    assert not reconcile(ergebnis).ok


#: Der Atruvia-Export, wie ihn eine Sparda-Bank schreibt: dieselben Spalten wie
#: bei Volks- und Raiffeisenbanken, UTF-8 mit BOM, Saldo ohne Tausenderpunkt.
#: Erfunden; der Aufbau folgt einem echten Export.
_SPARDA_CSV = (
    "﻿Bezeichnung Auftragskonto;IBAN Auftragskonto;BIC Auftragskonto;"
    "Bankname Auftragskonto;Buchungstag;Valutadatum;Name Zahlungsbeteiligter;"
    "IBAN Zahlungsbeteiligter;BIC (SWIFT-Code) Zahlungsbeteiligter;Buchungstext;"
    "Verwendungszweck;Betrag;Waehrung;Saldo nach Buchung;Bemerkung;"
    "Gekennzeichneter Umsatz;Glaeubiger ID;Mandatsreferenz\r\n"
    "Girokonto;DE89370400440532013000;GENODEF1XXX;Sparda-Bank Muster eG;"
    "30.09.2026;30.09.2026;Kreditrate;;;Darlehenstilgung;"
    "IBAN DE02100100100006820101RECHN.ZINS         150,00  TILG./ENTG.        "
    "350,00  TILGUNG PER    30.09.2026;-500,00;EUR;600,00;;;;\r\n"
    "Girokonto;DE89370400440532013000;GENODEF1XXX;Sparda-Bank Muster eG;"
    "01.09.2026;01.09.2026;Erika Mustermann;;;Dauerauftragsgutschr;Deckung;"
    "500,00;EUR;1100,00;;;;\r\n"
)


def test_a_sparda_export_is_read_as_atruvia_csv_not_as_a_sparda_pdf(tmp_path):
    """Der PDF-Parser der Sparda erkannte die CSV am Banknamen und scheiterte
    dann an den fehlenden Kontostandzeilen -- wie bei der DKB gehoert der
    Export dem CSV-Profil, die PDFs bleiben beim PDF-Parser."""
    from finctl.rules.categorize import declared_split

    pfad = tmp_path / "Umsaetze_Sparda.csv"
    pfad.write_text(_SPARDA_CSV, encoding="utf-8")
    seiten = extract_pages(pfad)
    assert not load_parser("sparda_giro").matches(seiten[0], pfad)
    erkannt = detect(seiten, pfad)
    assert erkannt is not None and erkannt.profile_id == "volksbank_csv"

    ergebnis = erkannt.parse(seiten, pfad)
    kopf = ergebnis.header
    assert (kopf.balance_start_cents, kopf.balance_end_cents) == (60000, 60000)
    assert kopf.account_hint == "DE89370400440532013000"
    assert reconcile(ergebnis).ok
    rate = next(t for t in ergebnis.transactions if t.amount_cents < 0)
    # Dieselben Worte, an denen die Regeln haengen, und die Aufteilung der
    # Rate, die nur die Sparda druckt.
    assert "Darlehenstilgung" in rate.raw_text and "Kreditrate" in rate.raw_text
    assert declared_split(rate.raw_text) == (15000, 35000)
