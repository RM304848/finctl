"""Scalable Tagesgeld: what the printed opening balance already contains.

"Kontostand am 01.09." is the balance at the END of the 1st. Interest
value-dated on the last day of the previous month is in it, and so is a
transfer value-dated on the 1st itself. Counting that transfer again made
every statement with a payment on the 1st fail by exactly its amount.

The statements here are invented; round numbers, no real account.
"""

from __future__ import annotations

from finctl.ingest.importer import extract_pages, import_statement, reconcile
from finctl.ingest.profiles.scalable_tagesgeld import PARSER
from finctl.ledger.db import connect, init_db, statement_chain_gaps

_KOPF = """Scalable Capital Bank GmbH
Kontoauszug
Zeitraum {von} - {bis}
Verrechnungskonto DE00000000000000000000
Kontostand am {von} {start} EUR
Kontostand am {bis} {ende} EUR
Buchung Wertstellung Beschreibung Betrag
"""

AUGUST = _KOPF.format(von="01.08.2026", bis="31.08.2026",
                      start="9.000,00", ende="10.000,00") + (
    "15.08.2026 15.08.2026 Überweisung +1.000,00 EUR\n"
)

# Opening = August close + interest (Wertstellung 31.08) + transfer on the 1st.
SEPTEMBER = _KOPF.format(von="01.09.2026", bis="30.09.2026",
                         start="10.520,00", ende="9.820,00") + (
    "01.09.2026 31.08.2026 Erhaltene Zinsen +20,00 EUR\n"
    "01.09.2026 01.09.2026 Überweisung +500,00 EUR\n"
    "10.09.2026 10.09.2026 Überweisung +300,00 EUR\n"
    "20.09.2026 20.09.2026 Abhebung vom Geldkonto -1.000,00 EUR\n"
)


def _auszug(tmp_path, name: str, text: str):
    pfad = tmp_path / f"{name}.txt"
    pfad.write_text(text, encoding="utf-8")
    return pfad


def test_a_transfer_on_the_first_is_already_in_the_opening_balance(tmp_path):
    pfad = _auszug(tmp_path, "september", SEPTEMBER)
    ergebnis = PARSER.parse(extract_pages(pfad), pfad)

    assert len(ergebnis.transactions) == 4
    assert reconcile(ergebnis).ok


def test_such_statements_import_and_chain(tmp_path):
    db = tmp_path / "ledger.db"
    init_db(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO accounts (id, display_name, institution, account_type,"
        " ingest_mode, parser_profile) VALUES (?,?,?,?,?,?)",
        ("tagesgeld", "Tagesgeld", "Bank", "tagesgeld", "parsed", PARSER.profile_id),
    )
    for name, text in (("august", AUGUST), ("september", SEPTEMBER)):
        ausgang = import_statement(conn, _auszug(tmp_path, name, text), "tagesgeld")
        assert ausgang.status == "imported", ausgang.message

    assert statement_chain_gaps(conn) == []
