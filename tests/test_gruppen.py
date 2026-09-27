"""Vorgänge: eine Gruppe je Transaktion, über Kategorien hinweg.

Getestet wird das Datumsfenster, weil es der Punkt ist und nicht ein Zusatz:
derselbe Versicherer gehört vor und nach einem Fahrzeugwechsel in
verschiedene Gruppen, und ohne Fenster fielen zwanzig Jahre Beiträge in einen
Topf.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest
import yaml

from finctl.ledger import gruppen as gr


def _row(**kw):
    base = dict(id=1, booking_date="2026-05-01", account_id="dkb-giro",
                counterparty_norm="allianz", raw_text="Allianz Beitrag",
                mgmt_category_id="mobilitaet/kfz-versicherung",
                property_id=None)
    base.update(kw)
    return base


def test_the_date_window_separates_two_cars_from_one_insurer():
    """Der eigentliche Grund für das Fenster.

    Ohne es lägen die Beiträge für den alten und den neuen Wagen in derselben
    Gruppe, und die Frage "was hat mich dieses Auto gekostet" hätte keine
    Antwort mehr.
    """
    alt = gr.Gruppe(id="skoda", name="Skoda", von=date(2024, 1, 1),
                    bis=date(2026, 10, 31), counterparty=("allianz",))
    neu = gr.Gruppe(id="tesla", name="Tesla", von=date(2026, 11, 1),
                    counterparty=("allianz",))
    frueh, spaet = _row(booking_date="2026-05-01"), _row(booking_date="2026-12-01")
    assert alt.matches(frueh) and not neu.matches(frueh)
    assert neu.matches(spaet) and not alt.matches(spaet)


def test_an_open_group_has_no_end():
    laufend = gr.Gruppe(id="x", name="X", von=date(2024, 1, 1))
    assert laufend.matches(_row(booking_date="2099-01-01"))


def test_every_stated_condition_must_hold():
    g = gr.Gruppe(id="x", name="X", von=date(2024, 1, 1),
                  counterparty=("allianz",), konto="dkb-giro")
    assert g.matches(_row())
    assert not g.matches(_row(account_id="c24"))
    assert not g.matches(_row(counterparty_norm="huk", raw_text="HUK Beitrag"))


def test_several_prefixes_because_a_case_rarely_sits_on_one_branch():
    """Zum Auto gehören mobilitaet/kfz-* UND mobilitaet/kraftstoff, aber nicht
    mobilitaet/bahn-privat. Auf `mobilitaet/` zu verkürzen zöge Bahn, Fahrrad
    und Parken mit hinein -- der Skoda sprang dadurch von 3.016 auf 7.250."""
    g = gr.Gruppe(id="auto", name="Auto", von=date(2024, 1, 1),
                  kategorie_prefix=("mobilitaet/kfz", "mobilitaet/kraftstoff"))
    assert g.matches(_row(mgmt_category_id="mobilitaet/kfz-steuer"))
    assert g.matches(_row(mgmt_category_id="mobilitaet/kraftstoff"))
    assert not g.matches(_row(mgmt_category_id="mobilitaet/bahn-privat"))


def test_the_first_matching_rule_wins(tmp_path):
    """Wie im Kategorien-Regelwerk -- damit eine engere Regel vor eine
    weitere gestellt werden kann."""
    path = tmp_path / "groups.yaml"
    path.write_text(yaml.safe_dump({"gruppen": [
        {"id": "eng", "von": "2024-01-01",
         "match": {"kategorie_prefix": "mobilitaet/kfz-steuer"}},
        {"id": "weit", "von": "2024-01-01",
         "match": {"kategorie_prefix": "mobilitaet/"}},
    ]}), encoding="utf-8")
    geladen = gr.load(path)
    assert [g.id for g in geladen] == ["eng", "weit"]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, booking_date TEXT,
            account_id TEXT, counterparty_norm TEXT, raw_text TEXT,
            group_id TEXT);
        CREATE TABLE splits (transaction_id INTEGER, seq INTEGER,
            mgmt_category_id TEXT, property_id TEXT, amount_cents INTEGER);
        INSERT INTO transactions VALUES (1,'2025-03-01','dkb','x','x',NULL);
        INSERT INTO splits VALUES (1,0,'mobilitaet/kfz-steuer',NULL,-600);
    """)
    treffer = gr.apply(conn, geladen)
    assert treffer["eng"] == 1 and treffer["weit"] == 0


def test_applying_clears_stale_assignments():
    """Sonst bliebe eine Transaktion in einer Gruppe, deren Regel entfernt
    wurde, und der Zustand hinge davon ab, in welcher Reihenfolge die Regeln
    im Lauf der Zeit existiert haben."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, booking_date TEXT,
            account_id TEXT, counterparty_norm TEXT, raw_text TEXT,
            group_id TEXT);
        CREATE TABLE splits (transaction_id INTEGER, seq INTEGER,
            mgmt_category_id TEXT, property_id TEXT, amount_cents INTEGER);
        INSERT INTO transactions VALUES (1,'2025-03-01','dkb','x','x','veraltet');
        INSERT INTO splits VALUES (1,0,'konsum/sonstiges',NULL,-600);
    """)
    gr.apply(conn, [])
    assert conn.execute(
        "SELECT group_id FROM transactions WHERE id=1").fetchone()[0] is None


def test_a_dry_run_writes_nothing():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, booking_date TEXT,
            account_id TEXT, counterparty_norm TEXT, raw_text TEXT,
            group_id TEXT);
        CREATE TABLE splits (transaction_id INTEGER, seq INTEGER,
            mgmt_category_id TEXT, property_id TEXT, amount_cents INTEGER);
        INSERT INTO transactions VALUES (1,'2025-03-01','dkb','x','x',NULL);
        INSERT INTO splits VALUES (1,0,'konsum/sonstiges',NULL,-600);
    """)
    g = gr.Gruppe(id="alles", name="Alles", von=date(2024, 1, 1))
    assert gr.apply(conn, [g], dry_run=True)["alles"] == 1
    assert conn.execute(
        "SELECT group_id FROM transactions WHERE id=1").fetchone()[0] is None


def test_the_shipped_rules_span_more_than_one_category():
    """Eine Gruppe, die auf einer Kategorie sitzt, ist eine Kategorie."""
    from pathlib import Path

    if not Path("data/finance.db").exists():
        pytest.skip("no ledger present")
    from finctl.ledger.db import connect

    conn = connect()
    try:
        gr.apply(conn)
        for row in gr.summary(conn):
            assert row["kategorien"] > 1, row["id"]
    finally:
        conn.close()
