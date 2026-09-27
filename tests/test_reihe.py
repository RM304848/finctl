"""Die Reihe einer zugeordneten Buchung.

Ein Haken ist ein Beispiel, keine Inventur. Was hier schiefgeht, kostet kein
Detail, sondern zaehlt einen ganzen Vertrag zweimal: einmal als Median, einmal
als Termin.
"""

from __future__ import annotations

import sqlite3

import pytest

from finctl.ledger import reihe


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, dedup_hash TEXT,
                                   counterparty_norm TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
    """)
    return c


def _buchung(c, dedup_hash, gegenpartei, kategorie, cents):
    cur = c.execute("INSERT INTO transactions (dedup_hash, counterparty_norm) "
                    "VALUES (?, ?)", (dedup_hash, gegenpartei))
    c.execute("INSERT INTO splits VALUES (?, ?, ?)",
              (cur.lastrowid, kategorie, cents))


def test_one_tick_pulls_the_whole_series(conn):
    """Ein Haken von vielen hiess: der ganze Rest blieb im Median stehen,
    waehrend der Vertrag seinen Termin trotzdem buchte."""
    for i in range(6):
        _buchung(conn, f"praemie{i}", "kasse", "versicherung/pkv", -44400)
    assert reihe.hashes(conn, ["praemie0"]) == [f"praemie{i}" for i in range(6)]


def test_a_rising_premium_stays_in_the_series(conn):
    """Ein Beitrag steigt, der Empfaenger bleibt. Ueber den Betrag allein
    faende man die alten Monate nicht wieder."""
    _buchung(conn, "alt", "kasse", "versicherung/pkv", -41000)
    _buchung(conn, "neu", "kasse", "versicherung/pkv", -44400)
    assert reihe.hashes(conn, ["neu"], -44400) == ["alt", "neu"]


def test_the_amount_separates_two_things_under_one_name(conn):
    """Einkauf und Abo beim selben Haendler in derselben Kategorie.

    Die Reihe allein trennt sie nicht -- der Vertragsbetrag trennt sie.
    """
    _buchung(conn, "abo", "haendler", "konsum/sonstiges", -8990)
    for i in range(4):
        _buchung(conn, f"einkauf{i}", "haendler", "konsum/sonstiges", -2500)
    assert reihe.hashes(conn, ["abo"], -8990) == ["abo"]
    # Ohne Betrag bleibt es bei der breiten Reihe: eine Planzeile kennt
    # keinen Sollbetrag und nimmt deshalb, was der Empfaenger hergibt.
    assert len(reihe.hashes(conn, ["abo"])) == 5


def test_a_refund_is_not_part_of_the_series(conn):
    """Eine Erstattung ist echtes Geld und keine Praemie: sie bleibt gemessen."""
    _buchung(conn, "praemie", "kasse", "versicherung/pkv", -44400)
    _buchung(conn, "erstattung", "kasse", "versicherung/pkv", 104500)
    assert reihe.hashes(conn, ["praemie"], -44400) == ["praemie"]


def test_another_category_is_another_thing(conn):
    """Derselbe Empfaenger, andere Kategorie: das ist nicht derselbe Posten."""
    _buchung(conn, "abo", "haendler", "abo/video-streaming", -999)
    _buchung(conn, "einkauf", "haendler", "konsum/sonstiges", -999)
    assert reihe.hashes(conn, ["abo"], -999) == ["abo"]


def test_a_booking_without_a_counterparty_stands_alone(conn):
    """Ohne Gegenpartei gibt es keine Reihe -- und geraten wird nicht."""
    _buchung(conn, "einzel", None, "konsum/sonstiges", -1000)
    _buchung(conn, "anderes", None, "konsum/sonstiges", -1000)
    assert reihe.hashes(conn, ["einzel"], -1000) == ["einzel"]


def test_without_a_connection_only_the_ticked_ones_count(conn):
    assert reihe.hashes(None, ["a", "b"]) == ["a", "b"]
    assert reihe.hashes(conn, []) == []
