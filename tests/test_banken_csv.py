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
    "dkb_csv": (2, 50000, 241234, "Supermarkt Muster", "ende"),
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
