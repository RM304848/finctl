"""Eine Kuendigung wirkt in BEIDEN Rechnungen.

Die Kontoprognose las `gekuendigt_zum` und hoerte am Stichtag auf zu buchen.
Die Jahresrechnung kannte das Feld nicht und sah die Kuendigung erst, wenn die
alten Buchungen aus dem Messfenster gelaufen waren -- zwoelf Monate lang eine
Ersparnis, die es laengst gab, und deshalb Ziele, die zu spaet nachgaben.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from finctl import abos
from finctl.forecast import abgleich as ag
from finctl.forecast import jahre as jm
from finctl.forecast import szenarien as sz

FENSTER = ag.Fenster(von=date(2025, 9, 1), bis=date(2026, 8, 31), monate=12)
ANNAHMEN = jm.CONFIG_DIR / "assumptions.yaml"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, booking_date TEXT,
                                   dedup_hash TEXT, counterparty_norm TEXT,
                                   account_id TEXT DEFAULT 'a', raw_text TEXT);
        CREATE TABLE splits (transaction_id INTEGER, mgmt_category_id TEXT,
                             amount_cents INTEGER);
    """)
    # Zwoelf Monatsbeitraege eines Anbieters, alle im Fenster.
    for n, (jahr, monat) in enumerate(
            [(2025, m) for m in range(9, 13)] + [(2026, m) for m in range(1, 9)],
            start=1):
        c.execute("INSERT INTO transactions (id, booking_date, dedup_hash, "
                  "counterparty_norm) VALUES (?, ?, ?, 'anbieter')",
                  (n, f"{jahr}-{monat:02d}-05", f"b{n}"))
        c.execute("INSERT INTO splits VALUES (?, 'abo/video-streaming', -3000)", (n,))
    return c


def _vertrag(conn, monkeypatch, **extra):
    basis = {"id": "stream", "name": "Streaming", "kategorie": "abo/video-streaming",
             "konto": "a", "betrag_cents": -3000, "takt": 1, "faellig": "2026-10-05",
             "buchungen": ["b12"]}
    basis.update(extra)
    monkeypatch.setattr(abos, "alle_vertraege",
                        lambda: [abos.normalisieren(basis)])


BASIS = {"fixkosten": {"abo/video-streaming": -36000}}


def test_a_running_contract_changes_nothing(conn, monkeypatch):
    _vertrag(conn, monkeypatch)
    assert jm._vertragsenden(conn, FENSTER, [], BASIS) == []


def test_a_cancelled_contract_leaves_the_base(conn, monkeypatch):
    """Was gekuendigt ist, kuerzt den Block, in dem es gemessen wurde."""
    _vertrag(conn, monkeypatch, gekuendigt_zum="2026-10-31")
    [e] = jm._vertragsenden(conn, FENSTER, [], BASIS)
    assert (e["cents"], e["block"], e["gedeckelt"]) == (-36000, "fixkosten", False)
    [post] = jm._vertragsende_posten([e], 2028, 0, 0.0, ANNAHMEN)["fixkosten"]
    assert (post.cents, post.quelle) == (36000, "vertrag")


def test_the_year_of_the_cancellation_counts_only_its_remaining_months(conn,
                                                                      monkeypatch):
    """Bis Oktober lief der Vertrag. Nur November und Dezember sind frei."""
    _vertrag(conn, monkeypatch, gekuendigt_zum="2026-10-31")
    [e] = jm._vertragsenden(conn, FENSTER, [], BASIS)
    [post] = jm._vertragsende_posten([e], 2026, 0, 0.0, ANNAHMEN)["fixkosten"]
    assert post.cents == 6000                      # 36.000 × 2/12


def test_a_cancellation_before_the_elapsed_months_does_not_double(conn, monkeypatch):
    """Im Basisjahr zaehlen nur die Monate, die noch kommen.

    Die gemessenen Monate stecken schon im Anfangsbestand; sie ein zweites Mal
    gutzuschreiben waere derselbe Doppelzaehler, nur mit umgekehrtem Vorzeichen.
    """
    _vertrag(conn, monkeypatch, gekuendigt_zum="2026-03-31")
    [e] = jm._vertragsenden(conn, FENSTER, [], BASIS)
    [post] = jm._vertragsende_posten([e], 2026, 8, 0.0, ANNAHMEN)["fixkosten"]
    assert post.cents == 12000                     # 36.000 × 4/12, nicht 9/12


def test_the_saving_shrinks_as_the_window_moves_past_the_end(conn, monkeypatch):
    """Sobald das Fenster hinter dem Ende liegt, steckt nichts mehr in der
    Basis -- und dann darf auch nichts mehr abgezogen werden."""
    _vertrag(conn, monkeypatch, gekuendigt_zum="2026-08-31")
    spaeter = ag.Fenster(von=date(2026, 9, 1), bis=date(2027, 8, 31), monate=12)
    assert jm._vertragsenden(conn, spaeter, [], BASIS) == []


def test_a_cancellation_never_promises_more_than_the_contract_costs(conn,
                                                                   monkeypatch):
    """Eine angehakte Einmalzahlung -- ein Geraet zum Tarif -- hebt den
    Monatsschnitt weit ueber die Rate. Die Ersparnis saehe doppelt so gross
    aus wie sie ist, und das ist die gefaehrliche Richtung."""
    conn.execute("INSERT INTO transactions (id, booking_date, dedup_hash, "
                 "counterparty_norm) VALUES (99, '2025-10-17', 'geraet', 'anbieter')")
    conn.execute("INSERT INTO splits VALUES (99, 'abo/video-streaming', -60000)")
    _vertrag(conn, monkeypatch, gekuendigt_zum="2026-10-31",
             buchungen=["b12", "geraet"])
    [e] = jm._vertragsenden(conn, FENSTER, [], BASIS)
    assert e["gedeckelt"] and e["jahr"] == 36000
    [post] = jm._vertragsende_posten([e], 2028, 0, 0.0, ANNAHMEN)["fixkosten"]
    assert post.cents == 36000
    assert "auf die Vertragsrate begrenzt" in post.herleitung


def test_a_wegfall_line_for_the_same_series_is_not_counted_twice(conn, monkeypatch):
    """Wer beides angelegt hat -- Kuendigung UND Wegfall-Zeile -- bekommt den
    Abzug einmal. Sonst waere der Komfort eine neue Fehlerquelle."""
    _vertrag(conn, monkeypatch, gekuendigt_zum="2026-10-31")
    klammer = sz.Scenario(id="k", name="K", active=True, lines=[sz.Line(
        label="Streaming entfällt", amount_cents=0, kind="wegfall",
        start=date(2026, 11, 1), buchungen=["b12"])])
    assert jm._vertragsenden(conn, FENSTER, [klammer], BASIS) == []


def test_a_category_that_is_not_projected_saves_nothing(conn, monkeypatch):
    """Unter Prognosebasis abgewaehlt heisst: steht nicht in der Basis. Dann
    gibt es dort auch nichts zu kuerzen."""
    _vertrag(conn, monkeypatch, gekuendigt_zum="2026-10-31")
    assert jm._vertragsenden(conn, FENSTER, [], {"fixkosten": {}}) == []
